"""Native Google Gemini API adapter for Veo long-running generations."""

from __future__ import annotations

import base64
import http.client
import ipaddress
import json
import logging
import socket
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import HTTPSHandler, HTTPRedirectHandler, ProxyHandler, Request, build_opener

from providers.http_safety import safe_urlopen
from providers.credential_parser import parse_api_key, parse_api_url
from providers.image.compressor import ImageTooLargeError, prepare_image_bytes, prepare_local_image

from .rest_client import (
    RestVideoClient,
    TransportResponse,
    VideoProviderConfigurationError,
    VideoProviderRequestError,
)


logger = logging.getLogger(__name__)
Transport = Callable[[str, str, Mapping[str, str], bytes | None, float], Any]
MAX_REMOTE_IMAGE_BYTES = 20 * 1024 * 1024


def _is_public_address(address: str) -> bool:
    candidate = ipaddress.ip_address(address)
    return candidate.is_global


def _resolve_public_https_target(url: str) -> str:
    """Resolve a remote image once and reject every non-public address.

    The returned IP is used for the connection so a DNS response cannot change
    between validation and the outbound request.
    """
    try:
        parsed = urlparse(url)
        host = parsed.hostname
        port = parsed.port
    except ValueError as error:
        raise ValueError("remote image URL is invalid") from error
    if parsed.scheme.lower() != "https" or not host or parsed.username or parsed.password:
        raise ValueError("remote image URL must be an unauthenticated HTTPS URL")
    try:
        records = socket.getaddrinfo(host, port or 443, type=socket.SOCK_STREAM)
    except socket.gaierror as error:
        raise VideoProviderRequestError(f"could not resolve input image host: {error}") from error
    addresses = list(dict.fromkeys(record[4][0] for record in records if record[4]))
    if not addresses or any(not _is_public_address(address) for address in addresses):
        raise ValueError("remote image host must resolve exclusively to public addresses")
    return addresses[0]


class _VerifiedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS connection pinned to an IP already validated as public."""

    def __init__(self, host: str, *, verified_ip: str, **kwargs: Any) -> None:
        super().__init__(host, **kwargs)
        self._verified_ip = verified_ip

    def connect(self) -> None:
        sock = socket.create_connection((self._verified_ip, self.port), self.timeout, self.source_address)
        if self._tunnel_host:
            self.sock = sock
            self._tunnel()
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


class _VerifiedHTTPSHandler(HTTPSHandler):
    def https_open(self, request: Request):  # type: ignore[no-untyped-def]
        verified_ip = _resolve_public_https_target(request.full_url)
        return self.do_open(
            lambda host, **kwargs: _VerifiedHTTPSConnection(host, verified_ip=verified_ip, **kwargs),
            request,
        )


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def _download_public_image(url: str, timeout: float) -> tuple[bytes, str]:
    """Fetch a bounded, public HTTPS image without proxies or redirects."""
    _resolve_public_https_target(url)
    opener = build_opener(ProxyHandler({}), _VerifiedHTTPSHandler(), _NoRedirectHandler())
    request = Request(url, headers={"Accept": "image/*"}, method="GET")
    try:
        with opener.open(request, timeout=min(timeout, 30.0)) as response:
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > MAX_REMOTE_IMAGE_BYTES:
                raise ValueError("remote image exceeds 20 MB limit")
            raw = response.read(MAX_REMOTE_IMAGE_BYTES + 1)
            if len(raw) > MAX_REMOTE_IMAGE_BYTES:
                raise ValueError("remote image exceeds 20 MB limit")
            mime_type = response.headers.get_content_type()
    except (HTTPError, URLError, TimeoutError, socket.timeout, OSError) as error:
        raise VideoProviderRequestError(f"could not download input image: {error}") from error
    if not mime_type.startswith("image/"):
        raise ValueError("remote input URL did not return an image")
    return raw, mime_type


class GoogleVeoClient:
    """Client for Google's ``predictLongRunning`` Veo REST API.

    Google returns an operation name from submission and a signed media URI
    inside the completed operation. This client adapts that contract to the
    engine's submit/query/result interface without requiring a Google SDK.
    """

    provider_name = "veo"
    default_api_url = "https://generativelanguage.googleapis.com/v1beta"
    default_model = "veo-3.1-fast-generate-preview"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        api_url: str | None = None,
        model: str | None = None,
        timeout: float = 60.0,
        transport: Transport | None = None,
        request_logger: logging.Logger | None = None,
    ) -> None:
        from .rest_client import _setting

        configured_key = parse_api_key(api_key or _setting("VEO_API_KEY") or "")
        if not configured_key:
            raise VideoProviderConfigurationError(
                "VEO_API_KEY is required; set it in the environment or project .env"
            )
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")
        self.api_key = configured_key
        self.api_url = parse_api_url(api_url or _setting("VEO_API_URL") or self.default_api_url)
        self.model = (model or _setting("VEO_MODEL") or self.default_model).strip()
        if not self.model:
            raise ValueError("Veo model must be a non-empty string")
        self.timeout = float(timeout)
        self._transport = transport or self._urlopen_transport
        self._logger = request_logger or logger

    @staticmethod
    def _urlopen_transport(
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> TransportResponse:
        request = Request(url, data=body, headers=dict(headers), method=method)
        try:
            with safe_urlopen(request, timeout=timeout) as response:
                status = getattr(response, "status", None) or response.getcode()
                return TransportResponse(int(status), dict(response.headers.items()), response.read())
        except HTTPError as error:
            headers = dict(error.headers.items()) if error.headers else {}
            return TransportResponse(error.code, headers, error.read())

    def _request(self, method: str, url: str, payload: Mapping[str, Any] | None = None) -> Any:
        headers = {
            "Accept": "application/json, video/*, application/octet-stream",
            "x-goog-api-key": self.api_key,
        }
        body = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload).encode("utf-8")
        self._logger.info("%s request %s %s", self.provider_name, method, url)
        try:
            response = self._transport(method, url, headers, body, self.timeout)
        except (HTTPError, URLError, TimeoutError, socket.timeout, OSError) as error:
            raise VideoProviderRequestError(f"Google Veo request failed: {error}") from error
        normalized = RestVideoClient._normalize_response(response)
        if not 200 <= normalized.status < 300:
            detail = normalized.body.decode("utf-8", errors="replace")[:200]
            raise VideoProviderRequestError(
                f"Google Veo request failed with HTTP {normalized.status}: {detail}"
            )
        return RestVideoClient._decode_body(normalized.body, normalized.headers)

    def test_connection(self) -> Any:
        return self._request("GET", f"{self.api_url}/models/{quote(self.model, safe='-._')}")

    def list_models(self) -> Any:
        return self._request("GET", f"{self.api_url}/models")

    def submit_generation(self, prompt: str, **options: Any) -> Mapping[str, Any]:
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt must be a non-empty string")
        image = options.pop("image", None)
        instance: dict[str, Any] = {"prompt": prompt.strip()}
        if image is not None:
            instance["image"] = {"inlineData": self._inline_image(image)}
        payload: dict[str, Any] = {"instances": [instance]}
        parameters = options.pop("parameters", None)
        if parameters is not None and not isinstance(parameters, Mapping):
            raise ValueError("parameters must be a mapping")
        merged_parameters = dict(parameters or {})
        merged_parameters.update(options)
        if merged_parameters:
            payload["parameters"] = merged_parameters
        endpoint = f"{self.api_url}/models/{quote(self.model, safe='-._')}:predictLongRunning"
        response = self._request("POST", endpoint, payload)
        if not isinstance(response, Mapping) or not response.get("name"):
            raise VideoProviderRequestError("Google Veo submission did not return an operation name")
        return response

    def query_task(self, task_id: str) -> Mapping[str, Any]:
        operation = self._get_operation(task_id)
        if operation.get("error"):
            error = operation["error"]
            detail = error.get("message") if isinstance(error, Mapping) else str(error)
            return {"status": "failed", "error": detail or "operation failed", "operation": operation}
        return {
            "status": "completed" if operation.get("done") is True else "pending",
            "operation": operation,
        }

    def get_result(self, task_id: str) -> Mapping[str, Any]:
        operation = self._get_operation(task_id)
        if operation.get("error"):
            raise VideoProviderRequestError("Google Veo operation failed")
        if operation.get("done") is not True:
            raise VideoProviderRequestError("Google Veo operation is not complete")
        return operation

    def download_result(self, url: str) -> bytes:
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            raise ValueError("result URL must use http:// or https://")
        try:
            response = self._transport(
                "GET",
                url,
                {"Accept": "video/*, application/octet-stream"},
                None,
                self.timeout,
            )
        except (HTTPError, URLError, TimeoutError, socket.timeout, OSError) as error:
            raise VideoProviderRequestError(f"Google Veo result download failed: {error}") from error
        normalized = RestVideoClient._normalize_response(response)
        if not 200 <= normalized.status < 300:
            raise VideoProviderRequestError(
                f"Google Veo result download failed with HTTP {normalized.status}"
            )
        return normalized.body

    def _get_operation(self, task_id: str) -> Mapping[str, Any]:
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError("task_id must be a non-empty string")
        operation = task_id.strip()
        if operation.startswith(("http://", "https://")):
            raise ValueError("operation name must be a relative Google operation name")
        response = self._request("GET", f"{self.api_url}/{operation.lstrip('/')}")
        if not isinstance(response, Mapping):
            raise VideoProviderRequestError("Google Veo operation response must be a JSON object")
        return response

    @staticmethod
    def _inline_image(image: str) -> Mapping[str, str]:
        if not isinstance(image, str) or not image.strip():
            raise ValueError("image must be a non-empty path, URL, or data URI")
        source = image.strip()
        try:
            if source.startswith("data:"):
                header, separator, encoded = source.partition(",")
                if not separator or ";base64" not in header:
                    raise ValueError("image data URI must contain base64 data")
                mime_type = header[5:].split(";", 1)[0] or "application/octet-stream"
                try:
                    raw = base64.b64decode(encoded, validate=True)
                except (ValueError, TypeError) as error:
                    raise ValueError("image data URI is not valid base64") from error
                prepared = prepare_image_bytes(raw, source_name="inline", mime=mime_type)
            elif source.startswith(("http://", "https://")):
                raw, mime_type = _download_public_image(source, timeout=30.0)
                name = Path(urlparse(source).path).name or "remote.png"
                prepared = prepare_image_bytes(raw, source_name=name, mime=mime_type)
            else:
                path = Path(source)
                try:
                    prepared = prepare_local_image(str(path))
                except OSError as error:
                    raise VideoProviderRequestError(f"could not read input image {path}: {error}") from error
        except ImageTooLargeError as error:
            raise VideoProviderRequestError(str(error)) from error
        return {"mimeType": prepared.mime, "data": base64.b64encode(prepared.data).decode("ascii")}


def create_client(**kwargs: Any) -> GoogleVeoClient:
    return GoogleVeoClient(**kwargs)
