"""Video generation orchestration and local result saving."""

from __future__ import annotations

import re
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse
from urllib.request import Request

from providers.http_safety import safe_urlopen

from .veo_provider import VeoClient
from .google_veo_provider import GoogleVeoClient
from .seedance_provider import SeedanceClient
from config import env_value


class VideoGenerationError(RuntimeError):
    """Raised when a video task fails or returns an unusable result."""


class VideoGenerationClient(Protocol):
    def submit_generation(self, prompt: str, **options: Any) -> Any: ...

    def query_task(self, task_id: str) -> Any: ...

    def get_result(self, task_id: str) -> Any: ...


def generate_video(
    prompt: str,
    image: str | None = None,
    client: VideoGenerationClient | None = None,
    provider: str = "veo",
    output_dir: str = "outputs/videos",
    max_polls: int = 30,
    poll_interval: float = 0,
) -> str:
    """Submit, poll, download, and save one text-to-video or image-to-video result."""
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt must be a non-empty string")
    if image is not None and (not isinstance(image, str) or not image.strip()):
        raise ValueError("image must be a non-empty string when provided")
    if not isinstance(max_polls, int) or max_polls <= 0:
        raise ValueError("max_polls must be a positive integer")
    if poll_interval < 0:
        raise ValueError("poll_interval must not be negative")

    if client is not None:
        generation_client = client
    else:
        normalized_provider = provider.strip().lower() if isinstance(provider, str) else ""
        if normalized_provider == "veo":
            # Native Google Veo is the default when no gateway mode is requested.
            # Set VEO_API_MODE=gateway to use the older configurable task adapter.
            veo_mode = (env_value("VEO_API_MODE", "native") or "native").strip().lower()
            if veo_mode == "native":
                generation_client = GoogleVeoClient()
            elif veo_mode == "gateway":
                generation_client = VeoClient()
            else:
                raise ValueError("VEO_API_MODE must be native or gateway")
        elif normalized_provider == "seedance":
            generation_client = SeedanceClient()
        else:
            raise ValueError("video provider must be veo or seedance")
    options = {"image": image} if image is not None else {}
    submission = generation_client.submit_generation(prompt, **options)
    task_id = _extract_task_id(submission)
    if not task_id:
        raise VideoGenerationError("generation submission did not return a task id")

    completed = False
    for poll_number in range(max_polls):
        status_response = generation_client.query_task(task_id)
        status = _extract_status(status_response)
        if status in {"failed", "fail", "failure", "error", "cancelled", "canceled", "rejected"}:
            detail = _extract_message(status_response)
            suffix = f": {detail}" if detail else ""
            raise VideoGenerationError(f"video generation task {task_id} failed{suffix}")
        if status in {"succeeded", "succeed", "success", "completed", "complete", "done", "finished"}:
            completed = True
            break
        if status is None and _extract_result_reference(status_response) is not None:
            completed = True
            break
        if poll_number + 1 < max_polls and poll_interval:
            time.sleep(poll_interval)

    if not completed:
        raise VideoGenerationError(
            f"video generation task {task_id} did not complete after {max_polls} polls"
        )

    result = generation_client.get_result(task_id)
    content, extension = _result_bytes(result, generation_client)
    safe_task_id = re.sub(r"[^A-Za-z0-9._-]+", "_", task_id).strip("._") or "result"
    output_path = Path(output_dir) / f"video_{safe_task_id}{extension or '.mp4'}"
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(content)
    except OSError as error:
        raise VideoGenerationError(f"could not save video to {output_path}: {error}") from error
    return str(output_path)


def _extract_task_id(response: Any) -> str | None:
    if isinstance(response, str) and response.strip():
        return response.strip()
    value = _find_value(response, {"task_id", "taskid", "taskId", "id", "name"})
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _extract_status(response: Any) -> str | None:
    if isinstance(response, str):
        return response.strip().lower() or None
    value = _find_value(response, {"status", "state"})
    if value is None:
        return None
    return str(value).strip().lower() or None


def _extract_message(response: Any) -> str | None:
    value = _find_value(response, {"error", "message", "reason"})
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _extract_result_reference(response: Any) -> Any:
    if isinstance(response, (bytes, bytearray, memoryview)):
        return response
    if isinstance(response, str) and response.startswith(("http://", "https://")):
        return response
    reference = _find_value(
        response,
        {
            "url", "result", "results", "data", "video", "result_url", "resultUrl",
            "video_url", "videoUrl", "download_url", "downloadUrl", "bytes", "uri",
        },
    )
    if isinstance(reference, Mapping):
        nested = _extract_result_reference(reference)
        return nested if nested is not None else reference
    if isinstance(reference, (list, tuple)):
        for item in reference:
            nested = _extract_result_reference(item)
            if nested is not None:
                return nested
    return reference


def _find_value(value: Any, keys: set[str]) -> Any:
    if isinstance(value, (list, tuple)):
        for candidate in value:
            found = _find_value(candidate, keys)
            if found is not None:
                return found
        return None
    if not isinstance(value, Mapping):
        return None
    for key, candidate in value.items():
        if str(key) in keys:
            return candidate
    for candidate in value.values():
        found = _find_value(candidate, keys)
        if found is not None:
            return found
    return None


def _result_bytes(result: Any, client: VideoGenerationClient) -> tuple[bytes, str | None]:
    if isinstance(result, (bytes, bytearray, memoryview)):
        return bytes(result), None
    reference = _extract_result_reference(result)
    if isinstance(reference, (bytes, bytearray, memoryview)):
        return bytes(reference), None
    if isinstance(reference, str) and reference.startswith(("http://", "https://")):
        downloader = getattr(client, "download_result", None)
        content = downloader(reference) if callable(downloader) else _download_url(reference)
        if not isinstance(content, (bytes, bytearray, memoryview)):
            raise VideoGenerationError("result URL did not produce video bytes")
        suffix = Path(urlparse(reference).path).suffix.lower()
        if suffix not in {".mp4", ".webm", ".mov", ".m4v"}:
            suffix = None
        return bytes(content), suffix
    raise VideoGenerationError("video result did not contain bytes or a result URL")


def _download_url(url: str) -> bytes:
    try:
        with safe_urlopen(Request(url, method="GET"), timeout=30.0) as response:
            return response.read()
    except OSError as error:
        raise VideoGenerationError(f"could not download video result: {error}") from error
