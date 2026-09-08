"""Herdr runtime bridge for the Shapewear Video Generator skill."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from main import generate_image as engine_generate_image
from main import generate_video as engine_generate_video
from skills.shapewear_video_generator.quality_check import check_artifact, image_fidelity_contract


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILL_ROOT = PROJECT_ROOT / "skills" / "shapewear-video-generator"
OUTPUT_ROOT = PROJECT_ROOT / "outputs" / "shapewear"
# Hermes is the default; the second independent Hermes/Volcano slot is also
# supported, while the old ``liblib`` name remains an input alias.
SHAPEWEAR_IMAGE_PROVIDER = "hermes"
SHAPEWEAR_IMAGE_PROVIDERS = frozenset({"hermes", "hermes_volcano", "liblib"})
MAX_IMAGE_REFERENCES = 10

# This suffix is intentionally provider-facing and immutable. A user brief may
# choose presentation, but it must not be able to remove the product fidelity
# and no-invention constraints required for catalog imagery.
IMAGE_REFERENCE_BINDING = (
    "PRODUCT SOURCE BINDING: If an image reference is attached, it is the exact "
    "shapewear product source and the highest-priority conditioning input. "
    "Reproduce that same product, not a generic interpretation. If no reference "
    "is attached, use only the declared attributes and do not invent hidden details."
)
IMAGE_FIDELITY_CONTRACT = (
    "IMMUTABLE SHAPEWEAR PRODUCT CONTRACT: Create one photorealistic commercial "
    "product image with exact visual-match intent (100% fidelity target) to the "
    "selected garment and declared attributes. Preserve silhouette, proportions, "
    "rise, neckline, armholes, leg openings, bust cups, straps, side and back "
    "panels, compression zones, gusset/crotch construction, closures, lining, "
    "padding, boning, logo, label, and hardware, only where shown or declared. "
    "Render the actual textile character: fiber blend, weave or knit direction, "
    "yarn or rib scale, mesh, denier, opacity, thickness, stretch, compression, "
    "recovery, sheen, nap, drape, and realistic tension lines. Preserve all "
    "manufacturing evidence: seam placement, stitch type and spacing, flatlock, "
    "coverstitch, overlock, bonded seam, binding, folded or laser-cut hem, elastic, "
    "silicone gripper, bartack, reinforcement, and edge roll. Do not recolor, "
    "redesign, simplify, beautify, smooth away, add, remove, or substitute any "
    "garment detail. Do not turn it into ordinary lingerie, swimwear, a generic "
    "bodysuit, or decorative fashion. Do not guess details hidden by the source. "
    "Keep adult models fully covered, anatomically correct, and naturally posed; "
    "do not alter body shape or imply medical, slimming, weight-loss, or health "
    "outcomes. No extra garments, props, text, watermark, or competitor branding "
    "that obscures the product. Final self-check: compare full-product silhouette, "
    "color, material, seams, edges, compression zones, and every visible component "
    "to the source before returning the image; unresolved semantic differences "
    "require manual review."
)
# ``gpt-image-2`` responds better to a short, front-loaded edit contract than
# to the Seedream-oriented creative block above. Keep this contract separate so
# the Hermes gpt-image-2 slot can prioritize the selected main product image.
GPT_IMAGE_2_REFERENCE_BINDING = (
    "GPT-IMAGE-2 SOURCE LOCK (highest priority): When an image is attached, its "
    "first image is the user-selected shapewear product master and the exact product "
    "to reproduce. It outranks the text brief, scene, style, lighting, and model "
    "suggestion. Any later attached images are additional views or macro details of "
    "that same product, never a different garment, person, or background."
)
GPT_IMAGE_2_IMAGE_FIDELITY_CONTRACT = (
    "IMMUTABLE SHAPEWEAR PRODUCT CONTRACT FOR GPT-IMAGE-2: Generate one "
    "photorealistic commerce image with a near-100% exact visual-match target to "
    "the selected main product image. Preserve the exact silhouette, proportions, "
    "rise, neckline, armholes, leg openings, cups, straps, side/back panels, "
    "compression zones, gusset/crotch, closures, lining, padding, boning, labels, "
    "logos, and hardware that are visible in the source. Preserve the actual textile "
    "identity and surface: fiber character, weave or knit direction, yarn/rib scale, "
    "mesh, denier, opacity, thickness, stretch, compression, recovery, sheen, nap, "
    "drape, folds, wrinkles, and tension lines. Preserve manufacturing evidence: "
    "seam placement, stitch type and spacing, flatlock, coverstitch, overlock, "
    "bonded seam, binding, folded or laser-cut hem, elastic, silicone gripper, "
    "bartack, reinforcement, and edge roll.\n\n"
    "Do not recolor, redesign, smooth, beautify, simplify, embellish, add, remove, "
    "or substitute any garment detail. Do not turn the product into ordinary lingerie, "
    "swimwear, a generic bodysuit, or decorative fashion. Do not guess hidden or "
    "ambiguous details. Do not reshape the body or make medical, slimming, or weight-loss "
    "claims. The scene, camera, lighting, and adult fully covered presentation are "
    "secondary display choices only and must never change the product. Keep the complete "
    "garment visible at a useful scale with fabric and construction readable. Before "
    "returning the image, compare color, silhouette, material, seams, edges, components, "
    "and every visible detail to the selected source; unresolved differences require "
    "manual review."
)
GPT_IMAGE_2_PRESENTATION_GUIDANCE = (
    "GPT-IMAGE-2 DISPLAY GUIDANCE (secondary to source lock): Choose presentation "
    "from the selected advertising template. For product_detail or luxury_fashion, "
    "prefer a product-only flat-lay or headless mannequin. For fashion_campaign, show "
    "one adult woman fully wearing the same garment as a high-end fashion campaign "
    "still; do not convert it into a packshot or a phone UGC selfie. For tiktok_ugc, "
    "show one adult woman fully wearing the same garment in a standing mirror/try-on "
    "still; do not convert UGC into a product-only packshot. Keep the model adult, "
    "fully covered, and naturally posed. Keep the complete garment centered and "
    "readable, with fabric texture and construction in focus; do not hide seams or "
    "edges behind blur, props, or dramatic shadow."
)
FORBIDDEN_PATTERNS = (
    r"\bminor\b",
    r"\b(child|children|kid|teen|teenager)\b",
    r"\b(nude|naked|porn|explicit sex|sexual act)\b",
    r"\b(body[- ]sham(e|ing)|hide your fat|fix your body|hate your body)\b",
    r"\b(guaranteed|cure|treat|prevent|doctor[- ]approved|medical[- ]grade|health benefit|pain relief)\b",
    r"\b(lose|lose\s+)\d+\s*(kg|pounds|lb)\b",
    r"\b(official(?:ly)?\s+)?(sponsored|affiliated|endorsed)\s+(?:campaign|by|with|partnership)?\s*(?:by\s+)?(?:SKIMS|SPANX)\b",
    r"\b(SKIMS|SPANX)\s+(campaign|sponsored|official|affiliated|endorsed)\b",
)
GARMENT_OVERRIDE_PATTERNS = (
    r"\b(?:recolor|re-colou?r|dye|change|alter|modify|redesign|replace|add|remove|delete|invent)\b.{0,56}\b(?:garment|shapewear|clothing|outfit|color|colour|hue|pattern|print|logo|seam|fabric|material|detail|construction)\b",
    r"\b(?:different|new|another)\s+(?:color|colour|hue|pattern|print|fabric|material|garment|outfit)\b",
)


class ShapewearRequestError(ValueError):
    """Raised when a request violates the skill's safety or type contract."""


def _load_workflow(name: str) -> dict[str, Any]:
    path = SKILL_ROOT / "workflows" / name
    try:
        workflow = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"could not load Shapewear workflow: {path}") from error
    if not isinstance(workflow, dict):
        raise RuntimeError(f"Shapewear workflow must be an object: {path}")
    if workflow.get("name") == "shapewear_image":
        if workflow.get("provider") != SHAPEWEAR_IMAGE_PROVIDER:
            raise RuntimeError("shapewear image workflow must keep Hermes as its default provider")
        if workflow.get("provider_locked") is True:
            raise RuntimeError("shapewear image workflow provider must remain selectable")
        configured_providers = workflow.get("providers")
        if not isinstance(configured_providers, list) or set(configured_providers) != {"hermes", "hermes_volcano"}:
            raise RuntimeError("shapewear image workflow must declare Hermes and Hermes Volcano providers")
    for resource in workflow.get("resources", []):
        if not (SKILL_ROOT / resource).is_file():
            raise RuntimeError(f"missing Shapewear resource: {resource}")
    return workflow


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


def _request_text(request: Mapping[str, Any] | str) -> str:
    return " ".join(_iter_strings(request))


def _guard_request(request: Mapping[str, Any] | str) -> None:
    text = _request_text(request).lower()
    for pattern in FORBIDDEN_PATTERNS:
        if re.search(pattern, text, flags=re.IGNORECASE):
            raise ShapewearRequestError("request contains unsupported sexual, minor, medical, or affiliation content")
    if isinstance(request, str):
        presentation_text = request
    elif isinstance(request, Mapping):
        presentation_text = " ".join(
            candidate
            for key in ("prompt", "objective", "scene", "style")
            for candidate in _iter_strings(request.get(key))
        )
    else:
        presentation_text = ""
    for pattern in GARMENT_OVERRIDE_PATTERNS:
        if re.search(pattern, presentation_text, flags=re.IGNORECASE):
            raise ShapewearRequestError("shapewear product details cannot be recolored, redesigned, added, or removed")


def _value(request: Mapping[str, Any] | str, key: str, default: str) -> str:
    if isinstance(request, Mapping):
        candidate = request.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return default


_VIDEO_CLIP_DEFAULTS = {
    "luxury_brand": ("8", "9:16", "1080x1920"),
    "fashion_campaign": ("8", "9:16", "1080x1920"),
    "tiktok_ugc": ("8", "9:16", "1080x1920"),
    "product_detail": ("5", "9:16", "1080x1920"),
}


def _clip_field(request: Mapping[str, Any] | str, key: str, default: str) -> str:
    if isinstance(request, Mapping):
        candidate = request.get(key)
        if isinstance(candidate, bool):
            return default
        if isinstance(candidate, int) and not isinstance(candidate, bool):
            return str(candidate)
        if isinstance(candidate, float) and candidate.is_integer():
            return str(int(candidate))
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return default


def _clip_replacements(request: Mapping[str, Any] | str, block_id: str) -> dict[str, str]:
    duration_default, aspect_default, resolution_default = _VIDEO_CLIP_DEFAULTS.get(
        block_id, ("8", "9:16", "1080x1920")
    )
    aspect_ratio = _clip_field(request, "aspect_ratio", aspect_default)
    resolution = _clip_field(request, "resolution", "")
    if not resolution:
        resolution = {
            "9:16": "1080x1920",
            "16:9": "1920x1080",
            "1:1": "1080x1080",
        }.get(aspect_ratio, resolution_default)
    return {
        "duration_seconds": _clip_field(request, "duration_seconds", duration_default),
        "aspect_ratio": aspect_ratio,
        "resolution": resolution,
    }


def _style_id(request: Mapping[str, Any] | str, media_type: str) -> str:
    block_id = "luxury_fashion" if media_type == "image" else "luxury_brand"
    if isinstance(request, Mapping):
        explicit = request.get("style_id")
        if isinstance(explicit, str) and explicit.strip():
            block_id = explicit.strip()
        else:
            style = str(request.get("style", "")).lower()
            if "ugc" in style or "tiktok" in style or "customer" in style:
                block_id = "tiktok_ugc"
            elif "detail" in style or "macro" in style or "fabric" in style:
                block_id = "product_detail"
            elif "campaign" in style or "editorial" in style or "服装广告" in style:
                block_id = "fashion_campaign"
    if media_type != "image":
        return {
            "luxury_fashion": "luxury_brand",
            "product_detail": "product_detail",
            "fashion_campaign": "fashion_campaign",
            "tiktok_ugc": "tiktok_ugc",
            "luxury_brand": "luxury_brand",
        }.get(block_id, "luxury_brand")
    return block_id


def _prompt_block(filename: str, block_id: str) -> str:
    text = (SKILL_ROOT / "prompts" / filename).read_text(encoding="utf-8")
    marker = f"  - id: {block_id}"
    start = text.find(marker)
    if start < 0:
        raise RuntimeError(f"missing prompt block {block_id} in {filename}")
    prompt_start = text.find("    prompt: >-", start)
    if prompt_start < 0:
        raise RuntimeError(f"prompt block {block_id} has no prompt text")
    lines = []
    for line in text[prompt_start:].splitlines()[1:]:
        if line.startswith("      "):
            lines.append(line.strip())
        elif lines:
            break
    return " ".join(lines)


def _build_prompt(
    request: Mapping[str, Any] | str,
    media_type: str,
    *,
    provider: str | None = None,
) -> str:
    _guard_request(request)
    if isinstance(request, str):
        if media_type != "image":
            return request.strip()
        body: Mapping[str, Any] = {"prompt": request}
    else:
        body = request
        direct_prompt = body.get("prompt")
        if media_type != "image" and isinstance(direct_prompt, str) and direct_prompt.strip():
            return direct_prompt.strip()
    if media_type == "image":
        block_id = _style_id(request, media_type)
        template = _prompt_block("image_prompts.yaml", block_id)
    else:
        block_id = _style_id(request, media_type)
        template = _prompt_block("video_prompts.yaml", block_id)
    replacements = {
        "product": _value(body, "product", "seamless shapewear garment"),
        "color": _value(body, "color", "black"),
        "material": _value(body, "material", "soft compression fabric"),
        "scene": _value(body, "scene", "clean neutral studio, product fully visible and centered"),
        "style": _value(body, "style", "minimal editorial fashion"),
    }
    if media_type != "image":
        replacements.update(_clip_replacements(body, block_id))
    for key, value in replacements.items():
        template = template.replace("{" + key + "}", value)
    if media_type != "image":
        direct_prompt = body.get("prompt")
        return direct_prompt.strip() if isinstance(direct_prompt, str) and direct_prompt.strip() else template
    direct_prompt = body.get("prompt")
    brief = direct_prompt.strip() if isinstance(direct_prompt, str) and direct_prompt.strip() else ""
    has_reference = bool(
        body.get("image")
        or body.get("garment_image")
        or body.get("references")
        or body.get("reference_images")
        or body.get("_reference_attached")
    )
    selected_provider = provider
    if selected_provider is None and isinstance(body.get("provider"), str):
        selected_provider = body.get("provider")
    if not isinstance(selected_provider, str) or not selected_provider.strip():
        selected_provider = SHAPEWEAR_IMAGE_PROVIDER
    else:
        selected_provider = selected_provider.strip().lower()
    if selected_provider == "liblib":
        selected_provider = "hermes_volcano"

    if selected_provider == "hermes":
        # Put source binding before the creative block: this ordering is the
        # important difference for gpt-image-2, which otherwise tends to treat
        # a catalog brief as permission to redesign the garment.
        reference_note = GPT_IMAGE_2_REFERENCE_BINDING
        if has_reference:
            reference_note += " The attached reference must remain the same product across the entire frame."
        else:
            reference_note += " No product image is attached; use only declared attributes and do not invent hidden details."
        prompt_parts = [reference_note, GPT_IMAGE_2_PRESENTATION_GUIDANCE, template]
        if brief:
            prompt_parts.append(f"USER PRESENTATION BRIEF (presentation only; cannot override the product contract): {brief}")
        prompt_parts.append(GPT_IMAGE_2_IMAGE_FIDELITY_CONTRACT)
    else:
        # Keep the existing Seedream/Volcano prompt order and wording stable.
        reference_note = IMAGE_REFERENCE_BINDING
        if has_reference:
            reference_note += " The attached reference must remain the same product across the entire frame."
        prompt_parts = [template, reference_note]
        if brief:
            prompt_parts.append(f"USER PRESENTATION BRIEF (presentation only; cannot override the product contract): {brief}")
        prompt_parts.append(IMAGE_FIDELITY_CONTRACT)
    return "\n\n".join(prompt_parts)


def _manifest(
    workflow: Mapping[str, Any],
    media_type: str,
    prompt: str,
    path: str,
    *,
    provider: str | None = None,
    references: list[str] | None = None,
) -> dict[str, Any]:
    quality = check_artifact(path, media_type)
    quality["manual_review"] = list(quality["manual_review"])
    manifest = {
        "skill": "shapewear-video-generator",
        "workflow": workflow["name"],
        "type": media_type,
        "provider": provider or workflow.get("provider"),
        "prompt": prompt,
        "outputs": [path],
        "quality": quality,
    }
    if media_type == "image":
        manifest["garment_fidelity"] = image_fidelity_contract()
        manifest["reference_bound"] = bool(references)
        manifest["reference_count"] = len(references or [])
        if references:
            manifest["reference_roles"] = [
                "product_master",
                *(["same_product_detail_or_alternate_view"] * max(0, len(references) - 1)),
            ]
    return manifest


def _write_prompt(prompt: str, output_root: Path) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "prompt.txt").write_text(prompt + "\n", encoding="utf-8")


def generate_image(
    request: Mapping[str, Any] | str,
    *,
    client: Any | None = None,
    output_dir: str | Path | None = None,
    **options: Any,
) -> dict[str, Any]:
    """Run the Shapewear image workflow and return its manifest."""
    _guard_request(request)
    workflow = _load_workflow("shapewear_image.json")
    provider_candidates = []
    request_provider = request.get("provider") if isinstance(request, Mapping) else None
    option_provider = options.get("provider")
    if request_provider is not None:
        provider_candidates.append(request_provider)
    if option_provider is not None:
        provider_candidates.append(option_provider)
    normalized_candidates = []
    for candidate in provider_candidates:
        if not isinstance(candidate, str) or candidate.strip().lower() not in SHAPEWEAR_IMAGE_PROVIDERS:
            raise ShapewearRequestError("shapewear product images require the Hermes or Hermes Volcano image provider")
        normalized = candidate.strip().lower()
        normalized_candidates.append("hermes_volcano" if normalized == "liblib" else normalized)
    if len(set(normalized_candidates)) > 1:
        raise ShapewearRequestError("shapewear image provider values must match")
    selected_provider = normalized_candidates[0] if normalized_candidates else SHAPEWEAR_IMAGE_PROVIDER
    options["provider"] = selected_provider
    if isinstance(request, Mapping):
        if request.get("image") is not None:
            options.setdefault("image", request.get("image"))
        if request.get("references") is not None:
            options.setdefault("references", request.get("references"))
        if request.get("reference_images") is not None:
            options.setdefault("references", request.get("reference_images"))
    if options.get("references") is not None and not isinstance(options.get("references"), (list, tuple)):
        raise ShapewearRequestError("shapewear references must be an ordered image list")
    reference_count = len(options.get("references") or []) + (1 if options.get("image") else 0)
    if reference_count > MAX_IMAGE_REFERENCES:
        raise ShapewearRequestError(
            f"shapewear product images support at most {MAX_IMAGE_REFERENCES} ordered reference images"
        )
    prompt_request: Mapping[str, Any] | str = request
    if options.get("image") or options.get("references"):
        # Carry only a boolean into prompt construction; never put local paths
        # or remote URLs into the provider prompt or persisted prompt.txt.
        if isinstance(request, Mapping):
            prompt_request = dict(request)
        else:
            prompt_request = {"prompt": request}
        prompt_request["_reference_attached"] = True
    prompt = _build_prompt(prompt_request, "image", provider=selected_provider)
    output_root = Path(output_dir) if output_dir is not None else OUTPUT_ROOT
    _write_prompt(prompt, output_root)
    path = engine_generate_image(prompt, client=client, output_dir=output_root, **options)
    references = options.get("references")
    reference_list = list(references) if isinstance(references, (list, tuple)) else ([] if references is None else [str(references)])
    if options.get("image"):
        reference_list.insert(0, str(options["image"]))
    return _manifest(workflow, "image", prompt, path, provider=selected_provider, references=reference_list)


def generate_video(
    request: Mapping[str, Any] | str,
    *,
    image: str | None = None,
    client: Any | None = None,
    output_dir: str | Path | None = None,
    **options: Any,
) -> dict[str, Any]:
    """Run the Shapewear video workflow and return its manifest."""
    _guard_request(request)
    workflow = _load_workflow("shapewear_video.json")
    prompt = _build_prompt(request, "video")
    provider = request.get("provider") if isinstance(request, Mapping) else None
    if provider is not None and (not isinstance(provider, str) or provider.strip().lower() not in {"veo", "seedance"}):
        raise ShapewearRequestError("provider must be veo or seedance")
    provider = provider.strip().lower() if isinstance(provider, str) else None
    if image is None and isinstance(request, Mapping):
        candidate = request.get("image")
        if isinstance(candidate, str) and candidate.strip():
            image = candidate.strip()
    resume_id = options.get("resume_task_id")
    if image is None and not resume_id:
        image_request: Mapping[str, Any] | str = request
        image_client = client
        image_options = dict(options)
        image_options.pop("resume_task_id", None)
        image_options.pop("on_task_submitted", None)
        if provider == "seedance":
            # Seedance is video-only here; generate its local keyframe through
            # the configured Hermes image provider before uploading it.
            if isinstance(request, Mapping):
                image_request = dict(request)
                image_request["provider"] = "hermes"
            image_options["provider"] = "hermes"
            image_client = None
        image_manifest = generate_image(
            image_request,
            client=image_client,
            output_dir=output_dir,
            **image_options,
        )
        image = image_manifest["outputs"][0]
    output_root = Path(output_dir) if output_dir is not None else OUTPUT_ROOT
    _write_prompt(prompt, output_root)
    if provider is not None:
        options.setdefault("provider", provider)
    path = engine_generate_video(prompt, image=image, client=client, output_dir=output_root, **options)
    return _manifest(workflow, "video", prompt, path, provider=provider or workflow.get("provider"))
