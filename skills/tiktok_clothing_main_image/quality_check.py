"""Deterministic checks for TikTok clothing image artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
MANUAL_REVIEW = [
    "master product color, pattern, logo, silhouette, and proportions match the output",
    "fabric texture, weave, thickness, seams, stitch spacing, edges, straps, closures, and labels remain readable",
    "complete garment stays inside the selected aspect-ratio safe area without occlusion or crop",
    "underwear or sleepwear imagery shows an adult, full coverage, ordinary non-sexualized product pose",
    "overlay copy is limited to supplied facts and contains no unsupported medical, body, performance, review, or promotion claim",
    "target-market TikTok Shop and advertising policy status is reviewed before publishing",
]


def check_artifact(path: str | Path, *, aspect_ratio: str = "9:16") -> dict[str, Any]:
    artifact = Path(path)
    exists = artifact.is_file()
    non_empty = exists and artifact.stat().st_size > 0
    extension_ok = artifact.suffix.lower() in ALLOWED_EXTENSIONS
    return {"path": str(artifact), "media_type": "image", "aspect_ratio": aspect_ratio,
            "exists": exists, "non_empty": non_empty, "extension_ok": extension_ok,
            "passed": exists and non_empty and extension_ok, "policy_status": "unreviewed",
            "manual_review": list(MANUAL_REVIEW)}


__all__ = ["ALLOWED_EXTENSIONS", "MANUAL_REVIEW", "check_artifact"]
