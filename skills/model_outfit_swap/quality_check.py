"""Deterministic artifact checks for model outfit swap outputs."""

from __future__ import annotations

from pathlib import Path
from typing import Any


ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}

GARMENT_FIDELITY_CHECKS = (
    "verify the garment silhouette, proportions, length, neckline, openings, panel "
    "geometry, cut lines, and placement match all selected garment references",
    "verify the visible material identity and finish match, including weave or knit "
    "direction, yarn/grain scale, ribbing, pile/nap, mesh, lace, transparency, surface "
    "relief, print registration, and any wear or wash finish",
    "verify thickness, weight, stiffness, stretch, compression, opacity, sheen, drape, "
    "tension lines, folds, wrinkles, and edge roll reproduce the reference fabric behavior",
    "verify every visible seam, stitch line and spacing, topstitch, overlock, binding, "
    "hem, dart, gather, pleat, channel, reinforcement, and bartack is present and placed correctly",
    "verify every visible zipper, slider, hook, eye, snap, button, elastic, strap, adjuster, "
    "cup, lining, padding, boning, pocket, label, logo, and other component matches exactly",
    "verify hue, value, saturation, contrast, pattern repeat, pattern scale, alignment, and "
    "placement match the references under the preserved model-scene lighting",
    "verify no garment evidence was smoothed, simplified, beautified, recolored, substituted, "
    "merged with another design, omitted, or invented where the references are ambiguous",
)

MODEL_PRESERVATION_CHECKS = (
    "verify the adult model identity, face, hair, expression, skin, body proportions, pose, "
    "hands, feet, and anatomy remain unchanged",
    "verify only clothing changed and the background, objects, lighting, shadows, camera, "
    "viewpoint, framing, crop, and aspect ratio remain unchanged",
)


def garment_fidelity_contract() -> dict[str, Any]:
    """Return a fresh exact-match review contract for manifests and Harnesses."""
    return {
        "standard": "exact_visual_match",
        "target": "same_selected_garment",
        "scope": "all_observable_material_and_construction_evidence",
        "evidence_policy": "use_all_selected_garment_references_without_guessing_or_redesign",
        "dimensions": list(GARMENT_FIDELITY_CHECKS),
        "verification_status": "pending_manual_review",
        "requires_visual_review": True,
        "automatic_guarantee": False,
    }


def check_artifact(path: str | Path, media_type: str = "image") -> dict[str, Any]:
    """Check file presence and extension; semantic review remains manual."""
    artifact = Path(path)
    normalized_type = media_type.strip().lower() if isinstance(media_type, str) else ""
    exists = artifact.is_file()
    non_empty = exists and artifact.stat().st_size > 0
    extension_ok = artifact.suffix.lower() in ALLOWED_EXTENSIONS
    return {
        "path": str(artifact),
        "media_type": normalized_type,
        "exists": exists,
        "non_empty": non_empty,
        "extension_ok": extension_ok,
        "passed": normalized_type == "image" and exists and non_empty and extension_ok,
        "garment_fidelity": garment_fidelity_contract(),
        "manual_review": [*MODEL_PRESERVATION_CHECKS, *GARMENT_FIDELITY_CHECKS],
    }


__all__ = [
    "GARMENT_FIDELITY_CHECKS",
    "MODEL_PRESERVATION_CHECKS",
    "check_artifact",
    "garment_fidelity_contract",
]
