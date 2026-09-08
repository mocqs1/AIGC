"""Unified entry points for the minimal AI Generation Engine."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agents.prompt_agent import generate_prompt
from config import OUTPUTS_DIR, env_value, load_workflow
from providers.image.image_generator import generate_image as _generate_image
from providers.image.hermes_client import HermesClient
from providers.video.video_generator import generate_video as _generate_video


def _prompt_for_request(request: Mapping[str, Any] | str, generation_type: str) -> str:
    if isinstance(request, str):
        if not request.strip():
            raise ValueError("prompt must be a non-empty string")
        return request.strip()
    if not isinstance(request, Mapping):
        raise ValueError("request must be a mapping or prompt string")

    direct_prompt = request.get("prompt")
    if direct_prompt is not None:
        if not isinstance(direct_prompt, str) or not direct_prompt.strip():
            raise ValueError("prompt must be a non-empty string")
        return direct_prompt.strip()

    prompt_request = dict(request)
    prompt_request.setdefault("type", generation_type)
    requested_type = prompt_request.get("type")
    if not isinstance(requested_type, str) or requested_type.strip().lower() != generation_type:
        raise ValueError(f"request type must be {generation_type}")
    return generate_prompt(prompt_request)


def _validate_provider(workflow: Mapping[str, Any]) -> None:
    provider = workflow.get("provider")
    if not isinstance(provider, str) or not provider.strip():
        raise RuntimeError("workflow provider must be a non-empty string")


def generate_image(
    request: Mapping[str, Any] | str,
    *,
    client: Any | None = None,
    output_dir: str | Path | None = None,
    max_polls: int = 30,
    poll_interval: float = 0,
    provider: str | None = None,
    image: str | None = None,
    references: list[str] | None = None,
    aspect_ratio: str = "square",
    max_wait_seconds: float | None = None,
    resume_task_id: str | None = None,
    on_task_submitted=None,
) -> str:
    """Optimize a request, select the image workflow, and save the result."""
    workflow = load_workflow("image")
    _validate_provider(workflow)
    prompt = _prompt_for_request(request, "image")
    destination = output_dir if output_dir is not None else OUTPUTS_DIR / "images"
    generation_client = client
    selected_provider = provider
    if not selected_provider and isinstance(request, Mapping):
        candidate_provider = request.get("provider")
        if isinstance(candidate_provider, str) and candidate_provider.strip():
            selected_provider = candidate_provider
    selected_provider = (selected_provider or workflow["provider"]).strip().lower()
    if generation_client is None and selected_provider in {"hermes", "hermes_volcano", "liblib"}:
        prefix = "HERMES" if selected_provider == "hermes" else "HERMES_VOLCANO"
        default_api_url = "https://aiapi.yicheng.bj.cn/v1" if prefix == "HERMES" else "https://ark.cn-beijing.volces.com/api/v3"
        default_model = "gpt-image-2" if prefix == "HERMES" else "doubao-seedream-5-0-pro-260628"
        generation_client = HermesClient(
            api_url=env_value(f"{prefix}_API_URL", default_api_url) or default_api_url,
            api_key=env_value(f"{prefix}_API_KEY", "") or "",
            model=env_value(f"{prefix}_MODEL", default_model) or default_model,
            submit_path=env_value(f"{prefix}_SUBMIT_PATH", "/images/generations") or "/images/generations",
            edit_path=env_value(f"{prefix}_EDIT_PATH", "/images/edits") or "/images/edits",
            status_path=env_value(f"{prefix}_STATUS_PATH", "/images/generations/{task_id}") or "/images/generations/{task_id}",
            result_path=env_value(f"{prefix}_RESULT_PATH", "/images/generations/{task_id}") or "/images/generations/{task_id}",
            timeout=env_value(f"{prefix}_TIMEOUT", "480") or "480",
            query_timeout=env_value(f"{prefix}_QUERY_TIMEOUT", "30") or "30",
            result_timeout=env_value(f"{prefix}_RESULT_TIMEOUT", "120") or "120",
            download_timeout=env_value(f"{prefix}_DOWNLOAD_TIMEOUT", "120") or "120",
        )
    return _generate_image(
        prompt,
        client=generation_client,
        output_dir=str(destination),
        max_polls=max_polls,
        poll_interval=poll_interval,
        image=image,
        references=references,
        aspect_ratio=aspect_ratio,
        max_wait_seconds=max_wait_seconds,
        resume_task_id=resume_task_id,
        on_task_submitted=on_task_submitted,
    )


def generate_video(
    request: Mapping[str, Any] | str,
    *,
    image: str | None = None,
    provider: str | None = None,
    client: Any | None = None,
    output_dir: str | Path | None = None,
    max_polls: int = 30,
    poll_interval: float = 0,
) -> str:
    """Optimize a request, select the video workflow, and save the result."""
    workflow = load_workflow("video")
    _validate_provider(workflow)
    prompt = _prompt_for_request(request, "video")
    selected_provider = provider or workflow["provider"]
    if isinstance(request, Mapping) and request.get("provider") is not None:
        requested_provider = request.get("provider")
        if not isinstance(requested_provider, str) or not requested_provider.strip():
            raise ValueError("provider must be a non-empty string")
        selected_provider = requested_provider.strip().lower()
    if not isinstance(selected_provider, str) or not selected_provider.strip():
        raise ValueError("provider must be a non-empty string")
    selected_provider = selected_provider.strip().lower()
    if client is None and selected_provider not in {"veo", "seedance"}:
        raise ValueError("provider must be veo or seedance")
    if image is None and isinstance(request, Mapping):
        candidate = request.get("image")
        if candidate is not None:
            image = candidate
    destination = output_dir if output_dir is not None else OUTPUTS_DIR / "videos"
    return _generate_video(
        prompt,
        image=image,
        client=client,
        provider=selected_provider.strip().lower(),
        output_dir=str(destination),
        max_polls=max_polls,
        poll_interval=poll_interval,
    )


def _cli() -> None:
    print("Enter a JSON request with type=image or type=video (Ctrl+C to exit):")
    request = json.loads(input())
    generation_type = request.get("type") if isinstance(request, Mapping) else None
    if generation_type == "image":
        print(generate_image(request))
    elif generation_type == "video":
        print(generate_video(request))
    else:
        raise SystemExit("request type must be image or video")


if __name__ == "__main__":
    _cli()
