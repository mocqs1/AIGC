"""Deterministic artifact checks for the Shapewear skill."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ALLOWED_EXTENSIONS = {
    "image": {".png", ".jpg", ".jpeg", ".webp"},
    "video": {".mp4", ".webm", ".mov", ".m4v"},
}


IMAGE_FIDELITY_CHECKS = (
    "verify the full garment is visible, centered, and not cropped at the neckline, waist, gusset, leg openings, straps, or closures",
    "verify the product remains shapewear rather than ordinary lingerie, swimwear, a generic bodysuit, or decorative fashion",
    "verify silhouette, proportions, cut, rise, compression zones, panels, cups, straps, openings, gusset, closures, lining, padding, boning, and hardware match the supplied brief or reference",
    "verify fiber or textile character, weave or knit direction, yarn or rib scale, mesh, opacity, thickness, stretch, compression, recovery, sheen, nap, and drape are visibly plausible and consistent",
    "verify every visible seam, stitch, flatlock, coverstitch, overlock, bonded seam, binding, folded hem, laser-cut edge, elastic, gripper, bartack, and reinforcement is present and correctly placed",
    "verify color, tone, pattern, logo, label, print registration, and accessory details are not recolored, redrawn, replaced, or embellished",
    "verify tension lines, folds, edge roll, and contact shadows follow the garment construction without melting, warping, plastic texture, or excessive retouching",
    "verify no unshown garment detail was guessed; ambiguous or occluded areas remain subject to manual review",
)


def image_fidelity_contract() -> dict[str, Any]:
    """Return the exact-match review contract for a shapewear product image."""
    return {
        "standard": "exact_visual_match",
        "target": "same_selected_shapewear_design_and_material",
        "scope": "all_observable_fabric_construction_and_finish_evidence",
        "evidence_policy": "reference_and_declared_attributes_are_sources_of_truth; never_guess_hidden_details",
        "dimensions": list(IMAGE_FIDELITY_CHECKS),
        "verification_status": "pending_manual_review",
        "requires_visual_review": True,
        "automatic_guarantee": False,
    }


def check_artifact(path: str | Path, media_type: str) -> dict[str, Any]:
    """Check file presence/extension and leave visual checks for review."""
    normalized_type = media_type.strip().lower() if isinstance(media_type, str) else ""
    artifact = Path(path)
    exists = artifact.is_file()
    non_empty = exists and artifact.stat().st_size > 0
    extension_ok = artifact.suffix.lower() in ALLOWED_EXTENSIONS.get(normalized_type, set())
    if normalized_type == "image":
        manual_checks = list(IMAGE_FIDELITY_CHECKS)
    else:
        manual_checks = [
            "product is complete and recognizable",
            "garment structure and anatomy are plausible",
            "material detail is readable",
            "product continuity, ad logic, and 9:16 framing are correct",
        ]
    result = {
        "path": str(artifact),
        "media_type": normalized_type,
        "exists": exists,
        "non_empty": non_empty,
        "extension_ok": extension_ok,
        "passed": exists and non_empty and extension_ok and normalized_type in ALLOWED_EXTENSIONS,
        "manual_review": manual_checks,
    }
    if normalized_type == "image":
        result["fidelity"] = image_fidelity_contract()
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Check a shapewear output artifact")
    parser.add_argument("path")
    parser.add_argument("--type", choices=("image", "video"), required=True)
    args = parser.parse_args()
    print(json.dumps(check_artifact(args.path, args.type), indent=2))


if __name__ == "__main__":
    main()
