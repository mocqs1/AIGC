"""Configurable REST video provider client.

The two supported video vendors expose task-style APIs, but deployments may
use different gateways and endpoint paths. This adapter keeps those details in
environment configuration while presenting the engine's common client
contract.
"""

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


logger = logging.getLogger(__name__)


class VideoProviderError(RuntimeError):
    """Base error for configurable video provider clients."""


class VideoProviderConfigurationError(VideoProviderError):
    """Raised when a provider key or endpoint is missing."""


class VideoProviderRequestError(VideoProviderError):
    """Raised for transport and non-success HTTP responses."""

    def __init__(self, message: str, *, status: int | None = None, response_detail: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.response_detail = response_detail


@dataclass(frozen=True)
class TransportResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


Transport = Callable[[str, str, Mapping[str, str], bytes | None, float], Any]


def _dotenv_value(name: str) -> str | None:
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
        if separator and key.strip() == name:
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            return value or None
    return None


def _setting(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value and value.strip():
        return value.strip()
    return _dotenv_value(name) or default


def _join_url(base: str, path: str) -> str:
    return f"{base.rstrip('/')}/{path.lstrip('/')}"


def _format_path(path: str, task_id: str) -> str:
    return path.replace("{task_id}", quote(task_id, safe=""))


class RestVideoClient:
    """Common task API adapter used by Veo and Seedance providers.

    The default request is JSON with ``prompt`` and optional ``image``. A
    caller can inject a transport in tests, so no real API is contacted by the
    test suite.
    """

    provider_name = "video"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        api_url: str | None = None,
        submit_path: str | None = None,
        status_path: str | None = None,
        result_path: str | None = None,
        auth_header: str | None = None,
        auth_scheme: str | None = None,
        timeout: float = 60.0,
        transport: Transport | None = None,
        request_logger: logging.Logger | None = None,
    ) -> None:
        prefix = self.provider_name.upper()
        configured_key = api_key or _setting(f"{prefix}_API_KEY")
        if not configured_key:
            raise VideoProviderConfigurationError(
                f"{prefix}_API_KEY is required; set it in the environment or project .env"
            )
        configured_url = api_url or _setting(f"{prefix}_API_URL")
        if not configured_url:
            raise VideoProviderConfigurationError(
                f"{prefix}_API_URL is required; set the provider gateway endpoint"
            )
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")
        self.api_key = configured_key.strip()
        self.api_url = configured_url.strip().rstrip("/")
        self.submit_path = submit_path or _setting(f"{prefix}_SUBMIT_PATH", "/v1/videos")
        self.status_path = status_path or _setting(
            f"{prefix}_STATUS_PATH", "/v1/videos/{task_id}"
        )
        self.result_path = result_path or _setting(
            f"{prefix}_RESULT_PATH", "/v1/videos/{task_id}/result"
        )
        self.auth_header = auth_header or _setting(f"{prefix}_AUTH_HEADER", "Authorization")
        self.auth_scheme = auth_scheme if auth_scheme is not None else _setting(f"{prefix}_AUTH_SCHEME", "Bearer")
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
        }
        credential = f"{self.auth_scheme.strip()} {self.api_key}" if self.auth_scheme and self.auth_scheme.strip() else self.api_key
        headers[self.auth_header or "Authorization"] = credential
        body = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload).encode("utf-8")
        self._logger.info("%s request method=%s url=%s timeout=%ss body_bytes=%s", self.provider_name, method, url, self.timeout, len(body) if body else 0)
        try:
            response = self._transport(method, url, headers, body, self.timeout)
        except (HTTPError, URLError, TimeoutError, socket.timeout, OSError) as error:
            self._logger.error("%s transport failed method=%s url=%s timeout=%ss error_type=%s", self.provider_name, method, url, self.timeout, type(error).__name__)
            raise VideoProviderRequestError(f"{self.provider_name} request failed: {type(error).__name__}") from error
        normalized = self._normalize_response(response)
        if not 200 <= normalized.status < 300:
            detail = normalized.body.decode("utf-8", errors="replace")[:500]
            self._logger.error("%s rejected method=%s url=%s status=%s detail=%s", self.provider_name, method, url, normalized.status, detail)
            raise VideoProviderRequestError(
                f"{self.provider_name} request failed with HTTP {normalized.status}: {detail}",
                status=normalized.status,
                response_detail=detail,
            )
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
        if isinstance(response, (bytes, bytearray, memoryview, str, dict, list)):
            return TransportResponse(200, {}, _as_bytes(response))
        raise VideoProviderRequestError("transport returned an unsupported response")

    @staticmethod
    def _decode_body(body: bytes, headers: Mapping[str, str]) -> Any:
        if not body:
            return {}
        content_type = next((str(v).lower() for k, v in headers.items() if k.lower() == "content-type"), "")
        if content_type.startswith(("video/", "application/octet-stream")):
            return body
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return body

    def test_connection(self) -> Any:
        return self._request("GET", self.api_url)

    def list_models(self) -> Any:
        return self._request("GET", _join_url(self.api_url, "/models"))

    def submit_generation(self, prompt: str, **options: Any) -> Any:
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt must be a non-empty string")
        payload: dict[str, Any] = {"prompt": prompt}
        payload.update(options)
        return self._request("POST", _join_url(self.api_url, self.submit_path or ""), payload)

    def query_task(self, task_id: str) -> Any:
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError("task_id must be a non-empty string")
        return self._request("GET", _join_url(self.api_url, _format_path(self.status_path or "", task_id)))

    def get_result(self, task_id: str) -> Any:
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError("task_id must be a non-empty string")
        return self._request("GET", _join_url(self.api_url, _format_path(self.result_path or "", task_id)))

    def download_result(self, url: str) -> bytes:
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            raise ValueError("result URL must use http:// or https://")
        try:
            response = self._transport(
                "GET", url, {"Accept": "video/*, application/octet-stream"}, None, self.timeout
            )
        except (HTTPError, URLError, TimeoutError, socket.timeout, OSError) as error:
            raise VideoProviderRequestError(f"{self.provider_name} result download failed: {error}") from error
        normalized = self._normalize_response(response)
        if not 200 <= normalized.status < 300:
            raise VideoProviderRequestError(f"{self.provider_name} result download failed with HTTP {normalized.status}")
        return normalized.body


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
    raise VideoProviderRequestError("transport response body must be bytes or JSON")
