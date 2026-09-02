"""Technical and manual review checks for poster artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import Any


ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
POSTER_REVIEW = [
    "confirm the page uses no more than two assigned printing inks and the substrate remains visible",
    "confirm 25-55% of the page reads as empty paper with one clearly quieter release zone",
    "confirm one focal event is immediately legible at thumbnail size and the dominant subject remains recognizable",
    "confirm the headline has a clear 5x or greater scale jump and crosses or locks to the dominant subject",
    "confirm halftone or plate separation reads as physical print, not a digital color filter",
    "confirm no copied wording, logos, fake publication marks, or protected reference composition appears",
]


def check_artifact(path: str | Path) -> dict[str, Any]:
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
        "manual_review": list(POSTER_REVIEW),
    }


__all__ = ["check_artifact"]
