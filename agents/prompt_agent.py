"""Deterministic prompt construction for image and video generation."""

from __future__ import annotations

import re
from collections.abc import Mapping

_INTIMATE_APPAREL_PATTERN = re.compile(
    r"shapewear|lingerie|bodysuit|underwear|under ?garment|intimate apparel|"
    r"\bbra\b|bras\b|panty|panties|thong|g-string|briefs|corset|bustier|"
    r"camisole|nightgown|chemise|skims|spanx|"
    r"塑身|塑形|内衣|文胸|内裤|连体衣|胸衣",
    re.IGNORECASE,
)
_BEDROOM_PATTERN = re.compile(
    r"\bbedrooms?\b|\bbed\b|\bboudoir\b|intimate interior|卧室|床上|床边",
    re.IGNORECASE,
)
_CAMPAIGN_BRAND_PATTERN = re.compile(r"\b(?:skims|spanx)\b|SKIMS|SPANX", re.IGNORECASE)

_CATALOG_IMAGE_CONTRACT = (
    "GPT-IMAGE-2 CATALOG STILL: Create one photorealistic commercial product "
    "photograph of {product}. Setting: {scene}. Visual style: {style}. "
    "Show the complete product as the only subject with a purposeful composition, "
    "realistic materials and surface detail, balanced studio lighting, refined color, "
    "and high visual quality. Prefer a product-only packshot, flat-lay, or mannequin "
    "still life on a clean seamless surface. Keep the result as a polished still image "
    "with a clean, intentional frame, strong subject placement, and crisp detail. "
    "Do not add people, faces, hands, body parts, extra products, competitor branding, "
    "watermarks, on-image text, sexual posing, nudity, minors, medical claims, "
    "weight-loss claims, or a lifestyle narrative."
)
_INTIMATE_IMAGE_LOCK = (
    "INTIMATE-APPAREL SAFETY LOCK: Treat this as a fully covered commercial garment "
    "still life, not a fashion campaign or bedroom lifestyle scene. Use product-only "
    "or a headless mannequin. Keep every garment fully covering the body; no skin-focused "
    "crop, wet look, implied nudity, or lying pose. Do not name or imitate any competitor "
    "brand campaign."
)
_SAFE_STUDIO_SCENE = "clean neutral studio, seamless light-gray backdrop, product centered"


def generate_prompt(request: Mapping[str, str]) -> str:
    """Build a professional generation prompt from a validated request.

    No model or provider calls are made; the same request always produces the
    same prompt.
    """
    if not isinstance(request, Mapping):
        raise ValueError("request must be a mapping with product, scene, style, and type")

    values: dict[str, str] = {}
    for field in ("product", "scene", "style", "type"):
        if field not in request:
            raise ValueError(f"missing required field: {field}")
        value = request[field]
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must be a non-empty string")
        values[field] = value.strip()

    generation_type = values["type"].lower()
    if generation_type not in {"image", "video"}:
        raise ValueError("type must be either image or video")

    product = _sanitize_campaign_brands(values["product"])
    scene = values["scene"]
    style = _sanitize_campaign_brands(values["style"])
    combined = f"{product} {scene} {style}"
    intimate = bool(_INTIMATE_APPAREL_PATTERN.search(combined))
    if generation_type == "image" and _BEDROOM_PATTERN.search(scene):
        scene = _SAFE_STUDIO_SCENE

    if generation_type == "image":
        prompt = _CATALOG_IMAGE_CONTRACT.format(product=product, scene=scene, style=style)
        if intimate:
            prompt = f"{prompt} {_INTIMATE_IMAGE_LOCK}"
        return prompt

    base = (
        f"Create a professional {generation_type} featuring {product} "
        f"in {scene}, rendered in {style} style. "
        "Show the subject clearly with a purposeful composition, realistic materials "
        "and surface detail, balanced lighting, refined color, and high visual quality. "
    )
    motion = (
        "Use a deliberate camera movement such as a smooth push-in or lateral track, "
        "with natural subject motion and subtle environmental movement; maintain clear "
        "temporal continuity and a polished cinematic composition throughout the video."
    )
    if intimate:
        motion += (
            " Keep any on-camera adult fully covered. Do not use bedroom intimacy, "
            "nudity, sexual posing, or competitor brand campaigns."
        )
    return base + motion


def _sanitize_campaign_brands(style: str) -> str:
    cleaned = _CAMPAIGN_BRAND_PATTERN.sub(" ", style)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -/,")
    return cleaned or "commercial catalog product photography"
