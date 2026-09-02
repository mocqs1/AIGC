"""Safe orchestration boundary for ordered model outfit-swap requests.

The Harness accepts only opaque managed asset IDs.  It never resolves source
paths, builds provider requests, or returns provider responses verbatim.  The
local API remains responsible for resolving assets and running the configured
image provider.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from harness.intelligent_editing.runner import HarnessError, LocalApiClient
from skills.intelligent_editing.runtime import validate_managed_asset_id
from skills.model_outfit_swap.runtime import (
    MAX_REFERENCE_IMAGES,
    OUTFIT_IMAGE_PROVIDERS,
    OutfitSwapRequestError,
    _guard_text,
)
from skills.model_outfit_swap.quality_check import garment_fidelity_contract


_MAX_REFERENCES = MAX_REFERENCE_IMAGES
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
_SAFE_TEXT = re.compile(r"^[^\x00-\x1f\x7f]{1,2000}$")
_PATH_OR_URL = re.compile(
    r"(?i)(?:[a-z]:[\\/]|(?:^|\s)/(?:home|users|root|tmp|var|etc|opt|mnt)(?:[\\/]|$)|\b(?:https?|file)://)"
)
_SECRET = re.compile(r"(?i)\b(?:api[_-]?key|authorization|bearer|secret|password|token)\b")


class ModelOutfitSwapHarnessError(HarnessError):
    """Stable, user-safe model outfit-swap Harness failure."""


def _error(code: str, message: str) -> ModelOutfitSwapHarnessError:
    return ModelOutfitSwapHarnessError(code, message)


def _asset_value(item: Any, index: int) -> tuple[str, str | None]:
    if isinstance(item, str):
        return item, None
    if not isinstance(item, Mapping):
        raise _error("invalid_reference", f"reference {index + 1} must be a managed asset")
    unknown = set(item) - {"asset_id", "id", "value", "role", "media_type"}
    if unknown:
        raise _error("invalid_reference", f"reference {index + 1} contains unsupported fields")
    value = item.get("asset_id", item.get("id", item.get("value")))
    role = item.get("role")
    if role is not None and role not in {"model", "outfit"}:
        raise _error("invalid_reference_role", "reference role must be model or outfit")
    media_type = item.get("media_type")
    if media_type is not None and media_type != "image":
        raise _error("invalid_media_type", "model outfit swap accepts image assets only")
    if not isinstance(value, str):
        raise _error("invalid_reference", f"reference {index + 1} must contain an asset ID")
    return value, role


def _looks_like_image_asset(asset_id: str) -> bool:
    # Managed IDs normally retain the imported extension.  Extensionless IDs
    # are allowed so the API catalog can perform the authoritative type check.
    if asset_id.split("/", 1)[0] == "videos":
        return False
    suffix = asset_id.rsplit("/", 1)[-1].lower()
    if "." not in suffix:
        return True
    return any(suffix.endswith(extension) for extension in _IMAGE_EXTENSIONS)


def validate_outfit_selection(references: Sequence[Any]) -> dict[str, Any]:
    """Normalize an ordered selection into one model and garment references."""
    if isinstance(references, (str, bytes, bytearray)) or not isinstance(references, Sequence):
        raise _error("invalid_references", "references must be an ordered list of 2 to 10 images")
    if not 2 <= len(references) <= _MAX_REFERENCES:
        raise _error("invalid_references", "model outfit swap requires 2 to 10 images")

    ordered: list[str] = []
    roles: list[str] = []
    for index, item in enumerate(references):
        value, declared_role = _asset_value(item, index)
        try:
            asset_id = validate_managed_asset_id(value)
        except Exception as error:
            raise _error("invalid_asset_id", "references must use managed asset IDs") from error
        if not _looks_like_image_asset(asset_id):
            raise _error("invalid_media_type", "model outfit swap accepts image assets only")
        expected_role = "model" if index == 0 else "outfit"
        if declared_role is not None and declared_role != expected_role:
            raise _error("invalid_reference_role", "reference role mismatch: the first image must be model; later images must be outfit")
        if asset_id in ordered:
            raise _error("duplicate_reference", "model and outfit references must be distinct")
        ordered.append(asset_id)
        roles.append(expected_role)
    return {
        "input_order": ordered,
        "model_asset_id": ordered[0],
        "outfit_asset_ids": ordered[1:],
        "roles": roles,
    }


def _safe_brief(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _error("invalid_request", f"{field} must be a non-empty string")
    text = value.strip()
    if not _SAFE_TEXT.fullmatch(text) or _PATH_OR_URL.search(text) or _SECRET.search(text):
        raise _error("invalid_request", f"{field} contains unsupported data")
    try:
        _guard_text(text)
    except OutfitSwapRequestError as error:
        raise _error("invalid_request", "request may only change the model's clothing") from error
    return text


def build_outfit_request(
    references: Sequence[Any],
    request: Mapping[str, Any] | None = None,
    *,
    provider: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build an API payload and role manifest without exposing source paths."""
    selection = validate_outfit_selection(references)
    raw_request = dict(request or {})
    allowed_request = {"prompt", "product", "scene", "style"}
    if set(raw_request) - allowed_request:
        raise _error("invalid_request", "request contains unsupported fields")
    # Validate legacy briefs for safety, but never forward them. The API owns
    # the fixed prompt contract for this workflow.
    for key, value in raw_request.items():
        if value is not None:
            _safe_brief(value, key)
    payload: dict[str, Any] = {
        "mode": "model_outfit_swap",
        "request": {},
        "reference_images": [
            {"kind": "asset", "value": asset_id}
            for asset_id in selection["input_order"]
        ],
    }
    if provider is not None:
        normalized_provider = provider.strip().lower() if isinstance(provider, str) else ""
        if normalized_provider == "liblib":
            normalized_provider = "hermes_volcano"
        if normalized_provider not in OUTFIT_IMAGE_PROVIDERS:
            raise _error("invalid_provider", "model outfit swap supports Hermes and Hermes Volcano providers")
        payload["provider"] = normalized_provider
    return payload, selection


def _safe_job_response(response: Any) -> dict[str, Any]:
    if not isinstance(response, Mapping):
        raise _error("invalid_response", "image service returned an invalid job response")
    safe: dict[str, Any] = {}
    for key in ("job_id", "status", "phase", "mode", "provider", "created_at", "updated_at", "model", "model_name"):
        value = response.get(key)
        if isinstance(value, (int, float, bool)):
            safe[key] = value
        elif isinstance(value, str) and _SAFE_TEXT.fullmatch(value.strip()) and not _PATH_OR_URL.search(value) and not _SECRET.search(value):
            # Keep lifecycle metadata useful while dropping provider detail
            # that could contain a path, URL, token, or raw error text.
            safe[key] = value.strip()
    outputs = response.get("outputs")
    if isinstance(outputs, list):
        safe_outputs: list[str] = []
        for output in outputs:
            if not isinstance(output, str) or not output.startswith("/api/assets/"):
                continue
            asset_id = output.removeprefix("/api/assets/").split("?", 1)[0].split("#", 1)[0]
            try:
                safe_id = validate_managed_asset_id(asset_id)
            except Exception:
                continue
            safe_outputs.append(f"/api/assets/{safe_id}")
        safe["outputs"] = safe_outputs
    return safe


def _role_manifest(selection: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "input_order": list(selection["input_order"]),
        "model_asset_id": selection["model_asset_id"],
        "outfit_asset_ids": list(selection["outfit_asset_ids"]),
        "reference_roles": list(selection["roles"]),
    }


def _fidelity_manifest() -> dict[str, Any]:
    """Expose the same review contract used by generated artifact checks."""
    return garment_fidelity_contract()


def _manual_review_items() -> list[str]:
    return [
        "verify the adult model identity, face, hair, expression, skin, body proportions, pose, hands, feet, and anatomy remain unchanged",
        "verify only clothing changed and the background, objects, lighting, shadows, camera, viewpoint, framing, crop, and aspect ratio remain unchanged",
        "verify the selected garment is the same design with zero tolerated deviation in material, fabric behavior, color, pattern, construction, components, and finish",
        "compare every garment reference at full-garment and close-up scale; reject missing, smoothed, simplified, substituted, recolored, or invented details",
    ]


class ModelOutfitSwapHarness:
    """Preview and submit a role-safe model outfit-swap request."""

    def __init__(
        self,
        api: LocalApiClient | Callable[[str, str, Any | None], Any] | None = None,
        *,
        transport: Callable[[str, str, Any | None], Any] | None = None,
    ) -> None:
        if api is not None and not hasattr(api, "request") and callable(api):
            self.api = LocalApiClient(transport=api)
        else:
            self.api = api or LocalApiClient(transport=transport)

    @staticmethod
    def validate_selection(references: Sequence[Any]) -> dict[str, Any]:
        return validate_outfit_selection(references)

    @staticmethod
    def build_request(
        references: Sequence[Any],
        request: Mapping[str, Any] | None = None,
        *,
        provider: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        return build_outfit_request(references, request, provider=provider)

    def preview(self, references: Sequence[Any], request: Mapping[str, Any] | None = None, *, provider: str | None = None) -> dict[str, Any]:
        payload, selection = build_outfit_request(references, request, provider=provider)
        # When a catalog is available, use it to reject extensionless video IDs
        # before submission.  The API remains the source of truth for media type.
        try:
            catalog = self.api.request("GET", "/api/assets")
        except Exception:
            catalog = None
        if isinstance(catalog, Mapping) and isinstance(catalog.get("assets"), list):
            by_id = {item.get("id"): item for item in catalog["assets"] if isinstance(item, Mapping)}
            for asset_id in selection["input_order"]:
                record = by_id.get(asset_id)
                if record is not None and record.get("media_type") != "image":
                    raise _error("invalid_media_type", "model outfit swap accepts image assets only")
        manifest = {
            "skill": "model-outfit-swap",
            "workflow": "model_outfit_swap",
            "type": "image",
            **_role_manifest(selection),
            "garment_fidelity": _fidelity_manifest(),
            "request": copy.deepcopy(payload),
            "manual_review": _manual_review_items(),
        }
        return {**manifest, "manifest": copy.deepcopy(manifest)}

    def submit(self, references: Sequence[Any], request: Mapping[str, Any] | None = None, *, provider: str | None = None) -> dict[str, Any]:
        payload, selection = build_outfit_request(references, request, provider=provider)
        response = self.api.request("POST", "/api/generations", payload)
        manifest = {
            "skill": "model-outfit-swap",
            "workflow": "model_outfit_swap",
            "type": "image",
            **_role_manifest(selection),
            "garment_fidelity": _fidelity_manifest(),
            "job": _safe_job_response(response),
            "manual_review": _manual_review_items(),
        }
        return {**manifest, "manifest": copy.deepcopy(manifest)}

    generate = submit
    run = submit


ModelOutfitSwapError = ModelOutfitSwapHarnessError
validate_selection = validate_outfit_selection
build_request = build_outfit_request

__all__ = [
    "ModelOutfitSwapError",
    "ModelOutfitSwapHarness",
    "ModelOutfitSwapHarnessError",
    "build_outfit_request",
    "build_request",
    "validate_outfit_selection",
    "validate_selection",
]
