"""Small, dependency-free application configuration helpers."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent
WORKFLOW_DIR = PROJECT_ROOT / "workflows"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"


def env_value(name: str, default: str | None = None) -> str | None:
    """Read a setting from the process environment or the project .env file."""
    value = os.environ.get(name)
    if value is not None and value.strip():
        return value.strip()

    env_path = PROJECT_ROOT / ".env"
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return default

    for line in lines:
        entry = line.strip()
        if not entry or entry.startswith("#"):
            continue
        if entry.startswith("export "):
            entry = entry[7:].lstrip()
        key, separator, candidate = entry.partition("=")
        if separator and key.strip() == name:
            candidate = candidate.strip()
            if len(candidate) >= 2 and candidate[0] == candidate[-1] and candidate[0] in {"'", '"'}:
                candidate = candidate[1:-1]
            return candidate or default
    return default


def load_workflow(generation_type: str) -> dict[str, Any]:
    """Load and validate one JSON workflow definition."""
    normalized = generation_type.strip().lower() if isinstance(generation_type, str) else ""
    if normalized not in {"image", "video"}:
        raise ValueError("generation type must be image or video")

    path = WORKFLOW_DIR / f"{normalized}_generation.json"
    try:
        workflow = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"could not load workflow {path}") from error
    if not isinstance(workflow, dict):
        raise RuntimeError(f"workflow {path} must contain a JSON object")
    if workflow.get("type") != normalized or not workflow.get("provider"):
        raise RuntimeError(f"workflow {path} must define matching type and provider")
    return workflow
