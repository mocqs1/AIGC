"""Configurable Hermes/OpenAI-compatible image task client.

The client intentionally uses the standard library so the local workbench can
talk to Hermes deployments without coupling itself to one SDK version.
"""

from __future__ import annotations

import base64
import json
import logging
import math
import mimetypes
import os
import re
import socket
import time
import uuid
from pathlib import Path
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request

from providers.http_safety import safe_urlopen
from providers.credential_parser import parse_api_key, parse_api_url



# Ark's Seedream image endpoint uses one ordered ``image`` input instead of
# the OpenAI-compatible ``image_url``/``reference_image_urls`` pair.  The
# service accepts at most ten images for a multi-image request.
_SEEDREAM_MAX_IMAGES = 10
_SEEDREAM_SIZE_BY_ASPECT = {
    "square": "2048x2048",
    "portrait": "1152x2048",
    "landscape": "2048x1152",
}
_IMAGE_MIME_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


class HermesClientError(RuntimeError):
    """Base Hermes client error."""


class HermesConfigurationError(HermesClientError):
    """Raised when Hermes settings are incomplete."""


class HermesRequestError(HermesClientError):
    """Raised for transport and non-success responses."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        provider_code: str | None = None,
        provider_message: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.provider_code = provider_code
        self.provider_message = provider_message


class HermesClient:
    """Submit, poll, and download image jobs from a Hermes-compatible API.

    Hermes deployments vary between synchronous image responses and task APIs.
    The adapter accepts both: a direct image/data URL completes immediately;
    ``task_id`` responses are polled using the configured paths.
    """
    handles_request_retries = True

    def __init__(
        self,
        *,
        api_url: str,
        api_key: str,
        model: str = "gpt-image-2",
        submit_path: str = "/images/generations",
        edit_path: str = "/images/edits",
        status_path: str = "/images/generations/{task_id}",
        result_path: str = "/images/generations/{task_id}",
        timeout: float = 480.0,
        query_timeout: float = 30.0,
        result_timeout: float = 120.0,
        download_timeout: float = 120.0,
        transport=None,
        request_logger: logging.Logger | None = None,
    ) -> None:
        if not api_url or not str(api_url).strip():
            raise HermesConfigurationError("Hermes API 地址不能为空")
        parsed_key = parse_api_key(api_key)
        if not parsed_key:
            raise HermesConfigurationError("Hermes API key is not configured")
        try:
            timeout_value = float(timeout)
        except (TypeError, ValueError) as error:
            raise ValueError("timeout must be a finite number") from error
        if not math.isfinite(timeout_value) or timeout_value <= 0:
            raise ValueError("timeout must be a finite number greater than zero")
        self.api_url = parse_api_url(api_url)
        self.api_key = parsed_key
        self.model = (model or "gpt-image-2").strip()
        self.submit_path = submit_path or "/images/generations"
        self.edit_path = edit_path or "/images/edits"
        self.status_path = status_path or "/images/generations/{task_id}"
        self.result_path = result_path or "/images/generations/{task_id}"
        self.timeout = timeout_value
        self.query_timeout = self._validate_timeout(query_timeout, "query_timeout")
        self.result_timeout = self._validate_timeout(result_timeout, "result_timeout")
        self.download_timeout = self._validate_timeout(download_timeout, "download_timeout")
        self._transport = transport or self._urlopen_transport
        self._logger = request_logger or logging.getLogger(__name__)

    @staticmethod
    def _validate_timeout(value: float, name: str) -> float:
        try:
            result = float(value)
        except (TypeError, ValueError) as error:
            raise ValueError(f"{name} must be a finite number") from error
        if not math.isfinite(result) or result <= 0:
            raise ValueError(f"{name} must be a finite number greater than zero")
        return result

    def _timeout_for_stage(self, stage: str) -> float:
        if stage in {"submit_edit", "submit_generation"}:
            # image2 edit requests commonly spend several minutes in the
            # upstream request before returning a task or image. Keep the
            # configured timeout as the lower bound while avoiding the old
            # 180-second false timeout for outfit swaps.
            return max(self.timeout, 300.0)
        return {
            "query_task": self.query_timeout,
            "get_result": self.result_timeout,
            "download_result": self.download_timeout,
        }.get(stage, self.timeout)

    def _uses_seedream_schema(self) -> bool:
        """Return whether this endpoint is Ark/Seedream's image API."""
        hostname = (urlparse(self.api_url).hostname or "").lower().rstrip(".")
        # Model matching also supports an Ark-compatible reverse proxy whose
        # hostname is not ``ark.cn-beijing.volces.com``.
        return hostname == "ark.cn-beijing.volces.com" or "seedream" in self.model.lower()

    def _uses_gpt_image_edit_schema(self) -> bool:
        """Return whether references must use OpenAI's multipart edit API."""
        return "gpt-image-2" in self.model.lower()

    @classmethod
    def _image_input(cls, value: str) -> str:
        return value if value.startswith(("http://", "https://", "data:")) else cls._encode_local(value)

    @staticmethod
    def _urlopen_transport(method: str, url: str, headers: Mapping[str, str], body: bytes | None, timeout: float):
        request_headers = dict(headers)
        authorization = request_headers.pop("Authorization", None)
        request = Request(url, data=body, headers=request_headers, method=method)
        # urllib only copies regular headers when following a redirect.
        if authorization:
            request.add_unredirected_header("Authorization", authorization)
        try:
            with safe_urlopen(request, timeout=timeout) as response:
                return int(getattr(response, "status", None) or response.getcode()), dict(response.headers.items()), response.read()
        except HTTPError as error:
            return error.code, dict(error.headers.items()) if error.headers else {}, error.read()

    @staticmethod
    def _safe_path(value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme and parsed.netloc:
            return parsed.path or "/"
        return value.split("?", 1)[0]

    def _request(
        self,
        method: str,
        path: str,
        payload: Mapping[str, Any] | None = None,
        *,
        body: bytes | None = None,
        content_type: str | None = None,
        stage: str = "request",
    ) -> Any:
        url = path if path.startswith(("http://", "https://")) else f"{self.api_url}/{path.lstrip('/')}"
        request_path = self._safe_path(path)
        # Result URLs can be hosted by a separate object store. Never forward
        # the Hermes credential outside the configured API origin.
        configured_origin = urlparse(self.api_url).netloc.lower()
        request_origin = urlparse(url).netloc.lower()
        headers = {"Accept": "application/json, image/*, application/octet-stream"}
        if request_origin == configured_origin:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request_body = body
        if payload is not None:
            if request_body is not None:
                raise ValueError("payload and body are mutually exclusive")
            headers["Content-Type"] = "application/json"
            request_body = json.dumps(payload).encode("utf-8")
        elif content_type:
            headers["Content-Type"] = content_type
        request_timeout = self._timeout_for_stage(stage)
        started = time.monotonic()
        self._logger.info(
            "Hermes request start stage=%s method=%s path=%s timeout=%ss body_bytes=%s",
            stage,
            method,
            request_path,
            request_timeout,
            len(request_body) if request_body is not None else 0,
        )
        response = None
        attempts = 3 if method.upper() == "GET" and stage in {"query_task", "get_result", "download_result"} else 1
        for attempt in range(attempts):
            try:
                response = self._transport(method, url, headers, request_body, request_timeout)
                break
            except (HTTPError, URLError, TimeoutError, socket.timeout, OSError) as error:
                if attempt + 1 >= attempts:
                    elapsed_ms = int((time.monotonic() - started) * 1000)
                    self._logger.warning(
                        "Hermes request failed stage=%s method=%s path=%s elapsed_ms=%s attempts=%s error_type=%s",
                        stage,
                        method,
                        request_path,
                        elapsed_ms,
                        attempt + 1,
                        type(error).__name__,
                    )
                    raise HermesRequestError(f"Hermes request failed during {stage}: {type(error).__name__}") from error
                delay = min(10.0, 2.0 ** attempt)
                self._logger.warning(
                    "Hermes request retry stage=%s method=%s path=%s attempt=%s delay=%ss error_type=%s",
                    stage,
                    method,
                    request_path,
                    attempt + 1,
                    delay,
                    type(error).__name__,
                )
                time.sleep(delay)
        if response is None:
            raise HermesRequestError(f"Hermes request failed during {stage}")
        if isinstance(response, tuple) and len(response) == 3:
            response_status = int(response[0])
            if response_status in {429, 502, 503, 504} and attempt + 1 < attempts:
                delay = min(10.0, 2.0 ** attempt)
                self._logger.warning(
                    "Hermes request retry stage=%s method=%s path=%s attempt=%s delay=%ss http_status=%s",
                    stage,
                    method,
                    request_path,
                    attempt + 1,
                    delay,
                    response_status,
                )
                time.sleep(delay)
                try:
                    response = self._transport(method, url, headers, request_body, request_timeout)
                except (HTTPError, URLError, TimeoutError, socket.timeout, OSError) as error:
                    raise HermesRequestError(f"Hermes request failed during {stage}: {type(error).__name__}") from error
        if isinstance(response, tuple) and len(response) == 3:
            status, response_headers, raw = response
        else:
            raise HermesRequestError("Hermes transport 杩斿洖鏍煎紡鏃犳晥")
        elapsed_ms = int((time.monotonic() - started) * 1000)
        self._logger.info(
            "Hermes request complete stage=%s method=%s path=%s status=%s elapsed_ms=%s",
            stage,
            method,
            request_path,
            status,
            elapsed_ms,
        )
        if not 200 <= int(status) < 300:
            provider_code, provider_message = self._safe_error_fields(raw)
            # Keep the upstream rejection diagnosable without ever logging
            # request bodies, image data URLs, credentials, or local paths.
            self._logger.warning(
                "Hermes provider rejected request stage=%s method=%s path=%s status=%s code=%s message=%s",
                stage,
                method,
                request_path,
                int(status),
                provider_code or "unknown",
                provider_message or "<empty>",
            )
            detail = ": ".join(part for part in (provider_code, provider_message) if part)
            raise HermesRequestError(
                f"Hermes request failed (HTTP {status}){': ' + detail if detail else ''}",
                status=int(status),
                provider_code=provider_code,
                provider_message=provider_message,
            )
        content_type = next((str(value).lower() for key, value in response_headers.items() if key.lower() == "content-type"), "")
        if content_type.startswith(("image/", "application/octet-stream")):
            return raw
        try:
            return json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            return raw

    @staticmethod
    def _sanitize_diagnostic(value: Any, *, limit: int = 240) -> str:
        """Return short diagnostic text safe for server logs and exceptions."""
        text = str(value).replace("\r", " ").replace("\n", " ").strip()
        text = "".join(character if character in "\t" or ord(character) >= 32 else " " for character in text)
        text = re.sub(r"(?i)bearer\s+[^\s,;]+", "Bearer <redacted>", text)
        text = re.sub(r"(?i)\b(?:sk|dpapi|fernet)[:_-][A-Za-z0-9+/=_-]+", "<redacted>", text)
        text = re.sub(r"(?i)data:image/[^;\s]+;base64,[A-Za-z0-9+/=]+", "data:image/<redacted>", text)
        text = re.sub(r"https?://[^\s'\"},]+", "<url>", text)
        text = re.sub(r"(?i)(?:[A-Za-z]:[\\/]|/)[^\s'\"},]+", "<path>", text)
        return text[:limit]

    @classmethod
    def _safe_error_fields(cls, raw: bytes) -> tuple[str, str]:
        """Extract an upstream code/message pair without retaining raw JSON."""
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return "", ""
        if not isinstance(value, Mapping):
            return "", cls._sanitize_diagnostic(value)
        error = value.get("error")
        nested = error if isinstance(error, Mapping) else {}
        code = value.get("code") or value.get("error_code") or nested.get("code") or nested.get("type")
        message = (
            value.get("message")
            or value.get("detail")
            or value.get("msg")
            or value.get("error_message")
            or nested.get("message")
            or nested.get("detail")
            or nested.get("msg")
        )
        if not message and isinstance(error, str):
            message = error
        safe_code = cls._sanitize_diagnostic(code, limit=100) if code else ""
        safe_message = cls._sanitize_diagnostic(message) if message else ""
        return safe_code, safe_message

    @classmethod
    def _safe_detail(cls, raw: bytes) -> str:
        code, message = cls._safe_error_fields(raw)
        return ": ".join(part for part in (code, message) if part)

    @staticmethod
    def _encode_local(path: str) -> str:
        artifact = Path(path)
        mime = mimetypes.guess_type(artifact.name)[0] or _IMAGE_MIME_TYPES.get(artifact.suffix.lower(), "application/octet-stream")
        return f"data:{mime};base64,{base64.b64encode(artifact.read_bytes()).decode('ascii')}"

    @classmethod
    def _image_bytes(cls, value: str) -> tuple[bytes, str, str]:
        """Load an edit source and return bytes, filename, and MIME type."""
        candidate = value.strip()
        if candidate.lower().startswith("data:"):
            header, separator, encoded = candidate.partition(",")
            if not separator:
                raise HermesRequestError("image data URL is malformed")
            mime = header[5:].split(";", 1)[0].lower() or "image/png"
            try:
                data = base64.b64decode(encoded, validate=True)
            except (ValueError, TypeError) as error:
                raise HermesRequestError("image data URL is not valid base64") from error
            extension = mimetypes.guess_extension(mime) or ".png"
            return data, f"reference-{uuid.uuid4().hex}{extension}", mime
        if candidate.lower().startswith(("http://", "https://")):
            request = Request(candidate, headers={"Accept": "image/*, application/octet-stream"}, method="GET")
            try:
                with safe_urlopen(request, timeout=60.0) as response:
                    data = response.read()
                    response_type = str(response.headers.get("Content-Type", "")).split(";", 1)[0].lower()
            except (HTTPError, URLError, TimeoutError, socket.timeout, OSError, ValueError) as error:
                raise HermesRequestError(f"could not download edit source image: {error}") from error
            parsed = urlparse(candidate)
            suffix = Path(parsed.path).suffix.lower()
            mime = response_type if response_type.startswith("image/") else _IMAGE_MIME_TYPES.get(suffix, "image/png")
            extension = mimetypes.guess_extension(mime) or suffix or ".png"
            return data, f"reference-{uuid.uuid4().hex}{extension}", mime
        artifact = Path(candidate)
        try:
            data = artifact.read_bytes()
        except OSError as error:
            raise HermesRequestError(f"could not read edit source image: {error}") from error
        mime = mimetypes.guess_type(artifact.name)[0] or _IMAGE_MIME_TYPES.get(artifact.suffix.lower(), "image/png")
        extension = mimetypes.guess_extension(mime) or artifact.suffix.lower() or ".png"
        return data, os.path.basename(artifact.name) or f"reference-{uuid.uuid4().hex}{extension}", mime

    @classmethod
    def _multipart_edit_body(
        cls,
        prompt: str,
        model: str,
        aspect_ratio: str,
        sources: list[str],
    ) -> tuple[bytes, str]:
        """Encode the OpenAI images.edit contract without a third-party SDK."""
        size = {
            "landscape": "1536x1024",
            "square": "1024x1024",
            "portrait": "1024x1536",
        }.get(aspect_ratio, "1024x1024")
        boundary = f"----AIGC-Hermes-{uuid.uuid4().hex}"
        chunks: list[bytes] = []

        def field(name: str, value: str) -> None:
            chunks.extend(
                [
                    f"--{boundary}\r\n".encode(),
                    f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                    str(value).encode("utf-8"),
                    b"\r\n",
                ]
            )

        field("model", model)
        field("prompt", prompt)
        field("size", size)
        field("quality", "high")
        field("n", "1")
        image_field = "image" if len(sources) == 1 else "image[]"
        for source in sources:
            data, filename, mime = cls._image_bytes(source)
            chunks.extend(
                [
                    f"--{boundary}\r\n".encode(),
                    f'Content-Disposition: form-data; name="{image_field}"; filename="{filename}"\r\n'.encode(),
                    f"Content-Type: {mime}\r\n\r\n".encode(),
                    data,
                    b"\r\n",
                ]
            )
        chunks.append(f"--{boundary}--\r\n".encode())
        return b"".join(chunks), f"multipart/form-data; boundary={boundary}"

    def submit_generation(self, prompt: str, *, image: str | None = None, references: list[str] | None = None, aspect_ratio: str = "square") -> Any:
        if self._uses_seedream_schema():
            ordered_images = []
            if image:
                ordered_images.append(self._image_input(image))
            if references:
                ordered_images.extend(self._image_input(item) for item in references)
            if len(ordered_images) > _SEEDREAM_MAX_IMAGES:
                raise HermesRequestError(
                    f"Seedream supports at most {_SEEDREAM_MAX_IMAGES} ordered reference images"
                )
            # These are the documented Ark fields.  ``image`` is deliberately
            # a list when there are multiple references so image 1 remains the
            # model and later entries remain garment references.
            payload = {
                "model": self.model,
                "prompt": prompt,
                "size": _SEEDREAM_SIZE_BY_ASPECT.get(aspect_ratio, "2K"),
                "response_format": "b64_json",
                "watermark": False,
            }
            if ordered_images:
                payload["image"] = ordered_images[0] if len(ordered_images) == 1 else ordered_images
        else:
            ordered_sources = []
            if image:
                ordered_sources.append(image)
            if references:
                ordered_sources.extend(references)
            if ordered_sources and self._uses_gpt_image_edit_schema():
                if self.edit_path.rstrip("/") == self.submit_path.rstrip("/"):
                    raise HermesConfigurationError(
                        "gpt-image-2 reference requests require a distinct /images/edits path"
                    )
                body, content_type = self._multipart_edit_body(
                    prompt,
                    self.model,
                    aspect_ratio,
                    ordered_sources,
                )
                return self._request(
                    "POST",
                    self.edit_path,
                    body=body,
                    content_type=content_type,
                    stage="submit_edit",
                )
            payload = {"model": self.model, "prompt": prompt, "aspect_ratio": aspect_ratio}
            if image:
                payload["image_url"] = self._image_input(image)
            if references:
                payload["reference_image_urls"] = [self._image_input(item) for item in references]
        return self._request("POST", self.submit_path, payload, stage="submit_generation")

    def query_task(self, task_id: str) -> Any:
        return self._request("GET", self.status_path.replace("{task_id}", quote(task_id, safe="")), stage="query_task")

    def get_result(self, task_id: str) -> Any:
        return self._request("GET", self.result_path.replace("{task_id}", quote(task_id, safe="")), stage="get_result")

    def test_connection(self) -> dict[str, Any]:
        return self._request("GET", "/models", stage="test_connection")

    def list_models(self) -> dict[str, Any]:
        return self._request("GET", "/models", stage="list_models")

    def download_result(self, url: str) -> bytes:
        result = self._request("GET", url, stage="download_result")
        if isinstance(result, (bytes, bytearray, memoryview)):
            return bytes(result)
        raise HermesRequestError("Hermes result URL did not return image bytes")
