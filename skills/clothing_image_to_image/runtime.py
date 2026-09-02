"""Runtime bridge for immutable-garment image-to-image generation."""

from __future__ import annotations

import ipaddress
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from main import generate_image as engine_generate_image

from .quality_check import check_artifact


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILL_ROOT = PROJECT_ROOT / "skills" / "clothing-image-to-image"
OUTPUT_ROOT = PROJECT_ROOT / "outputs" / "clothing_image"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


class ClothingImageRequestError(ValueError):
    """Raised when a clothing image-to-image request is invalid or unsafe."""


IMMUTABLE_GARMENT_CONTRACT = (
    "IMMUTABLE GARMENT CONTRACT: Treat the supplied clothing reference as the "
    "single source of truth for the garment. Reproduce the exact garment shown: "
    "color, hue, tone, pattern, print, silhouette, proportions, cut, panels, "
    "seams, stitching, hems, edges, straps, openings, closures, hardware, logos, "
    "labels, material, weave, texture, sheen, thickness, and visible finish. "
    "Do not recolor, redesign, add, remove, smooth away, or invent any garment "
    "detail. Do not turn the garment into a different product. Keep construction "
    "physically plausible and make fabric and workmanship clearly inspectable. "
    "Only change the requested presentation context; the garment itself must "
    "remain unchanged."
)

IMAGE_INPUT_BINDING = (
    "IMAGE INPUT BINDING: The attached image is the exact garment to present. "
    "Use that image as the single source of truth and preserve its identity in "
    "the output. Do not generate a generic clothing item from the text prompt "
    "and do not substitute another garment. The image input is the highest-"
    "priority conditioning signal. If the garment cannot be recognized clearly, "
    "do not invent a replacement; request a clearer source image."
)

CLOTHING_PROVIDER = "hermes"

_FORBIDDEN_PATTERNS = (
    r"\b(?:minor|child|children|kid|teen|teenager)\b",
    r"\b(?:nude|naked|porn|explicit sex|sexual act|fetish)\b",
    r"\b(?:body[- ]sham(?:e|ing)|hide your fat|hate your body|fix(?:ing)? your body)\b",
    r"\b(?:guaranteed|cure|treat|prevent|doctor[- ]approved|medical[- ]grade|clinically proven|pain relief|weight loss|burn fat)\b",
)

# Presentation may change, but a request must not instruct the model to alter
# the source garment. The immutable suffix itself is trusted and is appended
# after this guard runs.
_GARMENT_OVERRIDE_PATTERNS = (
    r"\b(?:recolor|re-colou?r|dye|change|alter|modify|redesign|replace|add|remove|delete|invent)\b.{0,56}\b(?:garment|clothing|outfit|dress|shirt|top|bottom|color|colour|hue|pattern|print|logo|seam|fabric|material|detail|construction)\b",
    r"\b(?:different|new|another)\s+(?:color|colour|pattern|print|fabric|material|garment|outfit)\b",
)


def _iter_strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for key, candidate in value.items():
            yield from _iter_strings(key)
            yield from _iter_strings(candidate)
    elif isinstance(value, (list, tuple, set)):
        for candidate in value:
            yield from _iter_strings(candidate)


def _guard_text(value: Any) -> None:
    text = " ".join(_iter_strings(value)).lower()
    if any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in _FORBIDDEN_PATTERNS):
        raise ClothingImageRequestError("request contains unsupported sexual, minor, body-shaming, or medical content")
    clauses = re.split(r"[.!?;\n]+", text)
    for clause in clauses:
        if any(re.search(pattern, clause.strip(), flags=re.IGNORECASE) for pattern in _GARMENT_OVERRIDE_PATTERNS):
            raise ClothingImageRequestError("request may only change the clothing presentation, not the garment itself")


def _validate_image_reference(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ClothingImageRequestError(f"{field} must be a non-empty image path or URL")
    candidate = value.strip()
    windows_path = bool(re.match(r"^[A-Za-z]:[\\/]", candidate)) or candidate.startswith(("\\\\", "//"))
    parsed = urlsplit(candidate) if not windows_path else None
    if parsed is not None and parsed.scheme:
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ClothingImageRequestError(f"{field} must be a public HTTPS image URL")
        hostname = (parsed.hostname or "").lower()
        if hostname == "localhost":
            raise ClothingImageRequestError(f"{field} must be a public HTTPS image URL")
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            pass
        else:
            if not address.is_global:
                raise ClothingImageRequestError(f"{field} must be a public HTTPS image URL")
        suffix = Path(parsed.path).suffix.lower()
    else:
        suffix = Path(candidate).suffix.lower()
    if suffix not in IMAGE_EXTENSIONS:
        raise ClothingImageRequestError(f"{field} must reference a PNG, JPEG, WEBP, or GIF image")
    return candidate


def _normalise_reference_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ClothingImageRequestError(f"{field} must be a list containing exactly one image")
    result: list[str] = []
    for index, item in enumerate(value):
        if isinstance(item, Mapping):
            item = item.get("value")
        result.append(_validate_image_reference(item, f"{field}[{index}]"))
    return result


def _load_workflow() -> dict[str, Any]:
    path = SKILL_ROOT / "workflows" / "clothing_image_to_image.json"
    try:
        workflow = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"could not load clothing image workflow: {path}") from error
    if (
        not isinstance(workflow, dict)
        or workflow.get("type") != "image"
        or workflow.get("provider") != CLOTHING_PROVIDER
        or workflow.get("provider_locked") is not True
    ):
        raise RuntimeError(f"clothing image workflow must be an image object: {path}")
    return workflow


def _request_parts(request: Mapping[str, Any] | str, garment_image: str | None, reference_images: Sequence[str] | None) -> tuple[str, str, dict[str, Any]]:
    body = {"prompt": request} if isinstance(request, str) else dict(request) if isinstance(request, Mapping) else None
    if body is None:
        raise ClothingImageRequestError("request must be a mapping or prompt string")
    _guard_text(body)

    body_sources = [
        body[key]
        for key in ("garment_image", "garment_asset", "image")
        if key in body and body[key] is not None
    ]
    if len(body_sources) > 1:
        normalised_sources = [_validate_image_reference(value, "garment_image") for value in body_sources]
        if len(set(normalised_sources)) != 1:
            raise ClothingImageRequestError("garment image aliases must identify the same single image")
    explicit = garment_image if garment_image is not None else (body_sources[0] if body_sources else None)
    if garment_image is not None and body_sources:
        selected = _validate_image_reference(garment_image, "garment_image")
        body_selected = _validate_image_reference(body_sources[0], "garment_image")
        if selected != body_selected:
            raise ClothingImageRequestError("garment image aliases must identify the same single image")
    refs = reference_images if reference_images is not None else body.get("reference_images")
    if refs is not None:
        normalised = _normalise_reference_list(refs, "reference_images")
        if len(normalised) != 1:
            raise ClothingImageRequestError("clothing image-to-image requires exactly one garment reference image")
        if explicit is not None and _validate_image_reference(explicit, "garment_image") != normalised[0]:
            raise ClothingImageRequestError("garment_image and reference_images must identify the same single image")
        explicit = normalised[0]
    garment = _validate_image_reference(explicit, "garment_image")

    prompt_value = body.get("prompt")
    if isinstance(prompt_value, str) and prompt_value.strip():
        objective = prompt_value.strip()
    else:
        objective = str(body.get("objective") or "Create a premium commercial clothing product image focused on craftsmanship, fabric texture, construction, and functional details.").strip()
        scene = str(body.get("scene") or "a clean neutral studio").strip()
        camera = str(body.get("camera") or "a crisp three-quarter product view with optional macro detail framing").strip()
        lighting = str(body.get("lighting") or "soft directional light that reveals weave, seams, edges, and material finish").strip()
        model_preference = str(body.get("model_preference") or "Do not add a model unless needed; a flat lay, mannequin, hanger, or product-only composition is preferred.").strip()
        objective = f"{objective} Scene: {scene}. Camera: {camera}. Lighting: {lighting}. Presentation: {model_preference}"
    prompt = f"{IMAGE_INPUT_BINDING}\n\n{objective}\n\n{IMMUTABLE_GARMENT_CONTRACT}"
    return prompt, garment, body


def build_prompt(request: Mapping[str, Any] | str) -> str:
    """Build the immutable garment prompt without resolving a reference.

    This is used by the local API's prompt preview; generation still requires
    and validates exactly one actual garment image.
    """
    body = {"prompt": request} if isinstance(request, str) else dict(request) if isinstance(request, Mapping) else None
    if body is None:
        raise ClothingImageRequestError("request must be a mapping or prompt string")
    _guard_text(body)
    prompt_value = body.get("prompt")
    if isinstance(prompt_value, str) and prompt_value.strip():
        objective = prompt_value.strip()
    else:
        objective = str(body.get("objective") or "Create a premium commercial clothing product image focused on craftsmanship, fabric texture, construction, and functional details.").strip()
        scene = str(body.get("scene") or "a clean neutral studio").strip()
        camera = str(body.get("camera") or "a crisp three-quarter product view with optional macro detail framing").strip()
        lighting = str(body.get("lighting") or "soft directional light that reveals weave, seams, edges, and material finish").strip()
        model_preference = str(body.get("model_preference") or "Do not add a model unless needed; a flat lay, mannequin, hanger, or product-only composition is preferred.").strip()
        objective = f"{objective} Scene: {scene}. Camera: {camera}. Lighting: {lighting}. Presentation: {model_preference}"
    return f"{IMAGE_INPUT_BINDING}\n\n{objective}\n\n{IMMUTABLE_GARMENT_CONTRACT}"


def _write_prompt(prompt: str, output_root: Path) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "prompt.txt").write_text(prompt + "\n", encoding="utf-8")


def generate_image(
    request: Mapping[str, Any] | str,
    *,
    garment_image: str | None = None,
    garment_asset: str | None = None,
    image: str | None = None,
    reference_images: Sequence[str] | None = None,
    client: Any | None = None,
    output_dir: str | Path | None = None,
    **options: Any,
) -> dict[str, Any]:
    """Generate one image from exactly one immutable garment reference."""
    keyword_sources = [
        value
        for value in (garment_image, garment_asset, image)
        if value is not None
    ]
    if len(keyword_sources) > 1:
        normalised_sources = [_validate_image_reference(value, "garment_image") for value in keyword_sources]
        if len(set(normalised_sources)) != 1:
            raise ClothingImageRequestError("garment image aliases must identify the same single image")
    selected_image = keyword_sources[0] if keyword_sources else None
    if "references" in options or "reference_images" in options:
        raise ClothingImageRequestError("clothing image-to-image accepts exactly one garment reference image")
    prompt, garment, body = _request_parts(request, selected_image, reference_images)
    workflow = _load_workflow()
    provider_candidates = [
        candidate
        for candidate in (options.pop("provider", None), body.get("provider"))
        if candidate is not None
    ]
    if any(
        not isinstance(candidate, str)
        or candidate.strip().lower() != CLOTHING_PROVIDER
        for candidate in provider_candidates
    ):
        raise ClothingImageRequestError("clothing image-to-image requires the Hermes provider")
    provider = CLOTHING_PROVIDER
    root = Path(output_dir) if output_dir is not None else OUTPUT_ROOT
    _write_prompt(prompt, root)
    path = engine_generate_image(
        prompt,
        client=client,
        output_dir=root,
        image=garment,
        references=[],
        provider=provider,
        **options,
    )
    quality = check_artifact(path)
    return {
        "skill": "clothing-image-to-image",
        "workflow": workflow.get("name", "clothing_image_to_image"),
        "type": "image",
        "provider": provider,
        "prompt": prompt,
        "garment_reference": garment,
        "outputs": [path],
        "quality": quality,
    }


__all__ = [
    "CLOTHING_PROVIDER",
    "IMAGE_INPUT_BINDING",
    "IMMUTABLE_GARMENT_CONTRACT",
    "ClothingImageRequestError",
    "build_prompt",
    "generate_image",
]
