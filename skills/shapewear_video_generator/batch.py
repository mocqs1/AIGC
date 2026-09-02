"""Deterministic batch planning for compliant shapewear commerce videos.

This module only produces validated creative metadata and intelligent-editing
requests. Rendering remains owned by the existing local mix API and Harness.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from skills.intelligent_editing.runtime import (
    IntelligentEditingValidationError,
    canonical_json,
    validate_managed_asset_id,
    validate_request,
)


BATCH_VERSION = "shapewear-video-batch/v1"
BEAT_TYPES = ("hook", "demonstration", "detail", "cta")
PLATFORMS = {"tiktok", "reels", "shorts"}
PLATFORM_PRESETS: dict[str, dict[str, Any]] = {
    "tiktok": {
        "aspect_ratio": "portrait",
        "default_duration_ms": 10000,
        "min_duration_ms": 6000,
        "max_duration_ms": 60000,
        "safe_zone": {"top": 0.12, "right": 0.08, "bottom": 0.18, "left": 0.08},
    },
    "reels": {
        "aspect_ratio": "portrait",
        "default_duration_ms": 10000,
        "min_duration_ms": 6000,
        "max_duration_ms": 90000,
        "safe_zone": {"top": 0.12, "right": 0.08, "bottom": 0.18, "left": 0.08},
    },
    "shorts": {
        "aspect_ratio": "portrait",
        "default_duration_ms": 15000,
        "min_duration_ms": 6000,
        "max_duration_ms": 60000,
        "safe_zone": {"top": 0.12, "right": 0.08, "bottom": 0.16, "left": 0.08},
    },
}

DEFAULT_HOOKS = ("fit_question", "seam_detail", "fabric_recovery")
DEFAULT_PROOF_ORDERS = ("fit_then_detail", "detail_then_fit")
DEFAULT_RHYTHMS = ("four_readable_beats", "three_longer_beats")
DEFAULT_CTA = ("shop_collection", "check_size_and_care")
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
UNSAFE_COPY = (
    re.compile(r"\b(?:guaranteed?|guarantee|cure|treat|prevent|medical|clinically\s+proven|fda[- ]approved|doctor[- ]approved|doctor[- ]recommended|pain relief|pain[- ]free|weight loss|slim(?:s|ming)?|slim\s+(?:your\s+)?waist|melts?\s+fat|burn(?:s|ing)?\s+fat|improves?\s+posture|compression\s+(?:improves?|corrects?)\s+posture|posture)\b", re.I),
    re.compile(r"\b(?:therapeutic|therapy|clinical|doctor|physician|fda|approved|pain|fat|weight|slimness|reshape|sculpt|body[- ]shap(?:e|ing)|transform(?:ation)?|effective|efficacy)\b", re.I),
    re.compile(r"\blose\s+\d+(?:\.\d+)?\s*(?:kg|kilos?|pounds?|lb)\b", re.I),
    re.compile(r"\b(?:fix(?:ing)?|hate|hide|reshape|transform(?:ing)?|sculpt(?:ing)?)\s+your\s+body\b|\bbody\s+(?:transformation|reshaping|sculpting|shaping)\b", re.I),
    re.compile(r"\b(?:reduce|reduction|relieve|relief)\s+(?:your\s+)?pain\b|\bpain\s+(?:reduction|relief)\b", re.I),
    re.compile(r"\b(?:fat\s+rolls?|body\s+sham(?:e|ing)|shame\s+your\s+body)\b", re.I),
    re.compile(r"\b(?:minor|child|children|kid|teen|teenager)\b", re.I),
    re.compile(r"\b(?:nude|naked|porn|explicit sex|sexual act|fetish|sexualized?|sexy|erotic|seductive|provocative)\b", re.I),
    re.compile(r"\b(?:official|sponsored|affiliated|endorsed)\b.{0,40}\b(?:SKIMS|SPANX)\b", re.I),
    re.compile(r"\b(?:SKIMS|SPANX)\b.{0,40}\b(?:campaign|sponsored|official|affiliated|endorsed|partnership)\b", re.I),
    re.compile(r"\b(?:SKIMS|SPANX)\b", re.I),
    re.compile(r"\b(?:best\b|number\s+one|no\.?\s*1|#\s*1|100%\s+effective|most\s+effective|top[- ]rated|award[- ]winning|certified|patented|as\s+seen\s+on\s+tv)\b", re.I),
    re.compile(r"\b(?:fake\s+review|customer\s+testimonial|testimonial|fake\s+customer|five[- ]star\s+review)\b", re.I),
    re.compile(r"(?:医疗|治疗|减肥|减重|疼痛缓解|身体羞辱|修复身体|讨厌你的身体|未成年人|儿童|青少年|裸体|色情|性行为|恋物|品牌冒充)"),
)


class ShapewearBatchError(ValueError):
    """Raised when a batch request crosses the skill contract."""


def _safe_text(value: Any, field: str, maximum: int = 200) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ShapewearBatchError(f"{field} must be a non-empty string")
    text = value.strip()
    if len(text) > maximum or any(ord(char) < 32 and char not in "\t\r\n" for char in text):
        raise ShapewearBatchError(f"{field} is invalid or too long")
    lowered = text.lower()
    if any(token in lowered for token in ("http://", "https://", "file://", "api_key", "authorization", "bearer ")):
        raise ShapewearBatchError(f"{field} must not contain URLs or credentials")
    return text


def _safe_copy(value: Any, field: str, maximum: int = 200) -> str:
    """Validate user-facing creative copy, including regulated claim terms."""
    text = _safe_text(value, field, maximum)
    if any(pattern.search(text) for pattern in UNSAFE_COPY):
        raise ShapewearBatchError(f"{field} contains an unsupported or unsubstantiated claim")
    return text


def _choice_list(value: Any, field: str, defaults: Sequence[str], maximum: int = 12) -> list[str]:
    values = defaults if value is None else value
    if not isinstance(values, (list, tuple)) or not values or len(values) > maximum:
        raise ShapewearBatchError(f"{field} must contain 1 to {maximum} options")
    result = []
    for item in values:
        result.append(_safe_text(item, f"{field} item", 80))
    if len(set(result)) != len(result):
        raise ShapewearBatchError(f"{field} must not contain duplicates")
    return result


def _asset_records(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not 2 <= len(value) <= 50:
        raise ShapewearBatchError("selected_assets must contain 2 to 50 assets")
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping):
            raise ShapewearBatchError(f"selected_assets[{index}] must be an object")
        allowed = {"asset_id", "media_type", "duration_ms", "trim_bounds_ms"}
        extra = set(raw) - allowed
        if extra:
            raise ShapewearBatchError(f"selected_assets[{index}] contains unsupported fields")
        try:
            asset_id = validate_managed_asset_id(raw.get("asset_id"))
        except IntelligentEditingValidationError as error:
            raise ShapewearBatchError(str(error)) from error
        if asset_id in seen:
            raise ShapewearBatchError("selected asset IDs must be distinct")
        seen.add(asset_id)
        media_type = raw.get("media_type")
        if media_type not in {"image", "video"}:
            raise ShapewearBatchError("asset media_type must be image or video")
        record: dict[str, Any] = {"asset_id": asset_id, "media_type": media_type}
        duration = raw.get("duration_ms")
        if duration is not None:
            if isinstance(duration, bool) or not isinstance(duration, int) or not 500 <= duration <= 60000:
                raise ShapewearBatchError("asset duration_ms must be between 500 and 60000")
            record["duration_ms"] = duration
        if raw.get("trim_bounds_ms") is not None:
            bounds = raw["trim_bounds_ms"]
            if media_type != "video" or not isinstance(bounds, Mapping):
                raise ShapewearBatchError("video trim_bounds_ms must be an object")
            if set(bounds) != {"start_ms", "end_ms"}:
                raise ShapewearBatchError("trim_bounds_ms must use start_ms and end_ms")
            start, end = bounds["start_ms"], bounds["end_ms"]
            if any(isinstance(item, bool) or not isinstance(item, int) for item in (start, end)) or not 0 <= start < end <= 3600000 or end - start > 60000:
                raise ShapewearBatchError("video trim bounds are invalid")
            record["trim_bounds_ms"] = {"start_ms": start, "end_ms": end}
        records.append(record)
    return records


def _duration(value: Any, platform: str) -> int:
    preset = PLATFORM_PRESETS[platform]
    if value is None:
        return preset["default_duration_ms"]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ShapewearBatchError("duration_ms must be an integer")
    if not preset["min_duration_ms"] <= value <= preset["max_duration_ms"]:
        raise ShapewearBatchError(f"duration_ms is outside the {platform} preset bounds")
    return value


def _beat_durations(total: int) -> list[int]:
    base = [max(500, int(total * ratio)) for ratio in (0.2, 0.4, 0.2, 0.2)]
    base[-1] += total - sum(base)
    if base[-1] < 500:
        raise ShapewearBatchError("duration is too short for four readable beats")
    return base


def _variant_id(seed: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(canonical_json(seed).encode("utf-8")).hexdigest()
    return f"swv-{digest[:20]}"


def _editing_request(variant_id: str, assets: list[dict[str, Any]], objective: str, aspect_ratio: str, duration_ms: int) -> dict[str, Any]:
    request_assets = []
    for asset in assets:
        record = dict(asset)
        if record["media_type"] == "image":
            record.setdefault("duration_ms", 3000)
        request_assets.append(record)
    return {
        "request_id": variant_id,
        "idempotency_key": variant_id,
        "selected_assets": request_assets,
        "objective": objective,
        "aspect_ratio": aspect_ratio,
        "target_duration_ms": duration_ms,
        "image_duration_ms": 3000,
        "transition_mode": "hard_cut",
        "planner": "auto",
    }


def plan_batch(request: Mapping[str, Any]) -> dict[str, Any]:
    """Create a stable batch of platform-aware creative variants."""
    if not isinstance(request, Mapping):
        raise ShapewearBatchError("batch request must be an object")
    platform = request.get("platform", "tiktok")
    if platform not in PLATFORMS:
        raise ShapewearBatchError("platform must be tiktok, reels, or shorts")
    batch_id = request.get("batch_id", "")
    if batch_id and (not isinstance(batch_id, str) or not SAFE_ID.fullmatch(batch_id)):
        raise ShapewearBatchError("batch_id must be a safe opaque identifier")
    product = _safe_text(request.get("product", "shapewear product"), "product")
    market = _safe_text(request.get("market", "global"), "market", 80)
    locale = _safe_text(request.get("locale", "en-US"), "locale", 40)
    objective = _safe_text(request.get("objective", "show fit, construction, and care details"), "objective", 500)
    assets = _asset_records(request.get("selected_assets"))
    duration_ms = _duration(request.get("duration_ms"), platform)
    variant_count = request.get("variant_count", 4)
    if isinstance(variant_count, bool) or not isinstance(variant_count, int) or not 1 <= variant_count <= 12:
        raise ShapewearBatchError("variant_count must be between 1 and 12")
    hooks = _choice_list(request.get("hooks"), "hooks", DEFAULT_HOOKS)
    proof_orders = _choice_list(request.get("proof_orders"), "proof_orders", DEFAULT_PROOF_ORDERS)
    rhythms = _choice_list(request.get("rhythms"), "rhythms", DEFAULT_RHYTHMS)
    ctas = _choice_list(request.get("ctas"), "ctas", DEFAULT_CTA)
    claims_reviewed = request.get("claims_reviewed", False)
    if not isinstance(claims_reviewed, bool):
        raise ShapewearBatchError("claims_reviewed must be a boolean")
    beat_durations = _beat_durations(duration_ms)
    variants: list[dict[str, Any]] = []
    for index in range(variant_count):
        hook = hooks[index % len(hooks)]
        proof_order = proof_orders[index % len(proof_orders)]
        rhythm = rhythms[index % len(rhythms)]
        cta = ctas[index % len(ctas)]
        seed = {
            "product": product,
            "platform": platform,
            "market": market,
            "locale": locale,
            "index": index,
            "asset_ids": [asset["asset_id"] for asset in assets],
            "hook": hook,
            "proof_order": proof_order,
            "rhythm": rhythm,
            "cta": cta,
        }
        variant_id = _variant_id(seed)
        beat_assets = [assets[position % len(assets)]["asset_id"] for position in range(4)]
        beats = [
            {"type": beat_type, "asset_id": beat_assets[position], "duration_ms": beat_durations[position]}
            for position, beat_type in enumerate(BEAT_TYPES)
        ]
        variant = {
            "variant_id": variant_id,
            "platform": platform,
            "market": market,
            "locale": locale,
            "aspect_ratio": PLATFORM_PRESETS[platform]["aspect_ratio"],
            "duration_ms": duration_ms,
            "safe_zone": dict(PLATFORM_PRESETS[platform]["safe_zone"]),
            "creative": {"hook": hook, "proof_order": proof_order, "rhythm": rhythm, "cta": cta},
            "beats": beats,
            "editing_request": _editing_request(variant_id, assets, objective, PLATFORM_PRESETS[platform]["aspect_ratio"], duration_ms),
            "quality": {
                "passed": False,
                "manual_review": [
                    "确认服装结构、版型连续性和文字安全区",
                    "确认声明有真实尺码、材质、护理或退换依据",
                ],
                "claim_reviewed": claims_reviewed,
            },
        }
        if not claims_reviewed:
            variant["quality"]["manual_review"].append("尚未完成商品声明审校")
        variants.append(variant)
    result = {
        "version": BATCH_VERSION,
        "batch_id": batch_id or _variant_id({"product": product, "platform": platform, "market": market, "locale": locale, "variants": variants}),
        "product": product,
        "platform": platform,
        "market": market,
        "locale": locale,
        "objective": objective,
        "selected_assets": assets,
        "variant_count": variant_count,
        "variants": variants,
        "warnings": ["创意假设需要通过平台数据实验验证，不能保证流量或转化"],
    }
    validate_batch(result)
    return result


def validate_batch(batch: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a generated batch before any render submission."""
    if not isinstance(batch, Mapping) or batch.get("version") != BATCH_VERSION:
        raise ShapewearBatchError(f"batch.version must be {BATCH_VERSION}")
    required = {"version", "batch_id", "product", "platform", "market", "locale", "objective", "selected_assets", "variant_count", "variants", "warnings"}
    if set(batch) != required:
        raise ShapewearBatchError("batch contains unsupported or missing fields")
    batch_id = batch.get("batch_id")
    if not isinstance(batch_id, str) or not SAFE_ID.fullmatch(batch_id):
        raise ShapewearBatchError("batch_id must be a safe opaque identifier")
    platform = batch.get("platform")
    if platform not in PLATFORMS:
        raise ShapewearBatchError("batch platform is invalid")
    _safe_copy(batch.get("product"), "product")
    _safe_text(batch.get("market"), "market", 80)
    _safe_text(batch.get("locale"), "locale", 40)
    objective = _safe_copy(batch.get("objective"), "objective", 500)
    try:
        selected_assets = _asset_records(batch.get("selected_assets"))
    except (ShapewearBatchError, TypeError, AttributeError) as error:
        if isinstance(error, ShapewearBatchError):
            raise
        raise ShapewearBatchError("selected_assets is malformed") from error
    selected_ids = {asset["asset_id"] for asset in selected_assets}
    expected_request_assets = []
    for asset in selected_assets:
        normalized_asset = dict(asset)
        if normalized_asset["media_type"] == "image":
            normalized_asset.setdefault("duration_ms", 3000)
        expected_request_assets.append(normalized_asset)
    count = batch.get("variant_count")
    variants = batch.get("variants")
    if isinstance(count, bool) or not isinstance(count, int) or not isinstance(variants, list) or len(variants) != count or not 1 <= count <= 12:
        raise ShapewearBatchError("variant_count does not match variants")
    seen: set[str] = set()
    for index, variant in enumerate(variants):
        if not isinstance(variant, Mapping):
            raise ShapewearBatchError(f"variants[{index}] must be an object")
        for key in ("variant_id", "platform", "market", "locale", "aspect_ratio", "duration_ms", "safe_zone", "creative", "beats", "editing_request", "quality"):
            if key not in variant:
                raise ShapewearBatchError(f"variants[{index}] is missing {key}")
        variant_id = variant["variant_id"]
        if not isinstance(variant_id, str) or not SAFE_ID.fullmatch(variant_id) or variant_id in seen:
            raise ShapewearBatchError("variant IDs must be unique safe identifiers")
        seen.add(variant_id)
        if (
            variant["platform"] != platform
            or variant["market"] != batch["market"]
            or variant["locale"] != batch["locale"]
            or variant["aspect_ratio"] != "portrait"
        ):
            raise ShapewearBatchError("variant platform/aspect ratio does not match batch")
        duration = variant["duration_ms"]
        if isinstance(duration, bool) or not isinstance(duration, int) or not PLATFORM_PRESETS[platform]["min_duration_ms"] <= duration <= PLATFORM_PRESETS[platform]["max_duration_ms"]:
            raise ShapewearBatchError("variant duration is outside platform bounds")
        safe_zone = variant["safe_zone"]
        if (
            not isinstance(safe_zone, Mapping)
            or set(safe_zone) != {"top", "right", "bottom", "left"}
            or any(isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value < 1 for value in safe_zone.values())
        ):
            raise ShapewearBatchError("variant safe_zone is invalid")
        creative = variant["creative"]
        if not isinstance(creative, Mapping) or set(creative) != {"hook", "proof_order", "rhythm", "cta"}:
            raise ShapewearBatchError("variant creative fields are invalid")
        for field in ("hook", "proof_order", "rhythm", "cta"):
            _safe_copy(creative[field], f"variants[{index}].creative.{field}", 120)
        beats = variant["beats"]
        if not isinstance(beats, list) or len(beats) != 4:
            raise ShapewearBatchError("variant beats must be hook, demonstration, detail, and cta")
        normalized_beats = []
        for beat_index, beat in enumerate(beats):
            if not isinstance(beat, Mapping) or set(beat) != {"type", "asset_id", "duration_ms"}:
                raise ShapewearBatchError(f"variants[{index}].beats[{beat_index}] is malformed")
            if beat["type"] != BEAT_TYPES[beat_index]:
                raise ShapewearBatchError("variant beats must be hook, demonstration, detail, and cta")
            try:
                beat_asset_id = validate_managed_asset_id(beat["asset_id"])
            except IntelligentEditingValidationError as error:
                raise ShapewearBatchError(f"variants[{index}].beats[{beat_index}] asset is invalid") from error
            if beat_asset_id not in selected_ids:
                raise ShapewearBatchError("variant beat references an unselected asset")
            beat_duration = beat["duration_ms"]
            if isinstance(beat_duration, bool) or not isinstance(beat_duration, int) or not 500 <= beat_duration <= 60000:
                raise ShapewearBatchError("beat duration must be between 500 and 60000 ms")
            normalized_beats.append((beat_asset_id, beat_duration))
        if sum(item[1] for item in normalized_beats) != duration:
            raise ShapewearBatchError("beat durations must equal variant duration")
        if any(normalized_beats[pos][0] == normalized_beats[pos - 1][0] for pos in range(1, len(normalized_beats))):
            raise ShapewearBatchError("variant beats must not repeat the same asset consecutively")
        editing_request = variant["editing_request"]
        try:
            normalized_request = validate_request(editing_request)
        except IntelligentEditingValidationError as error:
            raise ShapewearBatchError(f"variant editing request is invalid: {error}") from error
        if normalized_request["request_id"] != variant_id:
            raise ShapewearBatchError("variant editing request ID mismatch")
        if normalized_request["idempotency_key"] != variant_id:
            raise ShapewearBatchError("variant editing idempotency key mismatch")
        if normalized_request["selected_assets"] != expected_request_assets:
            raise ShapewearBatchError("variant editing assets do not match batch selected_assets")
        if (
            normalized_request["objective"] != objective
            or normalized_request["aspect_ratio"] != variant["aspect_ratio"]
            or normalized_request["target_duration_ms"] != duration
            or normalized_request["image_duration_ms"] != 3000
            or normalized_request["transition_mode"] != "hard_cut"
            or normalized_request["planner"] != "auto"
        ):
            raise ShapewearBatchError("variant editing request does not match batch settings")
        quality = variant["quality"]
        if (
            not isinstance(quality, Mapping)
            or set(quality) != {"passed", "manual_review", "claim_reviewed"}
            or not isinstance(quality["passed"], bool)
            or not isinstance(quality["claim_reviewed"], bool)
            or not isinstance(quality["manual_review"], list)
            or any(not isinstance(item, str) or len(item) > 500 for item in quality["manual_review"])
        ):
            raise ShapewearBatchError("variant quality metadata is invalid")
        for review_index, review_text in enumerate(quality["manual_review"]):
            _safe_copy(review_text, f"variants[{index}].quality.manual_review[{review_index}]", 500)
    warnings = batch["warnings"]
    if not isinstance(warnings, list) or not warnings or any(not isinstance(item, str) or len(item) > 500 for item in warnings):
        raise ShapewearBatchError("batch warnings must be a non-empty string list")
    for warning_index, warning in enumerate(warnings):
        _safe_copy(warning, f"warnings[{warning_index}]", 500)
    return dict(batch)


def batch_manifest(batch: Mapping[str, Any], *, rendered: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Attach safe per-variant render results without accepting raw provider data."""
    validate_batch(batch)
    rendered_by_id = rendered or {}
    outputs = []
    for variant in batch["variants"]:
        result = rendered_by_id.get(variant["variant_id"]) if isinstance(rendered_by_id, Mapping) else None
        output = result.get("output") if isinstance(result, Mapping) else None
        safe_result = dict(variant["quality"])
        if isinstance(output, str) and output.startswith("/api/assets/"):
            safe_result["output"] = output
        else:
            safe_result.setdefault("manual_review", []).append("尚未获得可验证的渲染输出")
        outputs.append({"variant_id": variant["variant_id"], "quality": safe_result})
    return {"version": BATCH_VERSION, "batch_id": batch["batch_id"], "platform": batch["platform"], "variants": outputs}


__all__ = ["BATCH_VERSION", "BEAT_TYPES", "PLATFORM_PRESETS", "ShapewearBatchError", "batch_manifest", "plan_batch", "validate_batch"]
