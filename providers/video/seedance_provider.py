"""Seedance video provider adapters for Volcengine Ark and EvoLink."""
from __future__ import annotations

import base64
import ipaddress
import mimetypes
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from config import env_value
from providers.credential_parser import parse_api_url

from .rest_client import RestVideoClient


MAX_UPLOAD_IMAGE_BYTES = 30 * 1024 * 1024
UPLOAD_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
DEFAULT_ARK_SUBMIT_PATH = "/contents/generations/tasks"
DEFAULT_ARK_STATUS_PATH = "/contents/generations/tasks/{task_id}"
DEFAULT_ARK_RESULT_PATH = DEFAULT_ARK_STATUS_PATH
DEFAULT_EVOLINK_SUBMIT_PATH = "/v1/videos/generations"
DEFAULT_EVOLINK_STATUS_PATH = "/v1/tasks/{task_id}"
DEFAULT_EVOLINK_RESULT_PATH = DEFAULT_EVOLINK_STATUS_PATH
DEFAULT_UPLOAD_API_URL = "https://files-api.evolink.ai"
DEFAULT_UPLOAD_PATH = "/api/v1/files/upload/base64"


def _public_https_url(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Seedance upload response did not contain a file URL")
    candidate = value.strip()
    parsed = urlparse(candidate)
    host = parsed.hostname.rstrip(".").lower() if parsed.hostname else ""
    try:
        address = ipaddress.ip_address(host) if host else None
    except ValueError:
        address = None
    if (
        parsed.scheme.lower() != "https"
        or not host
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or host == "localhost"
        or host.endswith((".localhost", ".local", ".internal"))
        or (address is not None and not address.is_global)
    ):
        raise ValueError("Seedance upload response must be a public HTTPS URL")
    return candidate


class SeedanceClient(RestVideoClient):
    """Adapt Ark and EvoLink Seedance task APIs to the engine contract.

    Ark is selected when the endpoint is Volcengine Ark or the model is a
    ``doubao-seedance-*`` model. Other endpoints retain the EvoLink contract.
    """

    provider_name = "seedance"

    def __init__(
        self,
        *,
        text_model: str | None = None,
        image_model: str | None = None,
        upload_api_url: str | None = None,
        upload_path: str | None = None,
        **kwargs: Any,
    ) -> None:
        configured_url = str(kwargs.get("api_url") or env_value("SEEDANCE_API_URL", "") or "")
        configured_model = str(text_model or env_value("SEEDANCE_TEXT_MODEL", "") or "")
        if configured_url.strip():
            try:
                kwargs["api_url"] = parse_api_url(configured_url)
            except ValueError:
                pass
        hostname = (urlparse(str(kwargs.get("api_url") or configured_url)).hostname or "").lower().rstrip(".")
        self.uses_ark = hostname == "ark.cn-beijing.volces.com" or configured_model.lower().startswith("doubao-seedance-")

        if self.uses_ark:
            if kwargs.get("submit_path") in (None, "", DEFAULT_EVOLINK_SUBMIT_PATH):
                kwargs["submit_path"] = env_value("SEEDANCE_ARK_SUBMIT_PATH", DEFAULT_ARK_SUBMIT_PATH)
            if kwargs.get("status_path") in (None, "", DEFAULT_EVOLINK_STATUS_PATH):
                kwargs["status_path"] = env_value("SEEDANCE_ARK_STATUS_PATH", DEFAULT_ARK_STATUS_PATH)
            if kwargs.get("result_path") in (None, "", DEFAULT_EVOLINK_RESULT_PATH):
                kwargs["result_path"] = env_value("SEEDANCE_ARK_RESULT_PATH", DEFAULT_ARK_RESULT_PATH)
        else:
            kwargs.setdefault("submit_path", env_value("SEEDANCE_SUBMIT_PATH", DEFAULT_EVOLINK_SUBMIT_PATH))
            kwargs.setdefault("status_path", env_value("SEEDANCE_STATUS_PATH", DEFAULT_EVOLINK_STATUS_PATH))
            kwargs.setdefault("result_path", env_value("SEEDANCE_RESULT_PATH", DEFAULT_EVOLINK_RESULT_PATH))
        kwargs.setdefault("timeout", float(env_value("SEEDANCE_TIMEOUT", "180") or "180"))
        super().__init__(**kwargs)
        self.text_model = text_model or env_value(
            "SEEDANCE_TEXT_MODEL", "doubao-seedance-2-0-260128" if self.uses_ark else "seedance-2.5-text-to-video"
        )
        self.image_model = image_model or env_value(
            "SEEDANCE_IMAGE_MODEL", self.text_model if self.uses_ark else "seedance-2.5-image-to-video"
        )
        upload_url = upload_api_url or env_value("SEEDANCE_UPLOAD_API_URL", DEFAULT_UPLOAD_API_URL) or DEFAULT_UPLOAD_API_URL
        self.upload_api_url = parse_api_url(upload_url)
        self.upload_path = upload_path or env_value("SEEDANCE_UPLOAD_PATH", DEFAULT_UPLOAD_PATH)

    def _local_image_data_url(self, image: str) -> str:
        path = Path(image).expanduser()
        if not path.is_file():
            raise ValueError("local Seedance image must be an existing file")
        if path.suffix.lower() not in UPLOAD_IMAGE_SUFFIXES:
            raise ValueError("Seedance local image must be a .jpg, .jpeg, .png, or .webp file")
        try:
            size = path.stat().st_size
            if size <= 0:
                raise ValueError("Seedance local image must not be empty")
            if size > MAX_UPLOAD_IMAGE_BYTES:
                raise ValueError("Seedance local image exceeds the 30 MiB upload limit")
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        except OSError as error:
            raise ValueError("could not read local Seedance image") from error
        mime_type = mimetypes.types_map.get(path.suffix.lower(), "application/octet-stream")
        return f"data:{mime_type};base64,{encoded}"

    def _upload_local_image(self, image: str) -> str:
        path = Path(image).expanduser()
        if not path.is_file():
            raise ValueError("local Seedance image must be an existing file")
        if path.suffix.lower() not in UPLOAD_IMAGE_SUFFIXES:
            raise ValueError("Seedance local image must be a .jpg, .jpeg, .png, or .webp file")
        try:
            size = path.stat().st_size
        except OSError as error:
            raise ValueError("could not inspect local Seedance image") from error
        if size <= 0:
            raise ValueError("Seedance local image must not be empty")
        if size > MAX_UPLOAD_IMAGE_BYTES:
            raise ValueError("Seedance local image exceeds the 30 MiB upload limit")
        try:
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        except OSError as error:
            raise ValueError("could not read local Seedance image") from error
        mime_type = mimetypes.types_map.get(path.suffix.lower(), "application/octet-stream")
        response = self._request(
            "POST",
            f"{self.upload_api_url}/{self.upload_path.lstrip('/')}",
            {"base64_data": f"data:{mime_type};base64,{encoded}", "file_name": path.name},
        )
        if not isinstance(response, dict) or response.get("success") is False:
            raise ValueError("Seedance image upload was rejected by the file service")
        data = response.get("data") if isinstance(response, dict) else None
        file_url = data.get("file_url") if isinstance(data, dict) else None
        return _public_https_url(file_url)

    def _image_reference(self, image: str) -> str:
        if image.startswith(("http://", "https://")):
            return _public_https_url(image)
        return self._local_image_data_url(image) if self.uses_ark else self._upload_local_image(image)

    def submit_generation(self, prompt: str, **options: Any) -> Any:
        image = options.pop("image", None)
        model = options.pop("model", None) or (self.image_model if image is not None else self.text_model)
        if self.uses_ark and "seedance" not in str(model).lower():
            raise ValueError(
                "Seedance Ark requires a doubao-seedance model ID; "
                f"configured model is {model!r}"
            )
        if self.uses_ark:
            content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
            if image is not None:
                if not isinstance(image, str) or not image.strip():
                    raise ValueError("image must be a non-empty local path or public URL for Seedance")
                content.append({"type": "image_url", "image_url": {"url": self._image_reference(image.strip())}})
            payload: dict[str, Any] = {"model": model, "content": content}
            payload.update(options)
            return self._request("POST", f"{self.api_url}/{self.submit_path.lstrip('/')}", payload)
        if image is not None:
            if not isinstance(image, str) or not image.strip():
                raise ValueError("image must be a non-empty local path or public URL for Seedance")
            options["image_urls"] = [self._image_reference(image.strip())]
        return super().submit_generation(prompt, model=model, **options)

    def get_result(self, task_id: str) -> Any:
        return self.query_task(task_id)

    def test_connection(self) -> Any:
        """Probe Ark's authenticated model collection, not the API root."""
        return self._request("GET", f"{self.api_url}/models" if self.uses_ark else self.api_url)


def create_client(**kwargs):
    return SeedanceClient(**kwargs)
