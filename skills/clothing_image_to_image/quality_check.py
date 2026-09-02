"""Deterministic checks for clothing image-to-image artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import Any


ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def check_artifact(path: str | Path) -> dict[str, Any]:
    """Check the saved file; garment fidelity still requires visual review."""
    artifact = Path(path)
    exists = artifact.is_file()
    non_empty = exists and artifact.stat().st_size > 0
    extension_ok = artifact.suffix.lower() in ALLOWED_EXTENSIONS
    return {
        "path": str(artifact),
        "media_type": "image",
        "exists": exists,
        "non_empty": non_empty,
        "extension_ok": extension_ok,
        "passed": exists and non_empty and extension_ok,
        "manual_review": [
            "garment color, hue, pattern, silhouette, and proportions exactly match the source image",
            "fabric texture, weave, thickness, seams, panels, edges, closures, and hardware remain readable",
            "the requested presentation does not crop, occlude, recolor, or distort the garment",
            "any functional interpretation is visually supported and contains no unsupported medical or performance claim",
        ],
    }


__all__ = ["check_artifact"]
