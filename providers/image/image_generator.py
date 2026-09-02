"""Image generation orchestration and local result saving."""

from __future__ import annotations

import re
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse
from urllib.request import Request

from providers.http_safety import safe_urlopen

from providers.liblib.client import LiblibClient


class ImageGenerationError(RuntimeError):
    """Raised when an image task fails or returns an unusable result."""


class ImageGenerationClient(Protocol):
    def submit_generation(self, prompt: str, **options: Any) -> Any: ...

    def query_task(self, task_id: str) -> Any: ...

    def get_result(self, task_id: str) -> Any: ...


def generate_image(
    prompt: str,
    client: ImageGenerationClient | None = None,
    output_dir: str = "outputs/images",
    max_polls: int = 30,
    poll_interval: float = 0,
    image: str | None = None,
    references: list[str] | None = None,
    aspect_ratio: str = "square",
    max_wait_seconds: float | None = None,
    resume_task_id: str | None = None,
    on_task_submitted=None,
) -> str:
    """Submit, poll, download, and save one image result."""
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt must be a non-empty string")
    if not isinstance(max_polls, int) or max_polls <= 0:
        raise ValueError("max_polls must be a positive integer")
    if poll_interval < 0:
        raise ValueError("poll_interval must not be negative")
    if max_wait_seconds is not None and max_wait_seconds <= 0:
        raise ValueError("max_wait_seconds must be greater than zero")

    generation_client = client if client is not None else LiblibClient()
    options: dict[str, Any] = {}
    if image is not None:
        options["image"] = image
    if references:
        options["references"] = references
    if aspect_ratio != "square":
        options["aspect_ratio"] = aspect_ratio
    # A persisted provider task can be resumed after a browser or API restart.
    # Never submit it again: a timed-out HTTP response may still have created
    # a billable task upstream.
    if resume_task_id:
        submission = {"task_id": str(resume_task_id).strip()}
    else:
        submission = generation_client.submit_generation(prompt, **options)
    direct_result = _extract_result_reference(submission)
    task_id = _extract_task_id(submission)
    if not task_id and direct_result is None:
        raise ImageGenerationError("generation submission did not return a task id or image result")
    if task_id and callable(on_task_submitted) and not resume_task_id:
        on_task_submitted(task_id)

    completed = direct_result is not None
    deadline = time.monotonic() + max_wait_seconds if max_wait_seconds is not None else None
    for poll_number in range(max_polls if not completed else 0):
        if deadline is not None and time.monotonic() >= deadline:
            break
        status_response = _query_with_retry(generation_client, task_id, stage="query_task")
        status = _extract_status(status_response)
        if status in {"failed", "fail", "failure", "error", "cancelled", "canceled", "rejected"}:
            detail = _extract_message(status_response)
            suffix = f": {detail}" if detail else ""
            raise ImageGenerationError(f"image generation task {task_id} failed{suffix}")
        if status in {"succeeded", "succeed", "success", "completed", "complete", "done", "finished"}:
            completed = True
            break
        if status is None and _extract_result_reference(status_response) is not None:
            completed = True
            break
        if poll_number + 1 < max_polls and poll_interval:
            delay = poll_interval
            if poll_interval >= 2:
                # Keep early feedback responsive, then reduce provider load for
                # long-running image2 jobs while respecting the total deadline.
                delay = min(10.0, poll_interval * (1 + poll_number // 10))
            if deadline is not None:
                delay = min(delay, max(0.0, deadline - time.monotonic()))
            if delay:
                time.sleep(delay)

    if not completed:
        waited = f" after {max_wait_seconds:g} seconds" if max_wait_seconds is not None else ""
        raise ImageGenerationError(
            f"image generation task {task_id} did not complete{waited} ({max_polls} polls)"
        )

    result = submission if direct_result is not None else _result_with_retry(generation_client, task_id)
    content, extension = _result_bytes(result, generation_client)
    result_id = task_id or f"result_{int(time.time() * 1000)}"
    safe_task_id = re.sub(r"[^A-Za-z0-9._-]+", "_", result_id).strip("._") or "result"
    output_path = Path(output_dir) / f"image_{safe_task_id}{extension or '.png'}"
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(content)
    except OSError as error:
        raise ImageGenerationError(f"could not save image to {output_path}: {error}") from error
    return str(output_path)


def _extract_task_id(response: Any) -> str | None:
    # Synchronous image APIs may include a request ``id`` alongside ``data``.
    # A concrete image result must win over that generic identifier; otherwise
    # the worker would incorrectly poll a task endpoint that does not exist.
    if _extract_result_reference(response) is not None:
        return None
    if isinstance(response, str) and response.strip():
        return response.strip()
    value = _find_value(response, {"task_id", "taskid", "taskId", "id"})
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
    return re.sub(r"[\s-]+", "_", str(value).strip().lower()) or None


def _retryable_provider_error(error: Exception) -> bool:
    text = str(error).lower()
    return any(token in text for token in ("timeout", "timed out", "temporarily", "connection", "502", "503", "504", "429"))


def _query_with_retry(client: ImageGenerationClient, task_id: str, *, stage: str) -> Any:
    if getattr(client, "handles_request_retries", False):
        return client.query_task(task_id)
    for attempt in range(3):
        try:
            return client.query_task(task_id)
        except Exception as error:
            if attempt == 2 or not _retryable_provider_error(error):
                raise
            time.sleep(min(10.0, 2.0 ** attempt))
    raise AssertionError(f"unreachable {stage}")


def _result_with_retry(client: ImageGenerationClient, task_id: str) -> Any:
    getter = client.get_result
    if getattr(client, "handles_request_retries", False):
        return getter(task_id)
    for attempt in range(3):
        try:
            return getter(task_id)
        except Exception as error:
            if attempt == 2 or not _retryable_provider_error(error):
                raise
            time.sleep(min(10.0, 2.0 ** attempt))
    raise AssertionError("unreachable get_result")


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
            "url",
            "result",
            "data",
            "image",
            "result_url",
            "resultUrl",
            "image_url",
            "imageUrl",
            "download_url",
            "downloadUrl",
            "bytes",
            "b64_json",
        },
    )
    if isinstance(reference, Mapping):
        nested = _extract_result_reference(reference)
        return nested if nested is not None else reference
    if isinstance(reference, (list, tuple)):
        for candidate in reference:
            nested = _extract_result_reference(candidate)
            if nested is not None:
                return nested
        return None
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


def _result_bytes(result: Any, client: ImageGenerationClient) -> tuple[bytes, str | None]:
    if isinstance(result, (bytes, bytearray, memoryview)):
        return bytes(result), None

    reference = _extract_result_reference(result)
    if isinstance(reference, (bytes, bytearray, memoryview)):
        return bytes(reference), None
    if isinstance(reference, str) and reference.startswith(("http://", "https://")):
        downloader = getattr(client, "download_result", None)
        if callable(downloader):
            content = downloader(reference)
        else:
            content = _download_url(reference)
        if not isinstance(content, (bytes, bytearray, memoryview)):
            raise ImageGenerationError("result URL did not produce image bytes")
        suffix = Path(urlparse(reference).path).suffix.lower()
        if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
            suffix = None
        return bytes(content), suffix
    if isinstance(reference, str):
        try:
            import base64

            return base64.b64decode(reference, validate=True), None
        except (ValueError, TypeError):
            pass
    raise ImageGenerationError("image result did not contain bytes or a result URL")


def _download_url(url: str) -> bytes:
    try:
        with safe_urlopen(Request(url, method="GET"), timeout=30.0) as response:
            return response.read()
    except OSError as error:
        raise ImageGenerationError(f"could not download image result: {error}") from error
