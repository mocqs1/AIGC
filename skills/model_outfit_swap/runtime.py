"""Runtime bridge for deterministic model outfit-swap image generation."""

from __future__ import annotations

import re
import json
import ipaddress
import struct
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from main import generate_image as engine_generate_image

from .quality_check import GARMENT_FIDELITY_CHECKS, check_artifact, garment_fidelity_contract


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILL_ROOT = PROJECT_ROOT / "skills" / "model-outfit-swap"
OUTPUT_ROOT = PROJECT_ROOT / "outputs" / "images" / "model_outfit_swap"

# Hermes/Seedream accepts one model image plus at most nine garment images in
# a single multi-image request. Reject larger jobs before uploading any image.
MAX_REFERENCE_IMAGES = 10


class OutfitSwapRequestError(ValueError):
    """Raised when an outfit-swap request is invalid or unsafe."""


_FORBIDDEN_PATTERNS = (
    r"\b(?:minor|child|children|kid|teen|teenager)\b",
    r"\b(?:nude|naked|porn|explicit sex|sexual act)\b",
    r"\b(?:body[- ]sham(?:e|ing)|hide your fat|fix(?:ing)? your body|hate your body)\b",
    r"\b(?:reshape|sculpt|slim(?:ming)?|alter|change|fix(?:ing)?)\s+(?:your|the|my)?\s*body\b",
    r"\b(?:reduce|relieve|remove|eliminate)\s+(?:your\s+)?pain\b",
    r"\b(?:pain[- ]free|pain relief|weight loss|burn fat|doctor[- ]approved|clinically proven|medical claim)\b",
)

# A user brief can describe the desired garment, but it must not be able to
# widen the edit mask.  These patterns catch positive edit instructions while
# the immutable contract below remains present for every provider request.
_OVERRIDE_PATTERNS = (
    r"\b(?:change|alter|modify|transform|redraw|retouch|edit)\b.{0,48}\b(?:identity|face|hair|pose|body|background|lighting|shadow|camera|composition|aspect)\b",
    r"\b(?:reshape|slim|enlarge|lengthen|shorten)\b.{0,24}\b(?:body|waist|leg|arm|face|figure)\b",
    r"\b(?:add|remove|delete|insert)\b.{0,48}\b(?:background|object|logo|product detail|seam|strap|opening)\b",
)

_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}

IMMUTABLE_EDIT_CONTRACT = (
    "STRICT EDIT CONTRACT: Use the first reference image as the fixed adult model "
    "source and every later reference only as garment or outfit product reference. "
    "Edit only the clothing pixels on the model. Preserve the model's identity, "
    "face, hair, expression, skin tone, hands, feet, body proportions, pose, and "
    "anatomy. Preserve the complete background, scene, objects, lighting, shadows, "
    "camera, lens, viewpoint, framing, crop, and aspect ratio exactly. Do not add, "
    "remove, redraw, or replace any non-clothing object. Treat all later images as "
    "additional views of the same garment, never as a second person or scene. "
    "Reproduce the garment's visible color, pattern, silhouette, material, texture, "
    "panels, seams, edges, straps, openings, hardware, and existing logos without "
    "invention or alteration. Do not change product details, body shape, or composition."
)

# ``gpt-image-2`` follows the first, highest-priority image-edit instruction more
# reliably than a long creative brief. Keep source binding and the edit mask at
# the top, then describe fidelity evidence, and only lastly state output format.
# References remain the only variable input so free-form generation briefs cannot
# widen the edit.
GPT_IMAGE_2_OUTFIT_PROMPT = (
    "GPT-IMAGE-2 IMAGE EDIT | CLOTHING-ONLY VIRTUAL TRY-ON | ONE IMAGE OUTPUT\n"
    "SOURCE BINDING (highest priority, positional): Use the first reference image "
    "as the fixed adult model master and treat every later reference only as garment "
    "for the same outfit. Later references are flat-lay, product, detail, or alternate "
    "views of one garment; never people, faces, bodies, or backgrounds.\n\n"
    "EDIT MASK (mandatory): Replace only the clothing pixels on the model. Do not "
    "regenerate the full scene. Preserve the model's identity, face, hair, expression, "
    "skin tone, hands, feet, anatomy, body proportions, pose, and every non-clothing "
    "pixel. Preserve the background, objects, lighting, shadows, camera, lens, "
    "viewpoint, framing, crop, and aspect ratio exactly.\n\n"
    "GARMENT SOURCE OF TRUTH: Treat the selected garment references as production "
    "evidence, not inspiration. Reconstruct the same garment with zero tolerated "
    "deviation in material identity, fiber character, weave/knit direction, texture "
    "scale, thickness, transparency, stretch, compression, weight, stiffness, sheen, "
    "finish, color, print, and pattern. Preserve the garment's real drape, tension, "
    "compression, folds, wrinkles, edge roll, and body occlusion.\n\n"
    "CONSTRUCTION FIDELITY: Reproduce every visible panel boundary, cut line, seam "
    "position and type, stitch lines and spacing, topstitching, overlock, binding, "
    "hems, bartacks, elastic channels, closures, hardware, straps, openings, lining, "
    "cups, padding, boning, labels, and logos. Do not smooth, beautify, recolor, "
    "simplify, stylize, redesign, or substitute a similar fabric. If a detail is "
    "hidden or ambiguous, do not invent it. Do not change product details.\n\n"
    "FIT AND OUTPUT: Fit the sourced garment naturally to the unchanged model with "
    "correct scale, drape, seams, openings, shadows, and contact. Return exactly one "
    "clean image with the same framing, viewpoint, crop, and aspect ratio as the model "
    "master. No new model, scene, person, garment, exposed replacement body, floating "
    "fabric, caption, text, border, or watermark.\n\n"
    "STRICT EDIT CONTRACT: Preserve the model and scene; edit only clothing. Preserve "
    "all garment material, fabric behavior, color, pattern, construction, and product "
    "details. Reject any result with guessed fabric, missing stitch or component, "
    "altered pattern, wrong texture direction, wrong sheen, wrong thickness, wrong "
    "stretch/compression, or simplified construction. Compare the clothing region "
    "against every garment reference at both full-garment and close-up scale before "
    "returning the one image.\n\n"
    f"{IMMUTABLE_EDIT_CONTRACT}"
)

# Seedream accepts the same ordered references through Ark's JSON image field,
# but its prompt parser is more reliable when the source binding and display
# intent are stated as a compact product-edit brief rather than GPT's edit API
# terminology. Keep this contract separate from the GPT Image 2 prompt.
SEEDREAM_OUTFIT_PROMPT = (
    "SEEDREAM IMAGE EDIT | SOURCE-LOCKED CLOTHING REPLACEMENT | ONE IMAGE OUTPUT\n"
    "REFERENCE ORDER (mandatory): Image 1 is the fixed adult model master. Images 2+ "
    "are additional views or close-ups of the exact same garment. Never treat garment "
    "references as people, faces, bodies, or scenes.\n\n"
    "EDIT SCOPE (mandatory): Change only the clothing region on the unchanged model. "
    "Keep identity, face, hair, expression, skin, hands, feet, anatomy, body proportions, "
    "pose, background, objects, lighting, shadows, camera, framing, crop, and aspect ratio. "
    "Do not regenerate or redesign the scene.\n\n"
    "GARMENT FIDELITY (highest priority): Reproduce the selected garment as production "
    "evidence, not inspiration. Match its exact color, pattern, silhouette, fiber/material "
    "identity, weave or knit direction, texture scale, thickness, opacity, stretch, "
    "compression, sheen, drape, panels, seams, stitch spacing, hems, bindings, openings, "
    "straps, hardware, lining, padding, labels, logos, and finish. Preserve folds, tension, "
    "edge roll, and contact shadows. Do not recolor, smooth, beautify, simplify, stylize, "
    "substitute, add, remove, or invent hidden details.\n\n"
    "PRESENTATION: Fit the sourced garment naturally to the unchanged model at the same "
    "scale and viewpoint. Return exactly one clean photorealistic image with all visible "
    "fabric and construction details readable. No extra people, garments, props, text, "
    "border, or watermark. Reject any result that changes product details or the model/scene.\n\n"
    f"{IMMUTABLE_EDIT_CONTRACT}"
)

OUTFIT_IMAGE_PROVIDERS = frozenset({"hermes", "hermes_volcano"})


def outfit_prompt_for_provider(provider: str | None) -> str:
    """Return the immutable prompt contract for one supported image provider."""
    normalized = (provider or "hermes").strip().lower()
    if normalized == "liblib":
        normalized = "hermes_volcano"
    if normalized == "hermes":
        return GPT_IMAGE_2_OUTFIT_PROMPT
    if normalized == "hermes_volcano":
        return SEEDREAM_OUTFIT_PROMPT
    raise OutfitSwapRequestError("model outfit swap supports Hermes and Hermes Volcano providers")


# Public compatibility name used by the API, harness, and existing callers.
FIXED_OUTFIT_PROMPT = GPT_IMAGE_2_OUTFIT_PROMPT


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
        raise OutfitSwapRequestError("request contains unsupported minor, explicit, or body-shaming content")
    # Split clauses first so a preceding "preserve" cannot authorize a later
    # forbidden edit (for example: "Preserve the scene. Change the identity").
    clauses = re.split(r"[.!?;\n]+", text)
    for clause in clauses:
        stripped = clause.strip()
        if not stripped:
            continue
        if any(re.search(pattern, stripped, flags=re.IGNORECASE) for pattern in _OVERRIDE_PATTERNS):
            raise OutfitSwapRequestError("request may only change the model's clothing")


def _validate_image_reference(value: str, field: str) -> str:
    """Accept local image files or public HTTPS image URLs only."""
    candidate = value.strip()
    is_windows_path = bool(re.match(r"^[A-Za-z]:[\\/]", candidate)) or candidate.startswith(("\\\\", "//"))
    parsed = urlsplit(candidate) if not is_windows_path else None
    if parsed is not None and parsed.scheme:
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
            raise OutfitSwapRequestError(f"{field} must be a local image or public HTTPS image URL")
        hostname = (parsed.hostname or "").lower()
        if hostname == "localhost":
            raise OutfitSwapRequestError(f"{field} must use a public HTTPS image URL")
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            pass
        else:
            if not address.is_global:
                raise OutfitSwapRequestError(f"{field} must use a public HTTPS image URL")
        image_path = parsed.path
    else:
        image_path = candidate
    if Path(image_path).suffix.lower() not in _IMAGE_EXTENSIONS:
        raise OutfitSwapRequestError(f"{field} must reference an image file")
    return candidate


def _image_dimensions(path: str) -> tuple[int, int] | None:
    """Read common image dimensions without adding a Pillow dependency."""
    candidate = Path(path)
    try:
        with candidate.open("rb") as handle:
            header = handle.read(64)
            if header.startswith(b"\x89PNG\r\n\x1a\n") and len(header) >= 24:
                return struct.unpack(">II", header[16:24])
            if header[:6] in {b"GIF87a", b"GIF89a"} and len(header) >= 10:
                return struct.unpack("<HH", header[6:10])
            if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
                if header[12:16] == b"VP8X" and len(header) >= 30:
                    return (
                        1 + int.from_bytes(header[24:27], "little"),
                        1 + int.from_bytes(header[27:30], "little"),
                    )
                if header[12:16] == b"VP8 " and len(header) >= 30 and header[23:26] == b"\x9d\x01\x2a":
                    return struct.unpack("<HH", header[26:30])
            if header[:2] == b"\xff\xd8":
                handle.seek(2)
                while True:
                    marker_prefix = handle.read(1)
                    if not marker_prefix:
                        break
                    if marker_prefix != b"\xff":
                        continue
                    marker = handle.read(1)
                    while marker == b"\xff":
                        marker = handle.read(1)
                    if not marker or marker in {b"\xd8", b"\xd9"}:
                        continue
                    length_data = handle.read(2)
                    if len(length_data) != 2:
                        break
                    length = struct.unpack(">H", length_data)[0]
                    if 0xC0 <= marker[0] <= 0xC3 or 0xC5 <= marker[0] <= 0xC7 or 0xC9 <= marker[0] <= 0xCB or 0xCD <= marker[0] <= 0xCF:
                        dimensions = handle.read(5)
                        if len(dimensions) == 5:
                            return struct.unpack(">HH", dimensions[1:5])[::-1]
                    if length < 2:
                        break
                    handle.seek(length - 2, 1)
    except (OSError, struct.error, ValueError):
        return None
    return None


def _source_aspect_ratio(model_image: str) -> str:
    """Map the model master image to the provider's supported aspect names."""
    dimensions = _image_dimensions(model_image)
    if not dimensions or not all(dimensions):
        # Most fashion model masters are portrait. This also avoids making a
        # remote URL request just to inspect dimensions before generation.
        return "portrait"
    width, height = dimensions
    ratio = width / height
    if ratio >= 1.08:
        return "landscape"
    if ratio <= 0.92:
        return "portrait"
    return "square"


def _load_workflow() -> dict[str, Any]:
    path = SKILL_ROOT / "workflows" / "model_outfit_swap.json"
    try:
        workflow = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"could not load model outfit swap workflow: {path}") from error
    if not isinstance(workflow, dict) or workflow.get("type") != "image":
        raise RuntimeError(f"model outfit swap workflow must be an image object: {path}")
    return workflow


def _non_empty_text(value: Any, field: str, *, required: bool = True) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        qualifier = "must be a non-empty string" if required else "must be a non-empty string when provided"
        raise OutfitSwapRequestError(f"{field} {qualifier}")
    return value.strip()


def _normalise_images(value: Any, field: str) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise OutfitSwapRequestError(f"{field} must be a list of image paths or URLs")
    images: list[str] = []
    for index, item in enumerate(value):
        if isinstance(item, Mapping):
            # Accept API-shaped references while retaining only their value.
            item = item.get("value")
        if not isinstance(item, str) or not item.strip():
            raise OutfitSwapRequestError(f"{field}[{index}] must be a non-empty image path or URL")
        images.append(_validate_image_reference(item, f"{field}[{index}]"))
    return images


def _request_parts(
    request: Mapping[str, Any] | str,
    *,
    model_image: str | None,
    outfit_images: Sequence[str] | None,
    reference_images: Sequence[str] | None,
) -> tuple[str, str, list[str], dict[str, Any]]:
    if isinstance(request, Mapping):
        body = dict(request)
    elif isinstance(request, str):
        body = {"prompt": request}
    else:
        raise OutfitSwapRequestError("request must be a mapping or prompt string")
    _guard_text(body)

    # Explicit keyword arguments take precedence over request fields.
    model_value = model_image if model_image is not None else body.get("model_image", body.get("model_asset"))
    outfit_value = outfit_images if outfit_images is not None else body.get("outfit_images")
    reference_value = reference_images if reference_images is not None else body.get("reference_images")
    if model_value is None and outfit_value is None and reference_value is not None:
        ordered = _normalise_images(reference_value, "reference_images")
        if not ordered:
            raise OutfitSwapRequestError("model outfit swap requires at least two reference images")
        model_value, outfit_value = ordered[0], ordered[1:]
    model = _non_empty_text(model_value, "model_image")
    model = _validate_image_reference(model, "model_image")
    if outfit_value is None:
        if reference_value is None:
            raise OutfitSwapRequestError("outfit_images must include at least one image")
        ordered = _normalise_images(reference_value, "reference_images")
        # A full ordered reference list may include the model as its first item.
        outfit_value = ordered[1:] if ordered and ordered[0] == model else ordered
    outfits = _normalise_images(outfit_value, "outfit_images")
    all_references = [model, *outfits]
    if len(all_references) < 2:
        raise OutfitSwapRequestError("model outfit swap requires at least two reference images")
    if len(all_references) > MAX_REFERENCE_IMAGES:
        raise OutfitSwapRequestError(
            f"model outfit swap supports at most {MAX_REFERENCE_IMAGES} reference images"
        )
    if len(set(all_references)) != len(all_references):
        raise OutfitSwapRequestError("reference images must be distinct")

    # User generation briefs are intentionally ignored. The guard above still
    # rejects unsafe direct calls, while every accepted request gets one exact
    # prompt regardless of product, scene, style, or prompt fields.
    # Provider-specific prompt selection happens in ``generate_image`` after
    # explicit options have been merged with the request body.
    prompt = FIXED_OUTFIT_PROMPT
    return prompt, model, outfits, body


def _write_prompt(prompt: str, output_root: Path) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "prompt.txt").write_text(prompt + "\n", encoding="utf-8")


def generate_image(
    request: Mapping[str, Any] | str,
    *,
    model_image: str | None = None,
    model_asset: str | None = None,
    outfit_images: Sequence[str] | None = None,
    reference_images: Sequence[str] | None = None,
    references: Sequence[str] | None = None,
    client: Any | None = None,
    output_dir: str | Path | None = None,
    **options: Any,
) -> dict[str, Any]:
    """Generate an image while preserving a model and replacing clothing.

    The first reference is the model image; subsequent references are outfit or
    garment images. At least two references are required. A client can be
    injected for deterministic tests.
    """
    if model_image is None:
        model_image = model_asset
    if outfit_images is None and references is not None:
        outfit_images = references
    prompt, model, outfits, body = _request_parts(
        request,
        model_image=model_image,
        outfit_images=outfit_images,
        reference_images=reference_images,
    )
    root = Path(output_dir) if output_dir is not None else OUTPUT_ROOT
    workflow = _load_workflow()
    provider = options.pop("provider", body.get("provider", workflow.get("provider")))
    if not isinstance(provider, str) or not provider.strip():
        provider = "hermes"
    provider = provider.strip().lower()
    if provider == "liblib":
        provider = "hermes_volcano"
    if provider not in OUTFIT_IMAGE_PROVIDERS:
        raise OutfitSwapRequestError("model outfit swap supports Hermes and Hermes Volcano providers")
    # ``provider`` can be supplied as an option even when it was absent from the
    # request body, so resolve the final prompt after normalising that option.
    prompt = outfit_prompt_for_provider(provider)
    _write_prompt(prompt, root)
    aspect_ratio = _source_aspect_ratio(model)
    path = engine_generate_image(
        prompt,
        client=client,
        output_dir=root,
        image=model,
        references=outfits,
        provider=provider,
        aspect_ratio=aspect_ratio,
        **options,
    )
    quality = check_artifact(path, "image")
    quality["aspect_ratio"] = aspect_ratio
    quality["manual_review"] = list(quality.get("manual_review", []))
    return {
        "skill": "model-outfit-swap",
        "workflow": workflow.get("name", "model_outfit_swap"),
        "type": "image",
        "provider": provider,
        "prompt": prompt,
        "references": [model, *outfits],
        "reference_roles": ["model", *(["outfit"] * len(outfits))],
        "model_reference": model,
        "outfit_references": list(outfits),
        "aspect_ratio": aspect_ratio,
        "garment_fidelity": garment_fidelity_contract(),
        "outputs": [path],
        "quality": quality,
    }


ModelOutfitSwapRequestError = OutfitSwapRequestError

__all__ = [
    "GPT_IMAGE_2_OUTFIT_PROMPT",
    "SEEDREAM_OUTFIT_PROMPT",
    "OUTFIT_IMAGE_PROVIDERS",
    "FIXED_OUTFIT_PROMPT",
    "GARMENT_FIDELITY_CHECKS",
    "IMMUTABLE_EDIT_CONTRACT",
    "MAX_REFERENCE_IMAGES",
    "_source_aspect_ratio",
    "OutfitSwapRequestError",
    "ModelOutfitSwapRequestError",
    "outfit_prompt_for_provider",
    "garment_fidelity_contract",
    "generate_image",
]
