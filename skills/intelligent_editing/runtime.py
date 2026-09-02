"""Pure validation and hashing for intelligent editing plans."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from typing import Any


RUN_VERSION = "aigc-intelligent-editing-run/v1"
PLAN_VERSION = "aigc-mix-plan/v1"
MANAGED_ROOTS = {"images", "videos", "shapewear", "clothing_image", "imported"}
REQUEST_KEYS = {
    "request_id",
    "idempotency_key",
    "selected_assets",
    "objective",
    "aspect_ratio",
    "target_duration_ms",
    "transition_mode",
    "planner",
    "image_duration_ms",
}
ASSET_KEYS = {"asset_id", "media_type", "duration_ms", "trim_bounds_ms", "size_bucket", "width", "height"}
PLAN_KEYS = {
    "version",
    "objective",
    "target_duration_ms",
    "clips",
    "planner",
    "transition_mode",
    "warnings",
}
CLIP_KEYS = {"asset_id", "start_ms", "end_ms", "duration_ms", "transition"}
OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SHA256 = re.compile(r"^[a-f0-9]{64}$")
SECRET_TEXT = re.compile(
    r"(?i)(?:api[_-]?key|authorization|bearer|access[_-]?token|secret|password)\s*[:=]\s*\S+"
)
WINDOWS_PATH = re.compile(r"(?i)(?:^|\s)[a-z]:[\\/]")
POSIX_PATH = re.compile(r"(?:^|\s)/(?:home|users|root|tmp|var|etc|opt|mnt|volumes)(?:/|\b)", re.I)
URL_TEXT = re.compile(r"(?i)\b(?:https?|file)://")
COMMAND_TEXT = re.compile(r"(?i)(?:^|[\r\n])\s*(?:ffmpeg|cmd(?:\.exe)?|powershell|pwsh|bash|sh)\b")


class IntelligentEditingValidationError(ValueError):
    """Raised when request or plan data crosses the Skill contract."""

    code = "invalid_intelligent_editing_data"

    @property
    def message(self) -> str:
        return str(self)

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise IntelligentEditingValidationError(f"{field} must be an object")
    return value


def _exact_keys(value: Mapping[str, Any], allowed: set[str], field: str) -> None:
    extras = set(value) - allowed
    if extras:
        raise IntelligentEditingValidationError(
            f"{field} contains unsupported fields: {', '.join(sorted(str(item) for item in extras))}"
        )


def _bounded_int(value: Any, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise IntelligentEditingValidationError(
            f"{field} must be an integer between {minimum} and {maximum}"
        )
    return value


def _opaque_id(value: Any, field: str) -> str:
    if not isinstance(value, str) or not OPAQUE_ID.fullmatch(value.strip()):
        raise IntelligentEditingValidationError(f"{field} must be a safe opaque identifier")
    return value.strip()


def validate_managed_asset_id(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise IntelligentEditingValidationError("asset_id must be a non-empty string")
    asset_id = value.strip()
    if (
        len(asset_id) > 4096
        or "\\" in asset_id
        or asset_id.startswith("/")
        or URL_TEXT.search(asset_id)
        or WINDOWS_PATH.search(asset_id)
        or any(character in asset_id for character in "\r\n\0;&|`$<>:?#%")
    ):
        raise IntelligentEditingValidationError("asset_id must be a managed identifier, not a path, URL, or command")
    parts = asset_id.split("/")
    if parts[0] not in MANAGED_ROOTS or any(part in {"", ".", ".."} for part in parts):
        raise IntelligentEditingValidationError("asset_id is outside the managed asset roots")
    return asset_id


def _safe_text(value: Any, field: str, *, maximum: int = 2000) -> str:
    if not isinstance(value, str) or not value.strip():
        raise IntelligentEditingValidationError(f"{field} must be a non-empty string")
    text = value.strip()
    if len(text) > maximum or any(ord(character) < 32 and character not in "\t\r\n" for character in text):
        raise IntelligentEditingValidationError(f"{field} is invalid or too long")
    if (
        SECRET_TEXT.search(text)
        or WINDOWS_PATH.search(text)
        or POSIX_PATH.search(text)
        or URL_TEXT.search(text)
        or COMMAND_TEXT.search(text)
        or any(token in text for token in ("&&", "||", "$(", "`"))
    ):
        raise IntelligentEditingValidationError(
            f"{field} must not contain credentials, URLs, filesystem paths, or commands"
        )
    return text


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_request(
    request: Mapping[str, Any],
    *,
    managed_asset_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    body = _mapping(request, "request")
    _exact_keys(body, REQUEST_KEYS, "request")
    request_id = _opaque_id(body.get("request_id"), "request_id")
    idempotency_key = _opaque_id(body.get("idempotency_key", request_id), "idempotency_key")
    objective = _safe_text(body.get("objective"), "objective")
    aspect_ratio = body.get("aspect_ratio", "portrait")
    if aspect_ratio not in {"portrait", "landscape", "square"}:
        raise IntelligentEditingValidationError("aspect_ratio must be portrait, landscape, or square")
    transition_mode = body.get("transition_mode", "auto")
    if transition_mode not in {"auto", "hard_cut", "fade"}:
        raise IntelligentEditingValidationError("transition_mode must be auto, hard_cut, or fade")
    planner = body.get("planner", "auto")
    if planner not in {"codex_terra", "local", "auto"}:
        raise IntelligentEditingValidationError(
            "planner must be codex_terra, local, or auto"
        )
    target_duration_ms = _bounded_int(
        body.get("target_duration_ms", 15000), "target_duration_ms", 1000, 300000
    )
    image_duration_ms = _bounded_int(
        body.get("image_duration_ms", 3000), "image_duration_ms", 500, 60000
    )
    assets = body.get("selected_assets")
    if not isinstance(assets, list) or not 2 <= len(assets) <= 50:
        raise IntelligentEditingValidationError("selected_assets must contain 2 to 50 objects")
    allowed_ids = set(managed_asset_ids) if managed_asset_ids is not None else None
    normalized_assets: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw_asset in enumerate(assets):
        asset = _mapping(raw_asset, f"selected_assets[{index}]")
        _exact_keys(asset, ASSET_KEYS, f"selected_assets[{index}]")
        asset_id = validate_managed_asset_id(asset.get("asset_id"))
        if allowed_ids is not None and asset_id not in allowed_ids:
            raise IntelligentEditingValidationError(f"selected asset is not managed: {asset_id}")
        if asset_id in seen:
            raise IntelligentEditingValidationError("selected asset IDs must be distinct")
        seen.add(asset_id)
        media_type = asset.get("media_type")
        if media_type not in {"image", "video"}:
            raise IntelligentEditingValidationError(f"selected_assets[{index}].media_type must be image or video")
        normalized: dict[str, Any] = {"asset_id": asset_id, "media_type": media_type}
        duration = asset.get("duration_ms")
        if duration is not None:
            normalized["duration_ms"] = _bounded_int(
                duration, f"selected_assets[{index}].duration_ms", 500, 60000
            )
        elif media_type == "image":
            normalized["duration_ms"] = image_duration_ms
        trims = asset.get("trim_bounds_ms")
        if trims is not None:
            if media_type != "video":
                raise IntelligentEditingValidationError("trim_bounds_ms is supported only for video assets")
            trim = _mapping(trims, f"selected_assets[{index}].trim_bounds_ms")
            keys = set(trim)
            if keys == {"start_ms", "end_ms"}:
                start_value, end_value = trim.get("start_ms"), trim.get("end_ms")
            elif keys == {"start", "end"}:
                start_value, end_value = trim.get("start"), trim.get("end")
            else:
                raise IntelligentEditingValidationError("trim bounds must use start_ms/end_ms")
            start = _bounded_int(start_value if start_value is not None else 0, "trim start", 0, 3600000)
            end = _bounded_int(end_value, "trim end", 500, 3600000)
            if end <= start or end - start > 60000:
                raise IntelligentEditingValidationError("video trim must be ordered and no longer than 60 seconds")
            normalized["trim_bounds_ms"] = {"start_ms": start, "end_ms": end}
        normalized_assets.append(normalized)
    return {
        "request_id": request_id,
        "idempotency_key": idempotency_key,
        "selected_assets": normalized_assets,
        "objective": objective,
        "aspect_ratio": aspect_ratio,
        "target_duration_ms": target_duration_ms,
        "transition_mode": transition_mode,
        "planner": planner,
        "image_duration_ms": image_duration_ms,
    }


def build_plan_payload(request: Mapping[str, Any]) -> dict[str, Any]:
    normalized = validate_request(request)
    clips: list[dict[str, Any]] = []
    for asset in normalized["selected_assets"]:
        clip: dict[str, Any] = {"asset_id": asset["asset_id"]}
        if "duration_ms" in asset:
            clip["duration_ms"] = asset["duration_ms"]
        trims = asset.get("trim_bounds_ms")
        if trims:
            clip["start_ms"] = trims["start_ms"]
            clip["end_ms"] = trims["end_ms"]
        clips.append(clip)
    return {
        "clips": clips,
        "objective": normalized["objective"],
        "target_duration_ms": normalized["target_duration_ms"],
        "transition_mode": normalized["transition_mode"],
    }


def _safe_warning(value: Any) -> str:
    try:
        return _safe_text(value, "warning", maximum=500)
    except IntelligentEditingValidationError:
        return "Planner warning omitted because it contained unsupported data"


def normalize_plan(plan: Mapping[str, Any], request: Mapping[str, Any]) -> dict[str, Any]:
    normalized_request = validate_request(request)
    body = _mapping(plan, "plan")
    _exact_keys(body, PLAN_KEYS, "plan")
    if body.get("version") != PLAN_VERSION:
        raise IntelligentEditingValidationError(f"plan.version must be {PLAN_VERSION}")
    planner = body.get("planner")
    if planner not in {"local", "codex_terra"}:
        raise IntelligentEditingValidationError("plan.planner must be local or codex_terra")
    if body.get("transition_mode") != normalized_request["transition_mode"]:
        raise IntelligentEditingValidationError("plan transition_mode does not match the request")
    raw_clips = body.get("clips")
    if not isinstance(raw_clips, list) or not 2 <= len(raw_clips) <= 50:
        raise IntelligentEditingValidationError("plan.clips must contain 2 to 50 clips")
    selected_ids = [asset["asset_id"] for asset in normalized_request["selected_assets"]]
    allowed = set(selected_ids)
    selected_by_id = {
        asset["asset_id"]: asset for asset in normalized_request["selected_assets"]
    }
    clips: list[dict[str, Any]] = []
    durations: list[int] = []
    for index, raw_clip in enumerate(raw_clips):
        clip = _mapping(raw_clip, f"plan.clips[{index}]")
        _exact_keys(clip, CLIP_KEYS, f"plan.clips[{index}]")
        asset_id = validate_managed_asset_id(clip.get("asset_id"))
        if asset_id not in allowed:
            raise IntelligentEditingValidationError("plan contains an unselected asset")
        if clips and clips[-1]["asset_id"] == asset_id:
            raise IntelligentEditingValidationError("plan must not repeat the same asset consecutively")
        start = _bounded_int(clip.get("start_ms", 0), "clip.start_ms", 0, 3600000)
        end_value = clip.get("end_ms")
        duration_value = clip.get("duration_ms")
        end = None
        if end_value is not None:
            end = _bounded_int(end_value, "clip.end_ms", 500, 3600000)
            duration = end - start
            if duration_value is not None and duration_value != duration:
                raise IntelligentEditingValidationError("clip duration does not match its trim range")
        else:
            duration = _bounded_int(duration_value, "clip.duration_ms", 500, 60000)
        if not 500 <= duration <= 60000:
            raise IntelligentEditingValidationError("clip duration must be between 500 and 60000 ms")
        selected_asset = selected_by_id[asset_id]
        trim_bounds = selected_asset.get("trim_bounds_ms")
        if selected_asset["media_type"] == "image" and (start != 0 or end is not None):
            raise IntelligentEditingValidationError("image clips must not contain source trims")
        if trim_bounds and (
            start < trim_bounds["start_ms"]
            or start + duration > trim_bounds["end_ms"]
        ):
            raise IntelligentEditingValidationError("clip trim is outside the selected video bounds")
        source_duration = selected_asset.get("duration_ms")
        if (
            selected_asset["media_type"] == "video"
            and trim_bounds is None
            and source_duration is not None
            and start + duration > source_duration
        ):
            raise IntelligentEditingValidationError("clip trim exceeds the selected video duration")
        if clip.get("transition", "hard_cut") != "hard_cut":
            raise IntelligentEditingValidationError("only hard_cut plans can reach the local renderer")
        clips.append(
            {
                "asset_id": asset_id,
                "start_ms": start,
                "end_ms": end,
                "duration_ms": duration,
                "transition": "hard_cut",
            }
        )
        durations.append(duration)
    first_repeat = next(
        (index for index, clip in enumerate(clips) if clip["asset_id"] in {item["asset_id"] for item in clips[:index]}),
        None,
    )
    if first_repeat is not None:
        required_unique = min(len(selected_ids), 3)
        if len({clip["asset_id"] for clip in clips[:first_repeat]}) < required_unique:
            raise IntelligentEditingValidationError("plan repeats assets before meeting the variety rule")
    warnings = body.get("warnings")
    if not isinstance(warnings, list) or len(warnings) > 10:
        raise IntelligentEditingValidationError("plan.warnings must be a list of at most 10 strings")
    safe_warnings = [_safe_warning(warning) for warning in warnings]
    omitted = [asset_id for asset_id in selected_ids if asset_id not in {clip["asset_id"] for clip in clips}]
    if omitted and any(
        not any(asset_id in warning for warning in safe_warnings)
        for asset_id in omitted
    ):
        raise IntelligentEditingValidationError(
            "a plan that omits selected assets must name each omitted asset in a warning"
        )
    total = sum(durations)
    declared_total = _bounded_int(body.get("target_duration_ms"), "plan.target_duration_ms", 1000, 300000)
    if declared_total != total:
        raise IntelligentEditingValidationError("plan target_duration_ms must equal its clip total")
    requested_total = normalized_request["target_duration_ms"]
    tolerance = max(1, int(requested_total * 0.05)) if requested_total < 10000 else 500
    if abs(total - requested_total) > tolerance and not safe_warnings:
        raise IntelligentEditingValidationError("plan duration is outside tolerance without a warning")
    return {
        "version": PLAN_VERSION,
        "objective": normalized_request["objective"],
        "target_duration_ms": total,
        "clips": clips,
        "planner": planner,
        "transition_mode": normalized_request["transition_mode"],
        "warnings": safe_warnings,
    }


def build_render_payload(request: Mapping[str, Any], plan: Mapping[str, Any]) -> dict[str, Any]:
    normalized_request = validate_request(request)
    normalized_plan = normalize_plan(plan, normalized_request)
    return {
        "clips": build_plan_payload(normalized_request)["clips"],
        "aspect_ratio": normalized_request["aspect_ratio"],
        "objective": normalized_request["objective"],
        "target_duration_ms": normalized_request["target_duration_ms"],
        "transition_mode": normalized_request["transition_mode"],
        "auto_plan": False,
        "plan": normalized_plan,
    }


def make_local_plan(request: Mapping[str, Any], reason: str | None = None) -> dict[str, Any]:
    """Build a deterministic hard-cut plan without touching media or providers."""
    normalized = validate_request(request)
    assets = normalized["selected_assets"]
    capacities: list[int] = []
    for asset in assets:
        bounds = asset.get("trim_bounds_ms")
        capacity = (bounds["end_ms"] - bounds["start_ms"]) if bounds else asset.get("duration_ms")
        capacities.append(max(500, min(60000, int(capacity or (normalized["image_duration_ms"] if asset["media_type"] == "image" else 3000)))))
    count = len(assets)
    target = normalized["target_duration_ms"]
    effective = min(300000, max(count * 500, min(target, sum(capacities))))
    remaining = effective
    clips: list[dict[str, Any]] = []
    for index, asset in enumerate(assets):
        slots = count - index
        duration = min(capacities[index], max(500, remaining // slots))
        if index == count - 1:
            duration = min(capacities[index], remaining)
        remaining -= duration
        clip = {"asset_id": asset["asset_id"], "start_ms": 0, "end_ms": None, "duration_ms": int(duration), "transition": "hard_cut"}
        bounds = asset.get("trim_bounds_ms")
        if bounds:
            clip["start_ms"] = bounds["start_ms"]
            clip["end_ms"] = bounds["start_ms"] + int(duration)
        clips.append(clip)
    warnings: list[str] = []
    if effective != target:
        warnings.append("目标时长超出素材边界，已使用最近的可执行时长")
    if normalized["transition_mode"] == "fade":
        warnings.append("当前本地执行器仅支持硬切，已使用硬切过渡")
    if reason:
        warnings.append(reason)
    return {
        "version": PLAN_VERSION,
        "objective": normalized["objective"],
        "target_duration_ms": effective,
        "clips": clips,
        "planner": "local",
        "transition_mode": normalized["transition_mode"],
        "warnings": warnings,
    }


# Public names used by the Harness. Keep the stricter implementation above as
# the single source of truth for validation and hashing.
PlanValidationError = IntelligentEditingValidationError


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


plan_hash = canonical_hash
planner_payload = build_plan_payload
validate_plan = normalize_plan


def validate_sha256(value: Any, field: str = "plan_hash") -> str:
    if not isinstance(value, str) or not SHA256.fullmatch(value):
        raise IntelligentEditingValidationError(f"{field} must be a lowercase SHA-256 value")
    return value


__all__ = [
    "IntelligentEditingValidationError",
    "PlanValidationError",
    "PLAN_VERSION",
    "RUN_VERSION",
    "build_plan_payload",
    "build_render_payload",
    "canonical_hash",
    "canonical_json",
    "make_local_plan",
    "normalize_plan",
    "plan_hash",
    "planner_payload",
    "validate_managed_asset_id",
    "validate_plan",
    "validate_request",
    "validate_sha256",
]
