"""Runtime for source-locked TikTok clothing product images."""

from __future__ import annotations

import ipaddress
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from main import generate_image as engine_generate_image
from .quality_check import check_artifact

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILL_ROOT = PROJECT_ROOT / "skills" / "tiktok-clothing-main-image"
OUTPUT_ROOT = PROJECT_ROOT / "outputs" / "tiktok_clothing"
TIKTOK_CLOTHING_PROVIDER = "hermes"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
MAX_REFERENCES = 10
PURPOSES = frozenset({"shop_listing", "photo_carousel", "video_cover", "ugc_variant"})
STYLES = frozenset({"studio_detail", "creator_ugc"})
ASPECT_RATIOS = frozenset({"9:16", "portrait", "square", "landscape"})
PRESENTATION_MODES = frozenset({"product_only", "adult_model_fully_covered"})
DEFAULT_MARKET = "US"
DEFAULT_LOCALE = "en-US"
PROMPT_VERSION = "tiktok-clothing-main-image/v1.1"


class TikTokClothingRequestError(ValueError):
    """Raised when a TikTok clothing image request is invalid or unsafe."""


SOURCE_LOCK = (
    "TIKTOK PRODUCT SOURCE LOCK (highest priority): The first attached image is "
    "the exact product master for sale. Later attached images, when present, are "
    "additional views or macro details of that same product. Use the images as "
    "the source of truth, not as inspiration. Never substitute a generic garment."
)
PRESENTATION_GUIDANCE = (
    "PRESENTATION ONLY: Create a clear commercial TikTok image. Keep the complete "
    "garment centered inside a 9:16-safe vertical frame with enough margin for UI. "
    "Prefer a product-only flat lay, hanger, or mannequin. A requested model must "
    "be an adult, fully covered, naturally posed, and secondary to the product. "
    "Use neutral light and sharp focus so fabric, seams, edges, straps, closures, "
    "lining, labels, and functional construction remain inspectable."
)
IMMUTABLE_PRODUCT_CONTRACT = (
    "IMMUTABLE GARMENT CONTRACT: Preserve the exact source garment's color, hue, "
    "tone, pattern, print, logo, label, silhouette, proportions, cut, panels, "
    "seams, stitch placement and spacing, hems, edges, straps, openings, closures, "
    "hardware, fiber character, weave or knit direction, mesh, opacity, thickness, "
    "stretch, sheen, nap, drape, folds, and visible finish. Do not recolor, "
    "redesign, simplify, beautify, smooth away, add, remove, relabel, or invent "
    "any garment detail. Do not guess hidden or occluded areas. Only the scene, "
    "camera, lighting, and presentation may change."
)
COPY_CONTRACT = (
    "COPY RULE: Render no text unless factual copy is explicitly supplied in the "
    "request. Any copy must be short, legible, and limited to supplied material, "
    "size, color, or care facts. Do not add prices, discounts, urgency, QR codes, "
    "URLs, platform logos, fake reviews, before/after body changes, medical, "
    "slimming, weight-loss, certification, or unsupported performance claims."
)
FORBIDDEN_PATTERNS = (
    r"\b(?:minor|child|children|kid|teen|teenager|schoolgirl|schoolboy)\b",
    r"\b(?:nude|naked|porn|explicit sex|sexual act|fetish|lingerie fetish)\b",
    r"\b(?:body[- ]sham(?:e|ing)|hide your fat|fix your body|hate your body)\b",
    r"\b(?:guaranteed|cure|treat|prevent|doctor[- ]approved|medical[- ]grade|clinically proven|pain relief|weight loss|burn fat|slimming result)\b",
    r"\b(?:fake[^.?!\n]{0,40}review|five[- ]star review|best seller|trending|limited time|buy now|discount|sale price)\b",
)
OVERRIDE_PATTERNS = (
    r"\b(?:recolor|re-colou?r|dye)\b",
    r"\b(?:recolor|re-colou?r|dye|change|alter|modify|redesign|replace|add|remove|delete|invent|make it)\b.{0,70}\b(?:garment|clothing|outfit|dress|shirt|top|bottom|color|colour|hue|pattern|print|logo|seam|fabric|material|detail|construction)\b",
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
    if any(re.search(pattern, text, re.IGNORECASE | re.VERBOSE) for pattern in FORBIDDEN_PATTERNS):
        raise TikTokClothingRequestError("request contains unsupported sexual, minor, body, medical, or promotional content")
    for pattern in OVERRIDE_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE | re.VERBOSE):
            raise TikTokClothingRequestError("the product master is immutable; only presentation may change")


def _validate_reference(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TikTokClothingRequestError(f"{field} must be a non-empty image path or URL")
    candidate = value.strip()
    windows_path = bool(re.match(r"^[A-Za-z]:[\\/]", candidate)) or candidate.startswith(("\\\\", "//"))
    parsed = urlsplit(candidate) if not windows_path else None
    if parsed is not None and parsed.scheme:
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise TikTokClothingRequestError(f"{field} must be a public HTTPS image URL")
        hostname = (parsed.hostname or "").lower().rstrip(".")
        if hostname in {"localhost", "127.0.0.1", "::1"} or hostname.endswith((".local", ".internal")):
            raise TikTokClothingRequestError(f"{field} must be a public HTTPS image URL")
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            pass
        else:
            if not address.is_global:
                raise TikTokClothingRequestError(f"{field} must be a public HTTPS image URL")
        suffix = Path(parsed.path).suffix.lower()
    else:
        suffix = Path(candidate).suffix.lower()
    if suffix not in IMAGE_EXTENSIONS:
        raise TikTokClothingRequestError(f"{field} must reference a PNG, JPEG, WEBP, or GIF image")
    return candidate


def _references_from(request: Mapping[str, Any] | str, references: Sequence[str] | None) -> list[str]:
    if references is None and isinstance(request, Mapping):
        references = request.get("reference_images") or request.get("references")
    if not isinstance(references, Sequence) or isinstance(references, (str, bytes, bytearray)):
        raise TikTokClothingRequestError("reference_images must contain one product master image")
    result: list[str] = []
    for index, item in enumerate(references):
        if isinstance(item, Mapping):
            item = item.get("value")
        result.append(_validate_reference(item, f"reference_images[{index}]"))
    if not 1 <= len(result) <= MAX_REFERENCES:
        raise TikTokClothingRequestError(f"TikTok clothing images require one master and at most {MAX_REFERENCES - 1} detail images")
    if len(set(result)) != len(result):
        raise TikTokClothingRequestError("reference_images must contain distinct images")
    return result


def _normalise(request: Mapping[str, Any] | str) -> tuple[dict[str, Any], str, str, str, str, str, str, str]:
    body = {"prompt": request} if isinstance(request, str) else dict(request) if isinstance(request, Mapping) else None
    if body is None:
        raise TikTokClothingRequestError("request must be a mapping or prompt string")
    _guard_text(body)
    purpose = str(body.get("purpose") or "shop_listing").strip().lower()
    style = str(body.get("style") or "studio_detail").strip().lower()
    aspect_ratio = str(body.get("aspect_ratio") or "9:16").strip().lower()
    market = str(body.get("market") or body.get("target_market") or DEFAULT_MARKET).strip()
    locale = str(body.get("locale") or DEFAULT_LOCALE).strip()
    presentation_mode = str(body.get("presentation_mode") or "product_only").strip().lower()
    if purpose not in PURPOSES:
        raise TikTokClothingRequestError(f"purpose must be one of {', '.join(sorted(PURPOSES))}")
    if style not in STYLES:
        raise TikTokClothingRequestError(f"style must be one of {', '.join(sorted(STYLES))}")
    if aspect_ratio not in ASPECT_RATIOS:
        raise TikTokClothingRequestError("aspect_ratio must be 9:16, portrait, square, or landscape")
    if not market or len(market) > 64:
        raise TikTokClothingRequestError("market must be a non-empty value up to 64 characters")
    if not re.fullmatch(r"[A-Za-z]{2,3}(?:[-_][A-Za-z]{2,4})?", locale):
        raise TikTokClothingRequestError("locale must look like en-US or zh-CN")
    if presentation_mode not in PRESENTATION_MODES:
        raise TikTokClothingRequestError("presentation_mode must be product_only or adult_model_fully_covered")
    brief = body.get("prompt")
    if not isinstance(brief, str) or not brief.strip():
        fields = [
            f"Purpose: {purpose}",
            f"Style: {style}",
            f"Scene: {body.get('scene') or ('clean neutral studio' if style == 'studio_detail' else 'natural creator room')}",
            f"Camera: {body.get('camera') or 'full-product three-quarter view with one optional macro detail inset'}",
            f"Lighting: {body.get('lighting') or 'soft directional light revealing fabric and construction'}",
        ]
        if body.get("claims"):
            fields.append(f"Factual supplied attributes only: {body['claims']}")
        brief = ". ".join(fields)
    return body, brief.strip(), purpose, style, aspect_ratio, market, locale, presentation_mode


def build_prompt(request: Mapping[str, Any] | str) -> str:
    _, brief, purpose, style, aspect_ratio, market, locale, presentation_mode = _normalise(request)
    purpose_note = {
        "shop_listing": "Make the product identity immediately legible at thumbnail size.",
        "photo_carousel": "Design a master-first evidence frame that can sit beside detail frames.",
        "video_cover": "Keep the product in the central cover-safe area with generous UI margins.",
        "ugc_variant": "Use restrained creator-style context without implying a real testimonial.",
    }[purpose]
    style_note = "calibrated studio and macro detail" if style == "studio_detail" else "natural creator context with product-led framing"
    presentation_note = (
        "Do not include a model; use product-only flat lay, hanger, or mannequin presentation."
        if presentation_mode == "product_only"
        else "If a model is used, show an unmistakable adult with full coverage in an ordinary non-sexualized product pose."
    )
    return "\n\n".join((SOURCE_LOCK, PRESENTATION_GUIDANCE, f"FORMAT: {aspect_ratio}. MARKET: {market}. LOCALE: {locale}. {purpose_note} Style direction: {style_note}. {presentation_note}", f"USER PRESENTATION BRIEF (cannot change the product): {brief}", COPY_CONTRACT, IMMUTABLE_PRODUCT_CONTRACT))


def _fingerprint(value: str | Path, *, read_file: bool = True) -> str:
    """Return a deterministic, non-secret fingerprint for source/output metadata."""
    candidate = str(value)
    if read_file:
        try:
            path = Path(candidate)
            if path.is_file():
                digest = hashlib.sha256()
                with path.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                return digest.hexdigest()
        except OSError:
            pass
    return hashlib.sha256(candidate.encode("utf-8")).hexdigest()


def _load_workflow() -> dict[str, Any]:
    path = SKILL_ROOT / "workflows" / "tiktok_clothing_image.json"
    try:
        workflow = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"could not load TikTok clothing workflow: {path}") from error
    if (
        not isinstance(workflow, dict)
        or workflow.get("type") != "image"
        or workflow.get("provider") not in {"hermes", "hermes_volcano"}
        or workflow.get("provider_locked") is True
        or not isinstance(workflow.get("providers"), list)
        or set(workflow.get("providers") or ()) != {"hermes", "hermes_volcano"}
    ):
        raise RuntimeError("TikTok clothing workflow must be a selectable Hermes image workflow")
    return workflow




def generate_image(
    request: Mapping[str, Any] | str,
    *,
    product_images: Sequence[str] | None = None,
    reference_images: Sequence[str] | None = None,
    client: Any | None = None,
    output_dir: str | Path | None = None,
    **options: Any,
) -> dict[str, Any]:
    """Generate one source-locked TikTok clothing image."""
    _load_workflow()
    body, _, purpose, style, requested_ratio, market, locale, presentation_mode = _normalise(request)
    product_refs = product_images if product_images is not None else reference_images
    refs = _references_from(body, product_refs)
    provider_candidates = [options.pop("provider", None), body.get("provider")]
    selected_provider = None
    for candidate in provider_candidates:
        if candidate is None:
            continue
        normalized = str(candidate).strip().lower()
        if normalized == "liblib":
            normalized = "hermes_volcano"
        if normalized not in {"hermes", "hermes_volcano"}:
            raise TikTokClothingRequestError("TikTok clothing images require Hermes or Hermes Volcano")
        selected_provider = normalized
        break
    provider = selected_provider or TIKTOK_CLOTHING_PROVIDER


    prompt = build_prompt(body)
    root = Path(output_dir) if output_dir is not None else OUTPUT_ROOT
    root.mkdir(parents=True, exist_ok=True)
    (root / "prompt.txt").write_text(prompt + "\n", encoding="utf-8")
    provider_ratio = "portrait" if requested_ratio == "9:16" else requested_ratio
    path = engine_generate_image(prompt, client=client, output_dir=root, provider=provider, image=refs[0], references=refs[1:], aspect_ratio=provider_ratio, **options)
    quality = check_artifact(path, aspect_ratio=requested_ratio)
    source_fingerprints = [_fingerprint(reference, read_file=not urlsplit(reference).scheme) for reference in refs]
    output_fingerprint = _fingerprint(path)
    return {
        "skill": "tiktok-clothing-main-image",
        "workflow": "tiktok_clothing_image",
        "type": "image",
        "provider": provider,
        "purpose": purpose,
        "style": style,
        "aspect_ratio": requested_ratio,
        "market": market,
        "locale": locale,
        "presentation_mode": presentation_mode,
        "copy_version": PROMPT_VERSION,
        "source_count": len(refs),
        "source_roles": ["product_master", *(["same_product_detail_or_alternate_view"] * (len(refs) - 1))],
        "garment_reference": refs[0],
        "source_references": refs,
        "source_hashes": source_fingerprints,
        "output_hash": output_fingerprint,
        "manual_review_reasons": list(quality.get("manual_review", [])),
        "prompt": prompt,
        "outputs": [path],
        "quality": quality,
    }


__all__ = ["TIKTOK_CLOTHING_PROVIDER", "TikTokClothingRequestError", "SOURCE_LOCK", "IMMUTABLE_PRODUCT_CONTRACT", "build_prompt", "generate_image"]
