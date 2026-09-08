"""Small stdlib client for the Liblib generation API."""

from __future__ import annotations

import json
import logging
import os
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request

from providers.http_safety import safe_urlopen
from providers.credential_parser import parse_api_key, parse_api_url



DEFAULT_API_URL = "https://openapi.liblib.art/api"
logger = logging.getLogger(__name__)


class LiblibClientError(RuntimeError):
    """Base error for Liblib client failures."""


class LiblibConfigurationError(LiblibClientError):
    """Raised when required client configuration is missing or invalid."""


class LiblibRequestError(LiblibClientError):
    """Raised when a network request or HTTP response fails."""


class LiblibResponseError(LiblibClientError):
    """Raised when an injected transport returns an invalid response shape."""


@dataclass(frozen=True)
class TransportResponse:
    """Response shape accepted from an injected transport."""

    status: int
    headers: Mapping[str, str]
    body: bytes


Transport = Callable[
    [str, str, Mapping[str, str], bytes | None, float], Any
]


def _dotenv_value(name: str) -> str | None:
    """Read one value from the project .env without modifying the process env."""
    env_path = Path(__file__).resolve().parents[2] / ".env"
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None

    for line in lines:
        entry = line.strip()
        if not entry or entry.startswith("#"):
            continue
        if entry.startswith("export "):
            entry = entry[7:].lstrip()
        key, separator, value = entry.partition("=")
        if not separator or key.strip() != name:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        return value
    return None


class LiblibClient:
    """HTTP client with injectable transport for deterministic tests.

    The default endpoint is ``https://openapi.liblib.art/api``. It can be
    overridden with ``LIBLIB_API_URL`` or the ``api_url`` argument. The
    transport callable receives ``(method, url, headers, body, timeout)`` and
    returns a :class:`TransportResponse` (a three-item tuple is also accepted).
    """

    def __init__(
        self,
        api_key: str | None = None,
        api_url: str | None = None,
        model: str | None = None,
        timeout: float = 30.0,
        transport: Transport | None = None,
        request_logger: logging.Logger | None = None,
    ) -> None:
        configured_key = parse_api_key(api_key or os.environ.get("LIBLIB_API_KEY") or "")
        if not configured_key:
            configured_key = parse_api_key(_dotenv_value("LIBLIB_API_KEY") or "")
        if not configured_key:
            raise LiblibConfigurationError(
                "LIBLIB_API_KEY is required; set it in the environment or project .env"
            )

        configured_url = api_url or os.environ.get("LIBLIB_API_URL") or DEFAULT_API_URL
        if not isinstance(configured_url, str) or not configured_url.strip():
            raise LiblibConfigurationError("LIBLIB_API_URL must be a non-empty URL")
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")

        self.api_key = configured_key
        self.api_url = parse_api_url(configured_url)
        configured_model = model if model is not None else (
            os.environ.get("LIBLIB_MODEL") or _dotenv_value("LIBLIB_MODEL") or ""
        )
        self.model = configured_model.strip() if isinstance(configured_model, str) else ""
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
                status = getattr(response, "status", None)
                if status is None:
                    status = response.getcode()
                return TransportResponse(
                    int(status),
                    dict(response.headers.items()),
                    response.read(),
                )
        except HTTPError as error:
            headers = dict(error.headers.items()) if error.headers else {}
            return TransportResponse(error.code, headers, error.read())

    def _request_http(
        self,
        method: str,
        url: str,
        payload: Mapping[str, Any] | None = None,
        authenticated: bool = True,
    ) -> Any:
        headers = {
            "Accept": "application/json, image/*, video/*, application/octet-stream",
        }
        if authenticated:
            headers["Authorization"] = f"Bearer {self.api_key}"
        body: bytes | None = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            try:
                body = json.dumps(payload).encode("utf-8")
            except (TypeError, ValueError) as error:
                raise LiblibRequestError("request payload is not JSON serializable") from error

        self._logger.info("Liblib request %s %s", method, url)
        try:
            if callable(self._transport):
                response = self._transport(method, url, headers, body, self.timeout)
            elif hasattr(self._transport, "request") and callable(self._transport.request):
                response = self._transport.request(method, url, headers, body, self.timeout)
            else:
                raise LiblibResponseError("transport must be callable or expose request")
        except (HTTPError, URLError, TimeoutError, socket.timeout, OSError) as error:
            if isinstance(error, (TimeoutError, socket.timeout)):
                message = "Liblib request timed out"
            else:
                message = f"Liblib request failed: {error}"
            self._logger.error(message)
            raise LiblibRequestError(message) from error

        normalized = self._normalize_response(response)
        if not 200 <= normalized.status < 300:
            detail = self._response_detail(normalized.body)
            message = f"Liblib request failed with HTTP {normalized.status}"
            if detail:
                message += f": {detail}"
            self._logger.error(message)
            raise LiblibRequestError(message)
        return self._decode_body(normalized.body, normalized.headers)

    @staticmethod
    def _normalize_response(response: Any) -> TransportResponse:
        if isinstance(response, TransportResponse):
            return response
        if isinstance(response, tuple) and len(response) == 3:
            status, headers, body = response
            return TransportResponse(int(status), dict(headers or {}), _as_bytes(body))
        if isinstance(response, tuple) and len(response) == 2:
            status, body = response
            return TransportResponse(int(status), {}, _as_bytes(body))
        if hasattr(response, "read") and (hasattr(response, "status") or hasattr(response, "code")):
            status = getattr(response, "status", None)
            if status is None:
                status = response.code
            headers = getattr(response, "headers", {})
            if hasattr(headers, "items"):
                headers = dict(headers.items())
            return TransportResponse(int(status), headers, _as_bytes(response.read()))
        if isinstance(response, (bytes, bytearray, memoryview, str, dict, list)):
            return TransportResponse(200, {}, _as_bytes(response))
        raise LiblibResponseError(
            "transport must return TransportResponse, a response tuple, or an HTTP-like object"
        )

    @staticmethod
    def _decode_body(body: bytes, headers: Mapping[str, str]) -> Any:
        if not body:
            return {}
        content_type = next(
            (str(value).lower() for key, value in headers.items() if key.lower() == "content-type"),
            "",
        )
        if content_type.startswith(("image/", "video/", "application/octet-stream")):
            return body
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return body

    @staticmethod
    def _response_detail(body: bytes) -> str:
        if not body:
            return ""
        try:
            decoded = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return body.decode("utf-8", errors="replace")[:200]
        if isinstance(decoded, Mapping):
            return str(decoded.get("message") or decoded.get("error") or decoded)[:200]
        return str(decoded)[:200]

    @staticmethod
    def _task_path(task_id: str) -> str:
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError("task_id must be a non-empty string")
        return quote(task_id.strip(), safe="")

    def test_connection(self) -> Any:
        return self._request_http("GET", self.api_url)

    def list_models(self) -> Any:
        """Return the provider's model catalog when its gateway supports it."""
        return self._request_http("GET", f"{self.api_url}/models")

    def submit_generation(self, prompt: str, **options: Any) -> Any:
        """Submit a generation request and return the decoded API response."""
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt must be a non-empty string")
        payload: dict[str, Any] = {"prompt": prompt}
        if self.model:
            payload["model"] = self.model
        payload.update(options)
        return self._request_http("POST", f"{self.api_url}/generate", payload)

    def query_task(self, task_id: str) -> Any:
        """Return the current task status."""
        path = self._task_path(task_id)
        return self._request_http("GET", f"{self.api_url}/generate/{path}")

    def get_result(self, task_id: str) -> Any:
        """Return the decoded result metadata or raw result bytes."""
        path = self._task_path(task_id)
        return self._request_http("GET", f"{self.api_url}/generate/{path}/result")

    def download_result(self, url: str) -> bytes:
        """Download a result URL without forwarding the Liblib API credential."""
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            raise ValueError("result URL must use http:// or https://")
        result = self._request_http("GET", url, authenticated=False)
        if not isinstance(result, (bytes, bytearray, memoryview)):
            raise LiblibResponseError("result URL did not return bytes")
        return bytes(result)


def _as_bytes(value: Any) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, (bytearray, memoryview)):
        return bytes(value)
    if isinstance(value, str):
        return value.encode("utf-8")
    if isinstance(value, (dict, list)):
        return json.dumps(value).encode("utf-8")
    if value is None:
        return b""
    raise LiblibResponseError("transport response body must be bytes, text, or JSON data")
