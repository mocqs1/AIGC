"""Deterministic compiler and runner for the mono-color poster skill.

The upstream ``mono-color`` skill provides the design language.  This adapter
turns that language into a bounded local API workflow: approved palettes,
layouts, and substrates are selected deterministically and only the final
prompt is sent to the configured image provider.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from main import generate_image as engine_generate_image

from .quality_check import check_artifact


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILL_ROOT = PROJECT_ROOT / "skills" / "mono-color-poster"
OUTPUT_ROOT = PROJECT_ROOT / "outputs" / "posters"


class PosterRequestError(ValueError):
    """Raised when a poster request cannot satisfy the mono-color contract."""


SUBSTRATES = {
    "substrate_neutral_white": ("Neutral White", "#FAFAF7"),
    "substrate_cool_gray": ("Cool Gray", "#E9E9E5"),
    "substrate_pale_beige": ("Pale Beige", "#F5F1E8"),
}
PALETTES = {
    "palette_cobalt": ("Cobalt", "#2148B8", "", "", "pure one-ink"),
    "palette_terracotta": ("Terracotta Orange", "#C65F38", "", "", "pure one-ink"),
    "palette_signal_red": ("Signal Red", "#C83232", "", "", "pure one-ink"),
    "palette_aubergine": ("Aubergine", "#63365F", "", "", "pure one-ink"),
    "palette_charcoal": ("Charcoal", "#30343A", "", "", "pure one-ink"),
    "palette_cobalt_terracotta": ("Cobalt", "#2148B8", "Terracotta Orange", "#C65F38", "complementary duotone"),
    "palette_charcoal_signal_red": ("Charcoal", "#30343A", "Signal Red", "#C83232", "chromatic + black"),
    "palette_botanical_oxblood": ("Botanical Green", "#008A4B", "Oxblood", "#8F3434", "complementary duotone"),
    "palette_mint_charcoal": ("Mint Green", "#5EB783", "Warm Charcoal", "#302D2E", "chromatic + black"),
    "palette_ultramarine_safety_orange": ("Ultramarine", "#263E99", "Safety Orange", "#E55D2B", "overprint duotone"),
    "palette_cyan_brick_red": ("Cyan", "#159DDA", "Brick Red", "#B64032", "overprint duotone"),
}
LAYOUTS = {
    "composition_image_field": "image field",
    "composition_specimen_annotation": "specimen annotation",
    "composition_type_declaration": "type-led declaration",
    "composition_ruled_information": "ruled information poster",
    "composition_archival_plate": "archival plate",
    "composition_editorial_cover": "editorial cover",
    "composition_object_field": "object field",
    "composition_overprint_collage": "overprint collage",
    "composition_editorial_journal": "editorial journal",
}
TYPE_ROLES = {
    "type_literary": "Literary serif with compact grotesk support",
    "type_cultural_grotesk": "Cultural grotesk with compact mono support",
    "type_condensed_civic": "Condensed civic grotesk with monospaced facts",
    "type_programmatic": "Programmatic modular sans with tabular mono support",
    "type_rotated_display": "Rotated display with small upright grotesk support",
    "type_handwritten_interjection": "Clean display voice with one restrained handwritten aside",
    "type_typographic_object": "Typographic object with minimal mono support",
}
CARRIERS = {
    "carrier_wall_poster": "wall poster",
    "carrier_zine": "bound zine",
    "carrier_social_cover": "social cover",
    "carrier_record_sleeve": "record sleeve",
    "carrier_packaging": "packaging label",
    "carrier_merch": "garment merchandise",
    "carrier_portfolio": "portfolio or exhibition sheet",
}
RATIOS = {"3:4", "2:3", "4:5", "1:1", "4:3"}
TENSIONS = {"relaxed", "balanced", "assertive"}
_TEXT_LIMIT = 2000
_UNSAFE_TEXT = re.compile(r"(?i)(?:https?://|file://|\\\\|\b(?:api[_-]?key|authorization|bearer|secret|password|token)\b)")


def _clean_text(value: Any, field: str, *, required: bool = False, limit: int = _TEXT_LIMIT) -> str:
    if value is None:
        if required:
            raise PosterRequestError(f"{field} must be a non-empty string")
        return ""
    if not isinstance(value, str):
        raise PosterRequestError(f"{field} must be a string")
    candidate = value.strip()
    if required and not candidate:
        raise PosterRequestError(f"{field} must be a non-empty string")
    if len(candidate) > limit or any(ord(char) < 32 and char not in "\n\t" for char in candidate):
        raise PosterRequestError(f"{field} is too long or contains control characters")
    if _UNSAFE_TEXT.search(candidate):
        raise PosterRequestError(f"{field} contains unsupported data")
    return candidate


def _choice(value: Any, field: str, allowed: Mapping[str, Any], default: str) -> str:
    candidate = str(value or default).strip()
    if candidate not in allowed:
        raise PosterRequestError(f"{field} must be one of the approved mono-color options")
    return candidate


def _ratio(value: Any) -> str:
    candidate = str(value or "3:4").strip()
    if candidate not in RATIOS:
        raise PosterRequestError("ratio must be one of 3:4, 2:3, 4:5, 1:1, or 4:3")
    return candidate


def _tension(body: Mapping[str, Any]) -> str:
    requested = str(body.get("tension") or "").strip().lower()
    if requested in TENSIONS:
        return requested
    intent = str(body.get("intent") or "").lower()
    if any(word in intent for word in ("announcement", "event", "宣言", "活动", "发布")):
        return "assertive"
    if any(word in intent for word in ("journal", "observation", "观察", "记录", "研究")):
        return "balanced"
    return "relaxed"


def _recipe(request: Mapping[str, Any] | str) -> dict[str, Any]:
    body: dict[str, Any]
    if isinstance(request, str):
        body = {"subject": request}
    elif isinstance(request, Mapping):
        body = dict(request)
    else:
        raise PosterRequestError("request must be a mapping or prompt string")
    subject = _clean_text(body.get("subject"), "subject", required=True)
    intent = _clean_text(body.get("intent"), "intent", limit=500) or "an observed cultural note"
    exact_text = _clean_text(body.get("exact_text", body.get("text")), "exact_text", limit=120)
    if not exact_text:
        exact_text = "A QUIET SIGNAL"
    representation = str(body.get("representation") or "faithful reproduction").strip().lower()
    if representation in {"faithful", "faithful reproduction", "reproduce"}:
        representation = "faithful reproduction"
    elif representation in {"abstract", "abstract symbol extraction", "symbol"}:
        representation = "abstract symbol extraction"
    else:
        raise PosterRequestError("representation must be faithful or abstract")
    ratio = _ratio(body.get("ratio"))
    substrate_id = _choice(body.get("substrate"), "substrate", SUBSTRATES, "substrate_neutral_white")
    palette_id = _choice(body.get("palette"), "palette", PALETTES, "palette_cobalt_terracotta")
    layout_id = _choice(body.get("layout"), "layout", LAYOUTS, "composition_editorial_cover")
    type_id = _choice(body.get("type_role"), "type_role", TYPE_ROLES, "type_cultural_grotesk")
    carrier_id = _choice(body.get("carrier"), "carrier", CARRIERS, "carrier_wall_poster")
    tension = _tension(body)
    focal_event = _clean_text(body.get("focal_event"), "focal_event", limit=240) or "one decisive oversized typographic collision"
    release_zone = _clean_text(body.get("release_zone"), "release_zone", limit=240) or "the upper-right region remains quiet open paper"
    gesture = _clean_text(body.get("manual_gesture"), "manual_gesture", limit=240) or "one small registration mark"
    treatment = _clean_text(body.get("image_treatment"), "image_treatment", limit=240) or "clean plate separation with medium halftone screening"
    substrate_name, substrate_hex = SUBSTRATES[substrate_id]
    ink_a, hex_a, ink_b, hex_b, mode = PALETTES[palette_id]
    seed_source = "|".join((subject, exact_text, palette_id, layout_id))
    seed = hashlib.sha256(seed_source.encode("utf-8")).hexdigest()[:12]
    return {
        "subject": subject,
        "intent": intent,
        "exact_text": exact_text,
        "representation": representation,
        "ratio": ratio,
        "substrate": f"{substrate_name} {substrate_hex}",
        "palette": palette_id,
        "ink_a": f"{ink_a} {hex_a}",
        "ink_b": f"{ink_b} {hex_b}".strip(),
        "mode": mode,
        "layout": LAYOUTS[layout_id],
        "type": TYPE_ROLES[type_id],
        "carrier": CARRIERS[carrier_id],
        "tension": tension,
        "focal_event": focal_event,
        "release_zone": release_zone,
        "gesture": gesture,
        "treatment": treatment,
        "reference_attached": bool(body.get("_reference_attached")),
        "seed": seed,
    }


def build_prompt(request: Mapping[str, Any] | str) -> str:
    """Compile the five-paragraph mono-color editorial prompt."""
    recipe = _recipe(request)
    reference_clause = (
        "Use the attached reference photo as the factual source for the subject; preserve its identity and core content."
        if recipe["reference_attached"]
        else "No source photo is supplied; construct the subject from two to four recognizable anchors rather than a stock-photo scene."
    )
    representation_clause = (
        "Reproduce it faithfully through a screened crop, preserving the recognizable subject and exposing paper in highlights."
        if recipe["representation"] == "faithful reproduction"
        else "Extract two to four identity anchors into one dominant mass, one structural contour, and one repeated rhythm; keep incidental detail out."
    )
    ink_clause = (
        f"Use exactly one printing ink: {recipe['ink_a']}, using density changes for image, type, and rules; no second ink is allowed."
        if recipe["mode"] == "pure one-ink"
        else f"Use exactly two printing inks in {recipe['mode']}: dominant {recipe['ink_a']} at 70-85% coverage and accent {recipe['ink_b']} at 15-30% coverage."
    )
    return "\n\n".join(
        (
            f"CANVAS AND INK: Create a flat front-facing {recipe['ratio']} {recipe['carrier']} on {recipe['substrate']}. {ink_clause} The paper is visible and is not an ink.",
            f"ORIGINAL COMPOSITION: Use the {recipe['layout']} layout with {recipe['tension']} visual tension, 35% visibly empty paper, generous 5-9% margins, and a simple asymmetric editorial grid. Make {recipe['focal_event']} the single focal event; reserve {recipe['release_zone']} as the one quiet release zone. Use one manual gesture only: {recipe['gesture']}.",
            f"SUBJECT: The subject is {recipe['subject']}; the intent is {recipe['intent']}. {reference_clause} {representation_clause} Let the dominant subject occupy 45-80% of the page, crop one edge decisively, and let exposed paper cut through the image as knockouts or clipped highlights.",
            f"TYPOGRAPHY AND WORDS: Use {recipe['type']} with a 5-12x scale jump between the display line and support type. Set the exact display text “{recipe['exact_text']}” and make it cross, cover, split around, or lock tightly to the dominant subject. Keep microcopy terse, factual, and free of invented brands, URLs, or claims.",
            f"MATERIAL AND AVOIDS: Render {recipe['treatment']}, visible dots at close range, medium contrast, paper fibers, and restrained uneven ink coverage with stable imperfection seed {recipe['seed']}. Exclude gradients, rainbow or third inks, digital color grading, glossy mockups, 3D depth, lens blur, hard shadows, centered template symmetry, scrapbook clutter, fake logos, CTA language, and imitation of any reference composition.",
        )
    )


def _aspect_for_ratio(ratio: str) -> str:
    if ratio == "1:1":
        return "square"
    if ratio == "4:3":
        return "landscape"
    return "portrait"


def generate_image(
    request: Mapping[str, Any] | str,
    *,
    image: str | None = None,
    references: Sequence[str] | None = None,
    provider: str | None = None,
    client: Any | None = None,
    output_dir: str | Path | None = None,
    max_polls: int = 30,
    poll_interval: float = 0,
    **options: Any,
) -> dict[str, Any]:
    """Generate one poster from zero or one ordered reference image."""
    if references:
        raise PosterRequestError("poster accepts at most one reference image")
    selected_provider = (provider or (request.get("provider") if isinstance(request, Mapping) else None) or "hermes").strip().lower()
    if selected_provider not in {"liblib", "hermes"}:
        raise PosterRequestError("poster requires the Liblib or Hermes image provider")
    body = dict(request) if isinstance(request, Mapping) else {"subject": request}
    if image:
        body["_reference_attached"] = True
    recipe = _recipe(body)
    prompt = build_prompt(body)
    root = Path(output_dir) if output_dir is not None else OUTPUT_ROOT
    root.mkdir(parents=True, exist_ok=True)
    (root / "prompt.txt").write_text(prompt + "\n", encoding="utf-8")
    path = engine_generate_image(
        prompt,
        client=client,
        output_dir=root,
        max_polls=max_polls,
        poll_interval=poll_interval,
        provider=selected_provider,
        image=image,
        references=None,
        aspect_ratio=_aspect_for_ratio(recipe["ratio"]),
        **options,
    )
    quality = check_artifact(path)
    return {
        "skill": "mono-color",
        "workflow": "mono_color_poster",
        "type": "image",
        "provider": selected_provider,
        "prompt": prompt,
        "recipe": {key: recipe[key] for key in ("ratio", "substrate", "palette", "ink_a", "ink_b", "mode", "layout", "type", "carrier", "tension", "seed")},
        "reference_bound": bool(image),
        "outputs": [path],
        "quality": quality,
    }


__all__ = ["PosterRequestError", "build_prompt", "generate_image"]
