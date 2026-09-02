"""Deterministic prompt construction for image and video generation."""

from collections.abc import Mapping


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

    base = (
        f"Create a professional {generation_type} featuring {values['product']} "
        f"in {values['scene']}, rendered in {values['style']} style. "
        "Show the subject clearly with a purposeful composition, realistic materials "
        "and surface detail, balanced lighting, refined color, and high visual quality. "
    )
    if generation_type == "image":
        return base + (
            "Keep the result as a polished still image with a clean, intentional frame, "
            "strong subject placement, and crisp detail."
        )
    return base + (
        "Use a deliberate camera movement such as a smooth push-in or lateral track, "
        "with natural subject motion and subtle environmental movement; maintain clear "
        "temporal continuity and a polished cinematic composition throughout the video."
    )
