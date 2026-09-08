"""Local web API for the AIGC Studio workbench.

The API deliberately keeps provider credentials server-side. Generation runs in
background threads because the existing engine exposes synchronous functions.
"""

from __future__ import annotations

import base64
import copy
import ctypes
import ipaddress
import json
import logging
import mimetypes
import os
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import uuid
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from ctypes import wintypes
from urllib.parse import unquote, urlparse

from fastapi import FastAPI, File, Form, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator, model_validator

from agents.prompt_agent import generate_prompt
from config import OUTPUTS_DIR, PROJECT_ROOT, env_value
from providers.credential_parser import parse_api_key, parse_api_url, parse_provider_settings
from main import generate_image, generate_video
from skills.shapewear_video_generator.runtime import (
    ShapewearRequestError,
    _build_prompt as build_shapewear_prompt,
    generate_image as generate_shapewear_image,
    generate_video as generate_shapewear_video,
)
from skills.model_outfit_swap.runtime import (
    FIXED_OUTFIT_PROMPT,
    MAX_REFERENCE_IMAGES as MAX_OUTFIT_REFERENCE_IMAGES,
    generate_image as generate_model_outfit_image,
    outfit_prompt_for_provider,
)
from skills.clothing_image_to_image.runtime import (
    ClothingImageRequestError,
    build_prompt as build_clothing_image_prompt,
    generate_image as generate_clothing_image,
)
from skills.tiktok_clothing_main_image.runtime import (
    TikTokClothingRequestError,
    build_prompt as build_tiktok_clothing_prompt,
    generate_image as generate_tiktok_clothing_image,
)
from skills.mono_color_poster.runtime import (
    PosterRequestError,
    build_prompt as build_poster_prompt,
    generate_image as generate_poster_image,
)
from uploader.config import R2Config
from uploader.exceptions import R2ConfigurationError, R2UploadError, R2ValidationError
from uploader.service import upload_image as upload_r2_image
from uploader.service import upload_video as upload_r2_video
from providers.image.hermes_client import HermesClient, HermesClientError, HermesRequestError
from providers.liblib.client import LiblibClient, LiblibClientError
from providers.video.google_veo_provider import GoogleVeoClient
from providers.video.rest_client import VideoProviderRequestError
from providers.video.seedance_provider import SeedanceClient
from providers.mix.codex_terra_planner import CodexTerraPlanner, TerraPlannerError
from providers.gateway_adapter import (
    ARK_HOST,
    GatewayCategoryMismatch,
    GatewayDetectionError,
    detect_gateway,
    hostname_of,
    match_protocol,
    normalize_provider_models as _normalize_gateway_models,
    suggested_name,
    suggested_slug,
)
from cryptography.fernet import Fernet, InvalidToken


PROVIDER_LOGGER = logging.getLogger("uvicorn.error")


APP_ROOT = PROJECT_ROOT / "web" / "dist"
JOB_STORE_PATH = OUTPUTS_DIR / ".aigc_studio_jobs.json"
HERMES_SETTINGS_PATH = OUTPUTS_DIR / ".aigc_studio_hermes.json"
HERMES_KEY_PATH = OUTPUTS_DIR / ".aigc_studio_hermes.key"
MODULE_SETTINGS_PATH = OUTPUTS_DIR / ".aigc_studio_modules.json"
BATCH_STORE_PATH = OUTPUTS_DIR / ".aigc_studio_batches.json"
MIX_STORE_PATH = OUTPUTS_DIR / ".aigc_studio_mixes.json"
R2_UPLOAD_STORE_PATH = OUTPUTS_DIR / ".aigc_studio_r2_uploads.json"
OUTPUT_ROOTS = (OUTPUTS_DIR / "images", OUTPUTS_DIR / "videos", OUTPUTS_DIR / "shapewear", OUTPUTS_DIR / "clothing_image", OUTPUTS_DIR / "tiktok_clothing", OUTPUTS_DIR / "posters", OUTPUTS_DIR / "imported")
JOB_STATES = {
    "queued",
    "preparing_prompt",
    "submitted",
    "running",
    "saving",
    "quality_check",
    "succeeded",
    "failed",
}
MODE_LABELS = {
    "tiktok_clothing_image": "TikTok Clothing Main Image",
    "model_outfit_swap": "模特换装",
    "clothing_image_to_image": "服装工艺图",
    "image": "图片",
    "video": "视频",
    "shapewear_image": "塑身衣商品图",
    "shapewear_video": "模特试穿视频",
    "tiktok_10s": "TikTok 10s 广告",
    "poster": "单色海报",
}
JOB_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="aigc-generation")
BATCH_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="aigc-batch")
MIX_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="aigc-mix")
JOBS: dict[str, "GenerationJob"] = {}
JOBS_LOCK = threading.RLock()
R2_UPLOADS: dict[str, dict[str, Any]] = {}
R2_UPLOADS_LOCK = threading.RLock()
BATCHES: dict[str, dict[str, Any]] = {}
BATCHES_LOCK = threading.RLock()
MIXES: dict[str, dict[str, Any]] = {}
MIXES_LOCK = threading.RLock()
MODULE_SETTINGS_LOCK = threading.RLock()

MODULE_SPECS: dict[str, dict[str, Any]] = {
    "image.hermes": {"name": "Hermes 图片生成", "category": "image", "env_prefix": "HERMES", "model": "gpt-image-2", "api_url": "https://aiapi.yicheng.bj.cn/v1", "capabilities": ["generate_image", "reference_image"], "options": {"submit_path": "/images/generations", "edit_path": "/images/edits", "status_path": "/images/generations/{task_id}", "result_path": "/images/generations/{task_id}"}},
    # Keep the old module id as a migration-compatible second Hermes slot,
    # now backed by the independent Volcengine Ark/Seedream configuration.
    "image.liblib": {"name": "Hermes 火山图片生成", "category": "image", "env_prefix": "HERMES_VOLCANO", "model": "doubao-seedream-5-0-pro-260628", "api_url": "https://ark.cn-beijing.volces.com/api/v3", "capabilities": ["generate_image", "reference_image"], "options": {"submit_path": "/images/generations", "status_path": "/images/generations/{task_id}", "result_path": "/images/generations/{task_id}"}},
    "video.veo": {"name": "Google Veo 视频生成", "category": "video", "env_prefix": "VEO", "model": "veo-3.1-fast-generate-preview", "api_url": "https://generativelanguage.googleapis.com/v1beta", "capabilities": ["generate_video"], "options": {}},
    "video.seedance": {"name": "Seedance 视频生成", "category": "video", "env_prefix": "SEEDANCE", "model": "doubao-seedance-2-0-260128", "api_url": "https://ark.cn-beijing.volces.com/api/v3", "capabilities": ["generate_video"], "options": {"submit_path": "/contents/generations/tasks", "status_path": "/contents/generations/tasks/{task_id}", "result_path": "/contents/generations/tasks/{task_id}", "timeout": "180"}},
    "mix.codex_terra": {"name": "Codex 5.6 Terra 智能规划", "category": "mix_planner", "env_prefix": "CODEX_TERRA", "model": "gpt-5.6-terra", "api_url": "", "capabilities": ["plan_mix"], "options": {}},
}
MODULE_OPTION_KEYS = {module_id: set(spec["options"]) for module_id, spec in MODULE_SPECS.items()}
for _image_module in ("image.hermes", "image.liblib"):
    MODULE_OPTION_KEYS[_image_module].update({"timeout", "query_timeout", "result_timeout", "download_timeout"})
MODULE_OPTION_KEYS["video.seedance"].add("timeout")
BUILTIN_MODULE_IDS = frozenset(MODULE_SPECS)
PROVIDER_MODULE_ALIASES = {
    "liblib": "image.liblib",
    "hermes": "image.hermes",
    "hermes_volcano": "image.liblib",
    "veo": "video.veo",
    "seedance": "video.seedance",
}
BUILTIN_IMAGE_PROVIDERS = {"liblib", "hermes", "hermes_volcano"}
BUILTIN_VIDEO_PROVIDERS = {"veo", "seedance"}
LOCKED_IMAGE_PROVIDERS = {"liblib", "hermes", "hermes_volcano"}
CUSTOM_MODULE_ID_RE = re.compile(r"^(image|video|mix)\.[a-z][a-z0-9_]{0,31}$")
CATEGORY_PREFIX = {"image": "image", "video": "video", "mix_planner": "mix"}
RESERVED_CUSTOM_SLUGS = {"hermes", "liblib", "veo", "seedance", "codex_terra"}
MAX_CUSTOM_MODULES = 16


def _protocol_for_module(module_id: str) -> str:
    spec = MODULE_SPECS.get(module_id)
    if spec and spec.get("kind") == "custom" and spec.get("protocol") in BUILTIN_MODULE_IDS:
        return str(spec["protocol"])
    if module_id in BUILTIN_MODULE_IDS:
        return module_id
    raise KeyError(module_id)

def _occupied_custom_slugs() -> set[str]:
    occupied = set(RESERVED_CUSTOM_SLUGS)
    for module_id in (*MODULE_SPECS, *_stored_module_records()):
        if isinstance(module_id, str) and "." in module_id:
            occupied.add(module_id.split(".", 1)[1])
    return occupied


def _runtime_protocol(module_id: str, api_url: str, model: str = "") -> str:
    stored = _protocol_for_module(module_id)
    spec = MODULE_SPECS[module_id]
    if spec.get("kind") != "custom":
        return stored
    return match_protocol(api_url, model=model or "", category=spec["category"], stored_protocol=stored)


_TIMEOUT_OPTION_KEYS = frozenset({"timeout", "query_timeout", "result_timeout", "download_timeout"})


def _uses_ark_image_gateway(api_url: str, model: str = "") -> bool:
    host = hostname_of(api_url)
    return host == ARK_HOST or (host.endswith(".volces.com") and host.startswith("ark.")) or "seedream" in (model or "").lower()


def _module_client_kwargs(protocol: str, options: Mapping[str, str]) -> dict[str, Any]:
    allowed = MODULE_OPTION_KEYS.get(protocol, set())
    kwargs: dict[str, Any] = {}
    for key, value in options.items():
        if key not in allowed:
            continue
        kwargs[key] = float(value) if key in _TIMEOUT_OPTION_KEYS else value
    return kwargs


def _hydrate_custom_module(module_id: str, record: Mapping[str, Any]) -> None:
    if module_id in BUILTIN_MODULE_IDS or not CUSTOM_MODULE_ID_RE.fullmatch(module_id):
        return
    protocol = record.get("protocol")
    if protocol not in BUILTIN_MODULE_IDS:
        return
    template = MODULE_SPECS[protocol]
    if template.get("kind") == "custom":
        return
    prefix = CATEGORY_PREFIX[template["category"]]
    if not module_id.startswith(f"{prefix}."):
        return
    slug = module_id.split(".", 1)[1]
    if slug in RESERVED_CUSTOM_SLUGS:
        return
    name = record.get("name")
    MODULE_SPECS[module_id] = {
        "name": name.strip() if isinstance(name, str) and name.strip() else template["name"],
        "category": template["category"],
        "env_prefix": f"CUSTOM_{module_id.replace('.', '_').upper()}",
        "model": template["model"],
        "api_url": template["api_url"],
        "capabilities": list(template["capabilities"]),
        "options": dict(template["options"]),
        "protocol": protocol,
        "kind": "custom",
    }
    MODULE_OPTION_KEYS[module_id] = set(MODULE_OPTION_KEYS[protocol])


def _forget_custom_module(module_id: str) -> None:
    if module_id in BUILTIN_MODULE_IDS:
        return
    MODULE_SPECS.pop(module_id, None)
    MODULE_OPTION_KEYS.pop(module_id, None)


def _registered_module_ids() -> list[str]:
    _stored_module_records()
    return list(MODULE_SPECS)


def _module_id_for_provider(name: str | None) -> str | None:
    if not isinstance(name, str) or not name.strip():
        return None
    normalized = name.strip().lower()
    if normalized in PROVIDER_MODULE_ALIASES:
        return PROVIDER_MODULE_ALIASES[normalized]
    if normalized in MODULE_SPECS:
        return normalized
    stored = _stored_module_records()
    if normalized in stored:
        return normalized
    return None


def _public_provider_id(module_id: str) -> str:
    if module_id == "image.hermes":
        return "hermes"
    if module_id == "image.liblib":
        return "hermes_volcano"
    if module_id == "video.veo":
        return "veo"
    if module_id == "video.seedance":
        return "seedance"
    return module_id


def _is_image_provider(name: str) -> bool:
    if name in BUILTIN_IMAGE_PROVIDERS:
        return True
    module_id = _module_id_for_provider(name)
    spec = MODULE_SPECS.get(module_id or "")
    return bool(spec and spec.get("category") == "image")


def _is_video_provider(name: str) -> bool:
    if name in BUILTIN_VIDEO_PROVIDERS:
        return True
    module_id = _module_id_for_provider(name)
    spec = MODULE_SPECS.get(module_id or "")
    return bool(spec and spec.get("category") == "video")


def _custom_module_id(category: str, slug: str) -> str:
    normalized_slug = slug.strip().lower()
    prefix = CATEGORY_PREFIX[category]
    module_id = f"{prefix}.{normalized_slug}"
    if not CUSTOM_MODULE_ID_RE.fullmatch(module_id):
        raise ValueError("自定义模块 ID 只能使用小写字母、数字和下划线")
    if normalized_slug in RESERVED_CUSTOM_SLUGS or module_id in BUILTIN_MODULE_IDS:
        raise ValueError("不能覆盖内置模块 ID")
    return module_id




class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _dpapi_secret(value: bytes, *, protect: bool) -> bytes:
    buffer = ctypes.create_string_buffer(value)
    source = _DataBlob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    target = _DataBlob()
    function = ctypes.windll.crypt32.CryptProtectData if protect else ctypes.windll.crypt32.CryptUnprotectData
    arguments = (ctypes.byref(source), None, None, None, None, 0x1, ctypes.byref(target))
    if not function(*arguments):
        raise OSError(ctypes.get_last_error(), "Windows DPAPI operation failed")
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(target.pbData)


def _protect_hermes_key(api_key: str) -> str:
    if not api_key:
        return ""
    if os.name == "nt":
        return "dpapi:" + base64.b64encode(_dpapi_secret(api_key.encode("utf-8"), protect=True)).decode("ascii")
    if HERMES_KEY_PATH.exists():
        key_material = HERMES_KEY_PATH.read_bytes()
        Fernet(key_material)
    else:
        key_material = Fernet.generate_key()
        HERMES_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
        HERMES_KEY_PATH.write_bytes(key_material)
    return "fernet:" + Fernet(key_material).encrypt(api_key.encode("utf-8")).decode("ascii")


def _unprotect_hermes_key(value: str) -> str:
    if value.startswith("dpapi:") and os.name == "nt":
        return _dpapi_secret(base64.b64decode(value[6:], validate=True), protect=False).decode("utf-8")
    if value.startswith("fernet:"):
        return Fernet(HERMES_KEY_PATH.read_bytes()).decrypt(value[7:].encode("ascii")).decode("utf-8")
    return ""


class ReferenceImage(BaseModel):
    kind: Literal["asset", "url"]
    value: str = Field(min_length=1, max_length=4096)

    @field_validator("value")
    @classmethod
    def trim_value(cls, value: str) -> str:
        return value.strip()


def _is_public_https_url(value: str) -> bool:
    """Accept only public HTTPS URLs for remote reference images."""
    try:
        parsed = urlparse(value)
        host = parsed.hostname
        if parsed.scheme.lower() != "https" or not host:
            return False
        normalized = host.rstrip(".").lower()
        if normalized == "localhost" or normalized.endswith((".localhost", ".local", ".internal")):
            return False
        try:
            address = ipaddress.ip_address(normalized)
        except ValueError:
            return True
        return not any((address.is_private, address.is_loopback, address.is_link_local, address.is_reserved, address.is_unspecified))
    except ValueError:
        return False


_REFERENCE_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
_REFERENCE_IMAGE_MODES = {"image", "shapewear_image", "model_outfit_swap", "clothing_image_to_image", "tiktok_clothing_image", "poster"}


def _resolve_reference_images(references: list[ReferenceImage]) -> list[str]:
    """Validate and resolve image references to local paths or public URLs."""
    resolved: list[str] = []
    for index, reference in enumerate(references):
        if reference.kind == "url":
            if not _is_public_https_url(reference.value):
                raise ValueError(f"reference_images[{index}] must be a public HTTPS image URL")
            resolved.append(reference.value)
            continue
        try:
            path = Path(resolve_asset(reference.value))
        except ValueError as error:
            raise ValueError(f"reference_images[{index}] asset is invalid: {error}") from error
        if path.suffix.lower() not in _REFERENCE_IMAGE_SUFFIXES:
            raise ValueError(f"reference_images[{index}] asset must be an image file")
        resolved.append(str(path))
    if len(set(resolved)) != len(resolved):
        raise ValueError("reference_images must contain distinct images")
    return resolved


class GenerationRequest(BaseModel):
    mode: Literal["image", "model_outfit_swap", "clothing_image_to_image", "tiktok_clothing_image", "video", "shapewear_image", "shapewear_video", "tiktok_10s", "poster"]
    request: dict[str, Any] = Field(default_factory=dict)
    provider: str | None = Field(default=None, max_length=64)
    reference_image: ReferenceImage | None = None
    reference_images: list[ReferenceImage] = Field(default_factory=list, max_length=16)

    @field_validator("request")
    @classmethod
    def normalize_request(cls, value: dict[str, Any]) -> dict[str, Any]:
        return {str(key): candidate.strip() if isinstance(candidate, str) else candidate for key, candidate in value.items()}

    @model_validator(mode="after")
    def validate_contract(self) -> "GenerationRequest":
        if isinstance(self.provider, str):
            self.provider = self.provider.strip().lower() or None
        if self.mode == "clothing_image_to_image":
            if self.provider not in {None, "hermes"}:
                raise ValueError("clothing image-to-image requires the Hermes provider")
            self.provider = "hermes"
        if self.mode == "tiktok_clothing_image" and self.provider == "liblib":
            self.provider = "hermes_volcano"
        if self.mode == "tiktok_clothing_image" and self.provider not in {None, "hermes", "hermes_volcano"}:
            raise ValueError("TikTok clothing images support Hermes and Hermes Volcano providers")
        if self.mode == "shapewear_image" and self.provider is None:
            self.provider = "hermes"
        if self.mode == "shapewear_image" and self.provider == "liblib":
            # Keep the old public alias accepted while persisting the current
            # canonical name used by the second Hermes/Volcano module.
            self.provider = "hermes_volcano"
        if self.mode == "shapewear_image" and self.provider not in {None, "hermes", "hermes_volcano"}:
            raise ValueError("shapewear product images require the Hermes or Hermes Volcano image provider")
        if self.mode in {"image", "poster"}:
            if self.provider is None:
                self.provider = "hermes"
            elif self.provider == "liblib":
                self.provider = "hermes_volcano"
            elif not _is_image_provider(self.provider):
                raise ValueError("图片模式仅支持图片 Provider")
        if self.mode == "tiktok_clothing_image" and self.provider is None:
            self.provider = "hermes"
        if self.mode in {"video", "shapewear_video", "tiktok_10s"}:
            if self.provider is None:
                self.provider = "veo"
            elif not _is_video_provider(self.provider):
                raise ValueError("视频模式仅支持视频 Provider")
        if self.reference_image and self.mode in _REFERENCE_IMAGE_MODES:
            raise ValueError("single reference_image is only supported for video modes")
        if self.mode == "model_outfit_swap":
            if self.provider == "liblib":
                self.provider = "hermes_volcano"
            if self.provider not in {None, "hermes", "hermes_volcano"}:
                raise ValueError("model outfit swap supports Hermes and Hermes Volcano providers")
            self.provider = self.provider or "hermes"
            if len(self.reference_images) < 2:
                raise ValueError("model outfit swap requires a model image and at least one outfit image")
            if len(self.reference_images) > MAX_OUTFIT_REFERENCE_IMAGES:
                raise ValueError(
                    f"model outfit swap supports at most {MAX_OUTFIT_REFERENCE_IMAGES} reference images"
                )
            # The outfit workflow has no user-authored generation brief. Drop
            # any legacy fields before the request is persisted or queued.
            self.request = {}
        if self.reference_images:
            if self.mode not in _REFERENCE_IMAGE_MODES:
                raise ValueError("reference_images are only supported for image modes")
            if self.mode == "clothing_image_to_image" and len(self.reference_images) != 1:
                raise ValueError("clothing image-to-image requires exactly one reference_image")
            if self.mode == "tiktok_clothing_image" and not 1 <= len(self.reference_images) <= 10:
                raise ValueError("TikTok clothing images require one master and at most nine detail references")
            if self.mode == "shapewear_image" and len(self.reference_images) > 10:
                raise ValueError("shapewear product images support at most 10 ordered reference images")
            if self.mode == "poster" and len(self.reference_images) > 1:
                raise ValueError("poster accepts at most one reference image")
            if self.mode not in {"clothing_image_to_image", "tiktok_clothing_image", "shapewear_image", "poster"} and len(self.reference_images) < 2:
                raise ValueError("image mode requires at least two reference_images")
            resolved_references = _resolve_reference_images(self.reference_images)
            if self.mode == "clothing_image_to_image" and self.reference_images[0].kind == "url":
                parsed = urlparse(resolved_references[0])
                if (
                    parsed.username
                    or parsed.password
                    or parsed.query
                    or parsed.fragment
                    or Path(parsed.path).suffix.lower() not in _REFERENCE_IMAGE_SUFFIXES
                ):
                    raise ValueError("clothing image reference must be a public HTTPS image URL without credentials or query parameters")
        elif self.mode == "clothing_image_to_image":
            raise ValueError("clothing image-to-image requires exactly one reference_image")
        elif self.mode == "tiktok_clothing_image":
            raise ValueError("TikTok clothing images require one product master reference image")
        if (
            self.reference_image
            and self.reference_image.kind == "url"
            and not _is_public_https_url(self.reference_image.value)
        ):
            raise ValueError("远程参考图仅支持公网 HTTPS 图片 URL")
        if self.provider == "seedance" and self.reference_image:
            if self.reference_image.kind == "url" and not _is_public_https_url(self.reference_image.value):
                raise ValueError("Seedance 远程参考图仅支持公网 HTTPS 图片 URL")
        return self


class PromptPreviewRequest(BaseModel):
    mode: Literal["image", "model_outfit_swap", "clothing_image_to_image", "tiktok_clothing_image", "video", "shapewear_image", "shapewear_video", "tiktok_10s", "poster"]
    request: dict[str, Any] = Field(default_factory=dict)
    provider: str | None = Field(default=None, max_length=64)


class R2UploadRequest(BaseModel):
    object_key: str | None = Field(default=None, max_length=1024)
    overwrite: bool = False

    @field_validator("object_key")
    @classmethod
    def trim_object_key(cls, value: str | None) -> str | None:
        return value.strip() if value else None


def _normalize_pasted_settings_payload(value: Any) -> Any:
    """Accept console/curl pastes without changing the write-only key contract."""
    if not isinstance(value, Mapping):
        return value
    payload = dict(value)
    url = payload.get("api_url")
    key = payload.get("api_key")
    parsed_key = parse_api_key(key) if isinstance(key, str) or key is None else ""
    if parsed_key:
        payload["api_key"] = parsed_key
    elif isinstance(key, str):
        payload["api_key"] = None
    if not isinstance(url, str):
        return payload
    try:
        parsed_url, recovered_key = parse_provider_settings(url, parsed_key or None)
    except ValueError:
        return payload
    payload["api_url"] = parsed_url
    if recovered_key:
        payload["api_key"] = recovered_key
    return payload


class HermesSettingsRequest(BaseModel):
    api_url: str = Field(min_length=1, max_length=2048)
    api_key: str | None = Field(default=None, max_length=4096)
    clear_api_key: bool = False
    model: str = Field(default="gpt-image-2", min_length=1, max_length=256)
    submit_path: str = Field(default="/images/generations", max_length=512)
    edit_path: str = Field(default="/images/edits", max_length=512)
    status_path: str = Field(default="/images/generations/{task_id}", max_length=512)
    result_path: str = Field(default="/images/generations/{task_id}", max_length=512)
    timeout: float = Field(default=480.0, gt=0, le=600)

    @model_validator(mode="before")
    @classmethod
    def normalize_pasted_credentials(cls, value: Any) -> Any:
        return _normalize_pasted_settings_payload(value)


class ModuleSettingsRequest(BaseModel):
    api_url: str = Field(min_length=1, max_length=2048)
    api_key: str | None = Field(default=None, max_length=4096)
    clear_api_key: bool = False
    model: str = Field(default="", max_length=256)
    enabled: bool = True
    options: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def normalize_pasted_credentials(cls, value: Any) -> Any:
        return _normalize_pasted_settings_payload(value)

    @field_validator("api_url", "model")
    @classmethod
    def trim_module_strings(cls, value: str) -> str:
        return value.strip()

    @field_validator("options")
    @classmethod
    def validate_option_values(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > 8:
            raise ValueError("配置选项过多")
        cleaned: dict[str, str] = {}
        for key, candidate in value.items():
            if not isinstance(key, str) or not isinstance(candidate, str) or len(key) > 64 or len(candidate) > 512:
                raise ValueError("配置选项格式无效")
            cleaned[key] = candidate.strip()
        return cleaned


class CreateCustomModuleRequest(BaseModel):
    name: str = Field(default="", max_length=64)
    category: Literal["image", "video", "mix_planner"]
    protocol: str = Field(default="", max_length=64)
    slug: str = Field(default="", max_length=32)
    api_url: str = Field(min_length=1, max_length=2048)
    api_key: str | None = Field(default=None, max_length=4096)
    model: str = Field(default="", max_length=256)
    enabled: bool = True
    options: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def normalize_pasted_credentials(cls, value: Any) -> Any:
        return _normalize_pasted_settings_payload(value)

    @field_validator("name", "protocol", "slug", "api_url", "model")
    @classmethod
    def trim_create_strings(cls, value: str) -> str:
        return value.strip()

    @field_validator("options")
    @classmethod
    def validate_option_values(cls, value: dict[str, str]) -> dict[str, str]:
        return ModuleSettingsRequest.validate_option_values(value)



class GatewayDetectRequest(BaseModel):
    api_url: str = Field(min_length=1, max_length=2048)
    api_key: str | None = Field(default=None, max_length=4096)
    category: Literal["image", "video", "mix_planner"] | None = None
    model: str = Field(default="", max_length=256)

    @model_validator(mode="before")
    @classmethod
    def normalize_pasted_credentials(cls, value: Any) -> Any:
        return _normalize_pasted_settings_payload(value)

    @field_validator("api_url", "model")
    @classmethod
    def trim_detect_strings(cls, value: str) -> str:
        return value.strip()

class BatchItem(BaseModel):
    client_id: str = Field(min_length=1, max_length=128)
    prompt: str = Field(min_length=1, max_length=20000)
    aspect_ratio: str = Field(default="square", max_length=64)
    image_asset_id: str | None = None
    reference_asset_ids: list[str] = Field(default_factory=list, max_length=16)


class BatchOptions(BaseModel):
    max_concurrency: int = Field(default=2, ge=1, le=4)


class BatchRequest(BaseModel):
    items: list[BatchItem] = Field(min_length=1, max_length=100)
    options: BatchOptions = Field(default_factory=BatchOptions)
    idempotency_key: str | None = Field(default=None, max_length=128)


class MixClip(BaseModel):
    asset_id: str = Field(min_length=1, max_length=4096)
    duration_ms: int | None = Field(default=None, ge=500, le=60000)
    start_ms: int = Field(default=0, ge=0, le=3600000)
    end_ms: int | None = Field(default=None, ge=500, le=3600000)

    @model_validator(mode="after")
    def validate_trim_range(self):
        if self.end_ms is not None and self.end_ms <= self.start_ms:
            raise ValueError("视频结束时间必须晚于开始时间")
        if self.end_ms is not None and self.end_ms - self.start_ms > 60000:
            raise ValueError("单个视频片段最长 60 秒")
        return self


class MixPlanClip(MixClip):
    # The current local FFmpeg renderer guarantees hard cuts.  Keeping this
    # explicit prevents a planning provider from claiming an unavailable fade.
    transition: Literal["hard_cut"] = "hard_cut"


class MixPlan(BaseModel):
    version: Literal["aigc-mix-plan/v1"] = "aigc-mix-plan/v1"
    objective: str = Field(default="", max_length=2000)
    target_duration_ms: int = Field(ge=1000, le=300000)
    clips: list[MixPlanClip] = Field(min_length=2, max_length=50)
    planner: Literal["local", "codex_terra"] = "local"
    transition_mode: Literal["hard_cut", "fade", "auto"] = "auto"
    warnings: list[str] = Field(default_factory=list, max_length=10)


class MixPlanRequest(BaseModel):
    clips: list[MixClip] = Field(min_length=2, max_length=50)
    objective: str = Field(default="生成简洁流畅的成片", min_length=1, max_length=2000)
    target_duration_ms: int = Field(default=15000, ge=1000, le=300000)
    transition_mode: Literal["hard_cut", "fade", "auto"] = "auto"


class MixRequest(BaseModel):
    clips: list[MixClip] = Field(min_length=2, max_length=50)
    aspect_ratio: Literal["portrait", "landscape", "square"] = "portrait"
    objective: str = Field(default="生成简洁流畅的成片", min_length=1, max_length=2000)
    target_duration_ms: int | None = Field(default=None, ge=1000, le=300000)
    transition_mode: Literal["hard_cut", "fade", "auto"] = "auto"
    auto_plan: bool = False
    plan: MixPlan | None = None

    @model_validator(mode="after")
    def validate_plan_assets(self):
        if self.plan and self.auto_plan:
            raise ValueError("请提交已有计划或启用自动规划，不能同时使用")
        if self.plan:
            supplied = {clip.asset_id for clip in self.clips}
            planned = [clip.asset_id for clip in self.plan.clips]
            if any(asset_id not in supplied for asset_id in planned):
                raise ValueError("智能混剪计划只能使用已选择的素材")
        return self


@dataclass
class GenerationJob:
    id: str
    payload: GenerationRequest
    status: str = "queued"
    phase: str = "等待处理"
    prompt: str | None = None
    outputs: list[str] = field(default_factory=list)
    quality: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    provider_used: str | None = None
    provider_fallback: dict[str, str] | None = None
    provider_task_id: str | None = None
    created_at: str = field(default_factory=lambda: _now())
    updated_at: str = field(default_factory=lambda: _now())

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "phase": self.phase,
            "mode": self.payload.mode,
            "mode_label": MODE_LABELS[self.payload.mode],
            "provider": self.provider_used or _provider_for_job(self.payload),
            "provider_requested": _provider_for_job(self.payload),
            "provider_fallback": self.provider_fallback,
            "provider_task_id": self.provider_task_id,
            "prompt": self.prompt,
            "outputs": [asset_url(path) for path in self.outputs],
            "quality": self.quality,
            "metadata": self.metadata,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _provider_for_job(payload: GenerationRequest) -> str:
    if payload.mode == "clothing_image_to_image":
        return "hermes"
    if payload.mode in {"image", "model_outfit_swap", "clothing_image_to_image", "tiktok_clothing_image", "shapewear_image", "poster"}:
        # ``liblib`` remains an input-compatible alias for the second Hermes
        # slot so previously queued requests do not select the old client.
        return "hermes_volcano" if payload.provider == "liblib" else (payload.provider or "hermes")
    return payload.provider or "veo"


_IMAGE_PROVIDER_FALLBACKS = {"hermes": "hermes_volcano", "hermes_volcano": "hermes"}


def _is_provider_quota_error(error: Exception) -> bool:
    """Return whether an image-provider error is safe to retry once."""
    text = str(error).lower()
    if re.search(r"\bhttp\s*(?:status\s*)?(?:402|429)\b", text):
        return True
    if re.search(r"\b(?:http\s*)?403\b", text) and re.search(r"quota|credit|balance|capacity|limit|exhaust|配额|额度|余额|限流|资源耗尽", text):
        return True
    return bool(
        re.search(
            r"quota|rate[ -]?limit|insufficient\s+(?:credit|balance|fund|resource)|capacity|out\s+of\s+credit|resource\s+exhausted|credit\s+exhausted|配额|额度|余额不足|限流|资源耗尽",
            text,
        )
    )


def _is_provider_fallback_error(error: Exception) -> bool:
    """Return whether an image provider can be retried with the other slot.

    Reference-image edits use a different upstream capability from text-only
    image generation.  Some gateways surface an unavailable edit capability
    as a 502 ``upstream_error`` (for example, ``access forbidden``) rather
    than a 4xx validation error.  The request has no task id in this case, so
    trying the independently configured image slot is safe.
    """
    if _is_provider_quota_error(error):
        return True
    text = str(error).lower()
    return bool(
        re.search(r"\bhttp\s*(?:status\s*)?(?:502|503|504)\b", text)
        and re.search(r"upstream|access forbidden|gateway|temporar|unavailable", text)
    )


def _safe_message(error: Exception) -> tuple[str, str, bool]:
    message = str(error).strip() or "生成任务失败"
    lowered = message.lower()
    if isinstance(error, HermesRequestError):
        provider_code = (getattr(error, "provider_code", None) or "").strip()
        provider_message = (getattr(error, "provider_message", None) or "").strip()
        if "sensitivecontent" in provider_code.lower() or "sensitive content" in provider_message.lower():
            return (
                "provider_input_rejected",
                "\u53c2\u8003\u56fe\u7247\u88ab Provider \u5185\u5bb9\u5b89\u5168\u5ba1\u6838\u62d2\u7edd\uff0c\u8bf7\u66f4\u6362\u5408\u89c4\u7684\u6a21\u7279\u56fe\u6216\u5546\u54c1\u56fe",
                False,
            )
        if (
            error.status in {401, 403, 502}
            and re.search(r"access\s+forbidden|upstream", f"{provider_code} {provider_message}", re.IGNORECASE)
        ):
            return (
                "provider_capability_unavailable",
                "GPT Image 2 的参考图编辑接口被上游拒绝，请检查模型权限或切换 Hermes 火山图片模型",
                False,
            )
        if error.status is not None and 400 <= error.status < 500:
            return (
                "provider_request_invalid",
                "Provider \u62d2\u7edd\u4e86\u56fe\u7247\u8bf7\u6c42\u53c2\u6570\uff0c\u8bf7\u68c0\u67e5\u6240\u9009\u6a21\u578b\u548c\u53c2\u8003\u56fe",
                False,
            )
    if isinstance(error, (ValueError, ShapewearRequestError, PosterRequestError, TikTokClothingRequestError)):
        return "validation_error", message, False
    if "api_key" in lowered or "required" in lowered and "key" in lowered:
        return "provider_unavailable", "Provider 尚未完成本地配置", False
    if "timeout" in lowered or "timed out" in lowered:
        return "provider_timeout", "Provider 请求超时；上游可能仍在处理中，请先查询任务状态后再决定是否重试", False
    if "failed with http" in lowered or "request failed" in lowered:
        return "provider_request_failed", "Provider 请求失败，请检查服务状态后重试", True
    # Provider exceptions may embed a signed URL or a deployment detail. Keep
    # the diagnostic in server-side logs only and return a stable UI message.
    return "generation_failed", "生成任务失败，请检查 Provider 状态后重试", True


def _structured_request(request: Mapping[str, Any], media_type: str) -> dict[str, Any]:
    prompt = request.get("prompt")
    if isinstance(prompt, str) and prompt.strip():
        return {"prompt": prompt.strip()}
    required = ("product", "scene", "style")
    missing = [name for name in required if not isinstance(request.get(name), str) or not request[name].strip()]
    if missing:
        raise ValueError("请填写产品描述、场景和风格，或直接输入完整英文 Prompt")
    result = {name: str(request[name]).strip() for name in required}
    result["type"] = media_type
    return result


def preview_prompt(
    mode: str,
    request: Mapping[str, Any],
    *,
    provider: str | None = None,
) -> str:
    if mode == "model_outfit_swap":
        # Outfit swap is deliberately prompt-invariant. The ordered reference
        # images and selected provider are the only workflow inputs.
        return outfit_prompt_for_provider(provider)
    if mode == "clothing_image_to_image":
        return build_clothing_image_prompt(request)
    if mode == "tiktok_clothing_image":
        return build_tiktok_clothing_prompt(request)
    if mode == "poster":
        return build_poster_prompt(request)
    if mode in {"shapewear_image", "shapewear_video", "tiktok_10s"}:
        body = dict(request)
        if mode == "tiktok_10s":
            body.setdefault("style_id", "tiktok_ugc")
            body.setdefault("style", "TikTok UGC")
        return build_shapewear_prompt(
            body,
            "image" if mode == "shapewear_image" else "video",
            provider=provider,
        )
    media_type = "image" if mode == "image" else "video"
    normalized = _structured_request(request, media_type)
    prompt = normalized["prompt"] if "prompt" in normalized else generate_prompt(normalized)
    return prompt


def _set_job(job: GenerationJob, status: str, phase: str) -> None:
    if status not in JOB_STATES:
        raise ValueError(f"unknown job status: {status}")
    with JOBS_LOCK:
        job.status = status
        job.phase = phase
        job.updated_at = _now()
        _save_jobs()


def _job_output_dir(job: GenerationJob) -> Path:
    if job.payload.mode == "clothing_image_to_image":
        return OUTPUTS_DIR / "clothing_image"
    if job.payload.mode == "tiktok_clothing_image":
        return OUTPUTS_DIR / "tiktok_clothing"
    if job.payload.mode in {"shapewear_image", "shapewear_video", "tiktok_10s"}:
        return OUTPUTS_DIR / "shapewear"
    if job.payload.mode == "poster":
        return OUTPUTS_DIR / "posters"
    return OUTPUTS_DIR / ("images" if job.payload.mode in {"image", "model_outfit_swap"} else "videos")


def _generate_shapewear_image_with_fallback(
    job: GenerationJob,
    request: dict[str, Any],
    *,
    image: str | None,
    references: list[str] | None,
    output_dir: Path,
) -> tuple[dict[str, Any], str]:
    """Generate a shapewear image and retry once on a quota-style failure."""
    requested_provider = _provider_for_job(job.payload)
    candidates = [requested_provider]
    fallback = _IMAGE_PROVIDER_FALLBACKS.get(requested_provider)
    if fallback:
        candidates.append(fallback)
    first_error: Exception | None = None
    for index, provider in enumerate(candidates):
        if index:
            available, _ = _provider_available(provider)
            if not available:
                continue
            _set_job(job, "running", f"{requested_provider} quota exhausted; switching to {provider}")
        try:
            manifest = generate_shapewear_image(
                request,
                client=_image_client_for_provider(provider),
                provider=provider,
                output_dir=output_dir,
                poll_interval=1,
                max_polls=120,
                image=image,
                references=references,
            )
        except Exception as error:
            if index == 0 and _is_provider_quota_error(error):
                first_error = error
                continue
            raise
        manifest = dict(manifest)
        manifest["provider"] = provider
        manifest["provider_requested"] = requested_provider
        if provider != requested_provider:
            manifest["provider_fallback"] = {
                "from": requested_provider,
                "to": provider,
                "reason": "quota_or_capacity",
            }
        return manifest, provider
    if first_error is not None:
        raise first_error
    raise RuntimeError("no configured image provider is available for shapewear product images")


def _generate_tiktok_clothing_image_with_fallback(
    job: GenerationJob,
    request: dict[str, Any],
    *,
    product_images: list[str],
    output_dir: Path,
) -> tuple[dict[str, Any], str]:
    """Generate a TikTok main image, falling back on edit-capability errors."""
    requested_provider = _provider_for_job(job.payload)
    candidates = [requested_provider]
    fallback = _IMAGE_PROVIDER_FALLBACKS.get(requested_provider)
    if fallback:
        candidates.append(fallback)
    first_error: Exception | None = None
    for index, provider in enumerate(candidates):
        if index:
            available, _ = _provider_available(provider)
            if not available:
                continue
            _set_job(job, "running", f"{requested_provider} reference edit unavailable; switching to {provider}")
        try:
            manifest = generate_tiktok_clothing_image(
                request,
                product_images=product_images,
                client=_image_client_for_provider(provider),
                provider=provider,
                output_dir=output_dir,
                poll_interval=1,
                max_polls=120,
            )
        except Exception as error:
            if index == 0 and _is_provider_fallback_error(error):
                first_error = error
                continue
            raise
        manifest = dict(manifest)
        manifest["provider"] = provider
        manifest["provider_requested"] = requested_provider
        if provider != requested_provider:
            manifest["provider_fallback"] = {
                "from": requested_provider,
                "to": provider,
                "reason": "reference_edit_unavailable",
            }
        return manifest, provider
    if first_error is not None:
        raise first_error
    raise RuntimeError("no configured image provider is available for TikTok clothing images")


def _run_job(job_id: str) -> None:
    with JOBS_LOCK:
        job = JOBS[job_id]
    try:
        def on_task_submitted(task_id: str) -> None:
            with JOBS_LOCK:
                job.provider_task_id = task_id
                job.provider_used = _provider_for_job(job.payload)
                job.status = "running"
                job.phase = "Provider task accepted; waiting in queue"
                job.updated_at = _now()
                _save_jobs()

        _set_job(job, "preparing_prompt", "正在优化 Prompt")
        job.prompt = preview_prompt(
            job.payload.mode,
            job.payload.request,
            provider=job.payload.provider,
        )
        _set_job(job, "submitted", "正在提交生成任务")
        # Outfit swap deliberately has no user generation brief. Keep the
        # worker request empty so stale or hand-crafted fields cannot reach the
        # provider; the runtime supplies its fixed prompt contract.
        request = {} if job.payload.mode == "model_outfit_swap" else dict(job.payload.request)
        # Shapewear runtimes own their immutable prompt assembly. Passing the
        # already-expanded preview back as a user prompt would wrap the
        # contract twice and weaken the distinction between brief and policy.
        if job.payload.mode not in {"model_outfit_swap", "clothing_image_to_image", "tiktok_clothing_image", "shapewear_image", "shapewear_video", "tiktok_10s", "poster"}:
            request["prompt"] = job.prompt
        reference = job.payload.reference_image
        image = None
        if reference:
            image = resolve_asset(reference.value) if reference.kind == "asset" else reference.value
        references: list[str] | None = None
        if job.payload.reference_images:
            resolved_references = _resolve_reference_images(job.payload.reference_images)
            image = resolved_references[0]
            references = resolved_references[1:]
        _set_job(job, "running", "正在生成素材")
        output_dir = _job_output_dir(job)
        if job.payload.mode in {"image", "model_outfit_swap", "clothing_image_to_image", "tiktok_clothing_image", "poster"}:
            client = _image_client_for_provider(job.payload.provider)
            if job.payload.mode == "model_outfit_swap":
                manifest = generate_model_outfit_image(
                    request,
                    model_image=image,
                    outfit_images=references,
                    client=client,
                    provider=job.payload.provider,
                    output_dir=output_dir,
                    poll_interval=3,
                    max_polls=240,
                    max_wait_seconds=600,
                    resume_task_id=job.provider_task_id,
                    on_task_submitted=on_task_submitted,
                )
                job.prompt = manifest["prompt"]
                job.outputs = list(manifest["outputs"])
                job.quality = dict(manifest["quality"])
            elif job.payload.mode == "clothing_image_to_image":
                if image is None or references:
                    raise ValueError("clothing image-to-image requires exactly one garment reference image")
                manifest = generate_clothing_image(
                    request,
                    garment_image=image,
                    client=client,
                    provider=job.payload.provider,
                    output_dir=output_dir,
                    poll_interval=1,
                    max_polls=120,
                )
                job.prompt = manifest["prompt"]
                job.outputs = list(manifest["outputs"])
                job.quality = dict(manifest["quality"])
            elif job.payload.mode == "tiktok_clothing_image":
                if image is None:
                    raise ValueError("TikTok clothing images require one product master reference image")
                product_images = [image, *(references or [])]
                manifest, job.provider_used = _generate_tiktok_clothing_image_with_fallback(
                    job,
                    request,
                    product_images=product_images,
                    output_dir=output_dir,
                )
                job.prompt = manifest["prompt"]
                job.outputs = list(manifest["outputs"])
                job.quality = dict(manifest["quality"])
                job.provider_fallback = manifest.get("provider_fallback")
                job.metadata = {
                    key: manifest[key]
                    for key in (
                        "market",
                        "locale",
                        "presentation_mode",
                        "copy_version",
                        "source_count",
                        "source_roles",
                        "source_hashes",
                        "output_hash",
                        "manual_review_reasons",
                    )
                    if key in manifest
                }
            elif job.payload.mode == "poster":
                if references:
                    raise ValueError("poster accepts at most one reference image")
                manifest = generate_poster_image(
                    request,
                    image=image,
                    client=client,
                    provider=job.payload.provider,
                    output_dir=output_dir,
                    poll_interval=1,
                    max_polls=120,
                )
                job.prompt = manifest["prompt"]
                job.outputs = list(manifest["outputs"])
                job.quality = dict(manifest["quality"])
            else:
                output = generate_image(
                    request,
                    client=client,
                    provider=job.payload.provider,
                    output_dir=output_dir,
                    poll_interval=1,
                    max_polls=120,
                    image=image,
                    references=references,
                )
                job.outputs = [output]
                job.quality = _basic_quality(output, "image")
        elif job.payload.mode == "video":
            output = generate_video(
                request,
                image=image,
                provider=job.payload.provider,
                client=_video_client_for_provider(job.payload.provider),
                output_dir=output_dir,
                poll_interval=2,
                max_polls=120,
            )
            job.outputs = [output]
            job.quality = _basic_quality(output, "video")
        else:
            if job.payload.mode == "tiktok_10s":
                request.setdefault("style_id", "tiktok_ugc")
                request.setdefault("style", "TikTok UGC")
                request.setdefault("target_market", "United States TikTok")
            _set_job(job, "running", "正在执行塑身衣工作流")
            if job.payload.mode == "shapewear_image":
                manifest, job.provider_used = _generate_shapewear_image_with_fallback(
                    job,
                    request,
                    image=image,
                    references=references,
                    output_dir=output_dir,
                )
                job.provider_fallback = manifest.get("provider_fallback")
            else:
                request["provider"] = job.payload.provider
                manifest = generate_shapewear_video(
                    request,
                    image=image,
                    output_dir=output_dir,
                    poll_interval=2,
                    max_polls=120,
                )
            job.prompt = manifest["prompt"]
            job.outputs = list(manifest["outputs"])
            job.quality = dict(manifest["quality"])
        _set_job(job, "quality_check", "正在完成技术检查")
        _set_job(job, "succeeded", "生成完成")
    except Exception as error:  # Provider implementations normalize the detail we can safely expose.
        if isinstance(error, (HermesRequestError, VideoProviderRequestError)):
            PROVIDER_LOGGER.error(
                "generation failed job=%s provider=%s error_type=%s message=%s",
                job.id,
                job.payload.provider,
                type(error).__name__,
                str(error),
            )
        code, message, retryable = _safe_message(error)
        with JOBS_LOCK:
            job.status = "failed"
            job.phase = "生成失败"
            job.error = {"code": code, "message": message, "retryable": retryable}
            job.updated_at = _now()
            _save_jobs()

def _basic_quality(path: str, media_type: str) -> dict[str, Any]:
    artifact = Path(path)
    allowed = {"image": {".png", ".jpg", ".jpeg", ".webp"}, "video": {".mp4", ".webm", ".mov", ".m4v"}}
    exists = artifact.is_file()
    non_empty = exists and artifact.stat().st_size > 0
    extension_ok = artifact.suffix.lower() in allowed[media_type]
    return {
        "path": asset_url(path),
        "media_type": media_type,
        "exists": exists,
        "non_empty": non_empty,
        "extension_ok": extension_ok,
        "passed": exists and non_empty and extension_ok,
        "manual_review": [],
    }


def _relative_output(path: str | Path) -> str:
    candidate = Path(path).resolve()
    for root in OUTPUT_ROOTS:
        try:
            return candidate.relative_to(root.resolve()).as_posix()
        except ValueError:
            continue
    raise ValueError("asset must be located inside outputs")


def asset_id(path: str | Path) -> str:
    candidate = Path(path).resolve()
    for root in OUTPUT_ROOTS:
        try:
            return f"{root.name}/{candidate.relative_to(root.resolve()).as_posix()}"
        except ValueError:
            continue
    raise ValueError("asset must be located inside outputs")


def asset_url(path: str | Path) -> str:
    return "/api/assets/" + asset_id(path)


def resolve_asset(identifier: str) -> str:
    clean = identifier.replace("\\", "/").lstrip("/")
    if not clean or ".." in Path(clean).parts:
        raise ValueError("资源地址无效")
    root_name, separator, relative = clean.partition("/")
    if not separator or root_name not in {root.name for root in OUTPUT_ROOTS}:
        raise ValueError("资源地址不在允许输出目录中")
    root = next(root for root in OUTPUT_ROOTS if root.name == root_name)
    path = (root / relative).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as error:
        raise ValueError("资源地址无效") from error
    if not path.is_file():
        raise ValueError("资源文件不存在")
    return str(path)


def _save_jobs() -> None:
    records = []
    for job in sorted(JOBS.values(), key=lambda item: item.created_at, reverse=True)[:100]:
        records.append(
            {
                "id": job.id,
                "status": job.status,
                "phase": job.phase,
                "mode": job.payload.mode,
                "provider": job.payload.provider,
                "provider_used": job.provider_used,
                "provider_fallback": job.provider_fallback,
                "provider_task_id": job.provider_task_id,
                # Only outfit-swap jobs need their inputs to resume an
                # upstream task. Keep the durable job record small for all
                # other workflows.
                "request": job.payload.request if job.payload.mode == "model_outfit_swap" else {},
                "reference_images": [item.model_dump() for item in job.payload.reference_images] if job.payload.mode == "model_outfit_swap" else [],
                "prompt": job.prompt,
                "outputs": [asset_id(path) for path in job.outputs if Path(path).is_file()],
                "quality": job.quality,
                "metadata": job.metadata,
                "error": job.error,
                "created_at": job.created_at,
                "updated_at": job.updated_at,
            }
        )
    try:
        OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
        JOB_STORE_PATH.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def _load_jobs() -> None:
    try:
        records = json.loads(JOB_STORE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(records, list):
        return
    for record in records:
        if not isinstance(record, Mapping) or record.get("status") not in {"succeeded", "failed", "submitted", "running"}:
            continue
        try:
            references = record.get("reference_images") if isinstance(record.get("reference_images"), list) else []
            payload = GenerationRequest(mode=record["mode"], request=record.get("request") if isinstance(record.get("request"), Mapping) else {}, provider=record.get("provider"), reference_images=references)
            outputs = [resolve_asset(item) for item in record.get("outputs", []) if isinstance(item, str)]
            job = GenerationJob(
                id=str(record["id"]),
                payload=payload,
                status=str(record["status"]),
                phase=str(record.get("phase", "已恢复的历史任务")),
                prompt=record.get("prompt") if isinstance(record.get("prompt"), str) else None,
                outputs=outputs,
                quality=record.get("quality") if isinstance(record.get("quality"), Mapping) else None,
                metadata=record.get("metadata") if isinstance(record.get("metadata"), Mapping) else None,
                error=record.get("error") if isinstance(record.get("error"), Mapping) else None,
                provider_used=record.get("provider_used") if isinstance(record.get("provider_used"), str) else None,
                provider_fallback=(
                    {str(key): str(value) for key, value in record["provider_fallback"].items()}
                    if isinstance(record.get("provider_fallback"), Mapping)
                    else None
                ),
                provider_task_id=record.get("provider_task_id") if isinstance(record.get("provider_task_id"), str) else None,
                created_at=str(record.get("created_at", _now())),
                updated_at=str(record.get("updated_at", _now())),
            )
            JOBS[job.id] = job
            if job.status in {"submitted", "running"} and job.provider_task_id:
                JOB_EXECUTOR.submit(_run_job, job.id)
            elif job.status in {"submitted", "running"}:
                # This record predates task-id persistence or the provider
                # never acknowledged submission. It cannot be retried safely.
                job.status = "failed"
                job.phase = "任务中断"
                job.error = {
                    "code": "interrupted_job",
                    "message": "任务在 Provider 返回任务编号前中断，请重新提交",
                    "retryable": True,
                }
                job.updated_at = _now()
        except (KeyError, ValueError):
            continue


def _save_r2_uploads() -> None:
    with R2_UPLOADS_LOCK:
        records = dict(R2_UPLOADS)
    try:
        OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
        R2_UPLOAD_STORE_PATH.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def _load_r2_uploads() -> None:
    try:
        records = json.loads(R2_UPLOAD_STORE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(records, Mapping):
        return
    with R2_UPLOADS_LOCK:
        for identifier, record in records.items():
            if not isinstance(identifier, str) or not isinstance(record, Mapping):
                continue
            url = record.get("url")
            object_key = record.get("object_key")
            source_size = record.get("source_size")
            source_modified_at = record.get("source_modified_at")
            if (
                isinstance(url, str)
                and isinstance(object_key, str)
                and isinstance(source_size, int)
                and isinstance(source_modified_at, int)
            ):
                R2_UPLOADS[identifier] = {
                    "url": url,
                    "object_key": object_key,
                    "source_size": source_size,
                    "source_modified_at": source_modified_at,
                    "uploaded_at": record.get("uploaded_at"),
                }


def _r2_status() -> dict[str, Any]:
    try:
        config = R2Config.from_env()
    except R2ConfigurationError:
        return {"available": False, "message": "Cloudflare R2 尚未完成本地配置"}
    return {"available": True, "public_base_url": config.public_base_url}


def _r2_upload_record(path: Path) -> dict[str, Any] | None:
    identifier = asset_id(path)
    try:
        metadata = path.stat()
    except OSError:
        return None
    with R2_UPLOADS_LOCK:
        record = R2_UPLOADS.get(identifier)
        if record is None:
            return None
        if record["source_size"] != metadata.st_size or record["source_modified_at"] != metadata.st_mtime_ns:
            return None
        return dict(record)


def _publish_asset_to_r2(path: Path, request: R2UploadRequest) -> dict[str, Any]:
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        url = upload_r2_image(path, request.object_key, overwrite=request.overwrite)
    elif suffix in {".mp4", ".mov", ".webm"}:
        url = upload_r2_video(path, request.object_key, overwrite=request.overwrite)
    else:
        raise R2ValidationError("该输出格式暂不支持上传到 Cloudflare R2")

    config = R2Config.from_env()
    parsed_url = urlparse(url)
    public_origin = urlparse(config.public_base_url)
    if (parsed_url.scheme, parsed_url.netloc) != (public_origin.scheme, public_origin.netloc):
        raise R2UploadError("R2 returned an unexpected public URL")
    object_key = unquote(parsed_url.path.lstrip("/"))
    if not object_key:
        raise R2UploadError("R2 returned an invalid public URL")

    metadata = path.stat()
    record = {
        "url": url,
        "object_key": object_key,
        "source_size": metadata.st_size,
        "source_modified_at": metadata.st_mtime_ns,
        "uploaded_at": _now(),
    }
    with R2_UPLOADS_LOCK:
        R2_UPLOADS[asset_id(path)] = record
    _save_r2_uploads()
    return record


def _is_private_network_target(host: str) -> bool:
    normalized = host.rstrip(".").lower()
    if normalized in {"localhost", "metadata.google.internal", "metadata", "instance-data"} or normalized.endswith((".localhost", ".local", ".internal")):
        return True
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        return False
    return any((address.is_private, address.is_loopback, address.is_link_local, address.is_reserved, address.is_unspecified, address.is_multicast))


def _validate_outbound_api_url(value: str) -> str:
    """Validate configured endpoints before any provider client uses them."""
    parsed = urlparse(parse_api_url(value))
    host = parsed.hostname
    if parsed.scheme.lower() not in {"http", "https"} or not host or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("API 地址必须是无凭据、无查询参数的 HTTP 或 HTTPS URL")
    if _is_private_network_target(host):
        raise ValueError("API 地址不能指向本地或私有网络")
    # DNS is intentionally checked at save/test time.  A DNS failure is left
    # for the connection test, but a mixed/private answer is always rejected.
    try:
        addresses = {entry[4][0] for entry in socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)}
    except socket.gaierror:
        addresses = set()
    if any(_is_private_network_target(address) for address in addresses):
        raise ValueError("API 地址不能解析到本地或私有网络")
    return parsed.geturl().rstrip("/")


def _hermes_settings(*, include_key: bool = False) -> dict[str, Any]:
    """Read the legacy Hermes record for compatibility migration only."""
    env_api_key = env_value("HERMES_API_KEY", "") or ""
    defaults = {
        "api_url": env_value("HERMES_API_URL", "https://aiapi.yicheng.bj.cn/v1") or "https://aiapi.yicheng.bj.cn/v1",
        "api_key": env_api_key,
        "model": env_value("HERMES_MODEL", "gpt-image-2") or "gpt-image-2",
        "submit_path": env_value("HERMES_SUBMIT_PATH", "/images/generations") or "/images/generations",
        "edit_path": env_value("HERMES_EDIT_PATH", "/images/edits") or "/images/edits",
        "status_path": env_value("HERMES_STATUS_PATH", "/images/generations/{task_id}") or "/images/generations/{task_id}",
        "result_path": env_value("HERMES_RESULT_PATH", "/images/generations/{task_id}") or "/images/generations/{task_id}",
        "timeout": env_value("HERMES_TIMEOUT", "480") or "480",
        "query_timeout": env_value("HERMES_QUERY_TIMEOUT", "30") or "30",
        "result_timeout": env_value("HERMES_RESULT_TIMEOUT", "120") or "120",
        "download_timeout": env_value("HERMES_DOWNLOAD_TIMEOUT", "120") or "120",
    }
    stored_key_loaded = False
    try:
        stored = json.loads(HERMES_SETTINGS_PATH.read_text(encoding="utf-8"))
        if isinstance(stored, Mapping):
            defaults.update({key: value for key, value in stored.items() if key in defaults and isinstance(value, str)})
            stored_key_loaded = "api_key" in stored
    except (OSError, json.JSONDecodeError):
        pass
    # API keys are encrypted at rest. Older plaintext files are ignored rather
    # than echoed back; users can re-enter the key in the settings dialog.
    try:
        encrypted_key = defaults.get("api_key", "")
        if encrypted_key.startswith(("dpapi:", "fernet:")):
            defaults["api_key"] = _unprotect_hermes_key(encrypted_key)
        elif encrypted_key and stored_key_loaded:
            defaults["api_key"] = ""
    except (OSError, InvalidToken, ValueError):
        defaults["api_key"] = ""
    if include_key:
        return defaults
    return {key: value for key, value in defaults.items() if key != "api_key"} | {"api_key_configured": bool(defaults["api_key"])}


def _write_hermes_settings(request: HermesSettingsRequest) -> dict[str, Any]:
    record = _write_module_settings("image.hermes", ModuleSettingsRequest(
        api_url=request.api_url,
        api_key=request.api_key,
        clear_api_key=request.clear_api_key,
        model=request.model,
        enabled=True,
        options={"submit_path": request.submit_path, "edit_path": request.edit_path, "status_path": request.status_path, "result_path": request.result_path, "timeout": str(request.timeout)},
    ))
    return _legacy_hermes_response(record)


def _legacy_hermes_response(record: Mapping[str, Any]) -> dict[str, Any]:
    options = record.get("options") if isinstance(record.get("options"), Mapping) else {}
    return {
        "api_url": record.get("api_url", ""),
        "model": record.get("model", "gpt-image-2"),
        "submit_path": options.get("submit_path", "/images/generations"),
        "edit_path": options.get("edit_path", "/images/edits"),
        "status_path": options.get("status_path", "/images/generations/{task_id}"),
        "result_path": options.get("result_path", "/images/generations/{task_id}"),
        "timeout": float(options.get("timeout", "480")),
        "query_timeout": float(options.get("query_timeout", "30")),
        "result_timeout": float(options.get("result_timeout", "120")),
        "download_timeout": float(options.get("download_timeout", "120")),
        "api_key_configured": bool(record.get("api_key_configured")),
    }


def _module_env_defaults(module_id: str) -> dict[str, Any]:
    spec = MODULE_SPECS[module_id]
    prefix = spec["env_prefix"]
    options = {key: env_value(f"{prefix}_{key.upper()}", default) or default for key, default in spec["options"].items()}
    protocol = _protocol_for_module(module_id)
    if protocol in {"image.hermes", "image.liblib"}:
        options.setdefault("timeout", env_value(f"{prefix}_TIMEOUT", "480") or "480")
        options.setdefault("query_timeout", env_value(f"{prefix}_QUERY_TIMEOUT", "30") or "30")
        options.setdefault("result_timeout", env_value(f"{prefix}_RESULT_TIMEOUT", "120") or "120")
        options.setdefault("download_timeout", env_value(f"{prefix}_DOWNLOAD_TIMEOUT", "120") or "120")
    if protocol == "video.seedance":
        options.setdefault("timeout", env_value(f"{prefix}_TIMEOUT", "180") or "180")
    return {
        "id": module_id,
        "name": spec["name"],
        "category": spec["category"],
        "api_url": env_value(f"{prefix}_API_URL", spec["api_url"]) or spec["api_url"],
        "api_key": env_value(f"{prefix}_API_KEY", "") or "",
        "model": env_value(f"{prefix}_MODEL", spec["model"]) or spec["model"],
        "enabled": True,
        "capabilities": list(spec["capabilities"]),
        "options": options,
        "kind": spec.get("kind", "builtin"),
        "protocol": protocol,
        "builtin": module_id in BUILTIN_MODULE_IDS,
    }


def _stored_module_records() -> dict[str, dict[str, Any]]:
    try:
        payload = json.loads(MODULE_SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, Mapping):
        return {}
    records = payload.get("modules", payload)
    if not isinstance(records, Mapping):
        return {}
    hydrated: dict[str, dict[str, Any]] = {}
    for module_id, record in records.items():
        if not isinstance(module_id, str) or not isinstance(record, Mapping):
            continue
        if module_id not in MODULE_SPECS:
            _hydrate_custom_module(module_id, record)
        if module_id in MODULE_SPECS:
            hydrated[module_id] = dict(record)
    return hydrated


def _module_settings(module_id: str, *, include_key: bool = False) -> dict[str, Any]:
    stored_records = _stored_module_records()
    if module_id not in MODULE_SPECS:
        stored = stored_records.get(module_id)
        if stored is not None:
            _hydrate_custom_module(module_id, stored)
        if module_id not in MODULE_SPECS:
            raise KeyError(module_id)
    defaults = _module_env_defaults(module_id)
    stored = stored_records.get(module_id)
    if stored is None and module_id == "image.hermes":
        legacy = _hermes_settings(include_key=True)
        defaults.update({key: legacy[key] for key in ("api_url", "api_key", "model")})
        defaults["options"] = {key: legacy[key] for key in MODULE_OPTION_KEYS[module_id]}
    elif stored is not None:
        for key in ("api_url", "model", "enabled"):
            if key in stored and isinstance(stored[key], type(defaults[key])):
                defaults[key] = stored[key]
        stored_name = stored.get("name")
        if isinstance(stored_name, str) and stored_name.strip():
            defaults["name"] = stored_name.strip()
        if isinstance(stored.get("options"), Mapping):
            defaults["options"].update({key: value for key, value in stored["options"].items() if key in MODULE_OPTION_KEYS[module_id] and isinstance(value, str)})
        encrypted_key = stored.get("api_key", "")
        if isinstance(encrypted_key, str) and encrypted_key.startswith(("dpapi:", "fernet:")):
            try:
                defaults["api_key"] = _unprotect_hermes_key(encrypted_key)
            except (OSError, InvalidToken, ValueError):
                defaults["api_key"] = ""
        elif "api_key" in stored:
            defaults["api_key"] = ""
    try:
        defaults["api_url"] = parse_api_url(str(defaults.get("api_url") or ""))
    except ValueError:
        pass
    if defaults.get("api_key"):
        defaults["api_key"] = parse_api_key(str(defaults["api_key"]))
    if include_key:
        return defaults
    return {key: value for key, value in defaults.items() if key != "api_key"} | {"api_key_configured": bool(defaults["api_key"])}


def _apply_module_environment(record: Mapping[str, Any]) -> None:
    """New jobs use saved settings immediately; no credentials leave process memory."""
    prefix = MODULE_SPECS[record["id"]]["env_prefix"]
    for name, value in (("API_URL", record.get("api_url", "")), ("API_KEY", record.get("api_key", "")), ("MODEL", record.get("model", ""))):
        environment_name = f"{prefix}_{name}"
        if value:
            os.environ[environment_name] = str(value)
        else:
            os.environ.pop(environment_name, None)
    options = record.get("options") if isinstance(record.get("options"), Mapping) else {}
    for key, value in options.items():
        environment_name = f"{prefix}_{key.upper()}"
        if value:
            os.environ[environment_name] = str(value)
        else:
            os.environ.pop(environment_name, None)
    if _protocol_for_module(record["id"]) == "video.seedance":
        for suffix in ("TEXT_MODEL", "IMAGE_MODEL"):
            environment_name = f"{prefix}_{suffix}"
            if record.get("model"):
                os.environ[environment_name] = str(record["model"])
            else:
                os.environ.pop(environment_name, None)


def _restore_module_environments() -> None:
    """Make persisted module records authoritative after a service restart."""
    for module_id in _stored_module_records():
        _apply_module_environment(_module_settings(module_id, include_key=True))


def _validate_module_options(module_id: str, options: Mapping[str, str], protocol: str | None = None) -> None:
    resolved = protocol or _protocol_for_module(module_id)
    for key, value in options.items():
        if key == "timeout":
            if resolved not in {"image.hermes", "image.liblib", "video.seedance"}:
                raise ValueError("timeout is not supported by this module")
            try:
                timeout = float(value)
            except (TypeError, ValueError) as error:
                raise ValueError("timeout must be a number") from error
            if not 1 <= timeout <= 600:
                raise ValueError("timeout must be between 1 and 600 seconds")
            continue
        if key in {"query_timeout", "result_timeout", "download_timeout"}:
            if resolved not in {"image.hermes", "image.liblib"}:
                raise ValueError("timeout is only supported by the Hermes image module")
            try:
                timeout = float(value)
            except (TypeError, ValueError) as error:
                raise ValueError("Hermes timeout must be a number") from error
            if not 1 <= timeout <= 600:
                raise ValueError("Hermes timeout must be between 1 and 600 seconds")
            continue
        if not value.startswith("/") or "://" in value:
            raise ValueError("路径选项必须是相对路径")


def _write_module_settings(module_id: str, request: ModuleSettingsRequest) -> dict[str, Any]:
    if module_id not in MODULE_SPECS:
        raise KeyError(module_id)
    api_url = _validate_outbound_api_url(request.api_url)
    current = _module_settings(module_id, include_key=True)
    spec = MODULE_SPECS[module_id]
    protocol = _runtime_protocol(module_id, api_url, request.model or current.get("model") or "")
    if spec.get("kind") == "custom" and protocol != spec.get("protocol"):
        _hydrate_custom_module(module_id, {"protocol": protocol, "name": spec.get("name")})
        MODULE_SPECS[module_id]["name"] = spec["name"]
        spec = MODULE_SPECS[module_id]
    allowed_options = MODULE_OPTION_KEYS[module_id]
    filtered_options = {key: value for key, value in request.options.items() if key in allowed_options}
    _validate_module_options(module_id, filtered_options, protocol)
    api_key = "" if request.clear_api_key else (parse_api_key(request.api_key) or current["api_key"])
    record = _module_env_defaults(module_id) | {
        "id": module_id,
        "name": spec["name"],
        "api_url": api_url,
        "api_key": _protect_hermes_key(api_key),
        "model": request.model or _module_env_defaults(module_id)["model"],
        "enabled": request.enabled,
        "options": _module_env_defaults(module_id)["options"] | filtered_options,
        "kind": spec.get("kind", "builtin"),
        "protocol": protocol,
    }
    with MODULE_SETTINGS_LOCK:
        records = _stored_module_records()
        records[module_id] = record
        _atomic_json_write(MODULE_SETTINGS_PATH, {"modules": records})
    current_record = _module_settings(module_id, include_key=True)
    _apply_module_environment(current_record)
    return _module_settings(module_id)


def _validate_module_draft(module_id: str, request: ModuleSettingsRequest) -> None:
    if module_id not in MODULE_SPECS:
        raise KeyError(module_id)
    _validate_outbound_api_url(request.api_url)
    spec = MODULE_SPECS[module_id]
    protocol = _runtime_protocol(module_id, request.api_url, request.model)
    allowed_options = set(MODULE_OPTION_KEYS.get(protocol, ()))
    if spec.get("kind") == "custom":
        allowed_options |= set(MODULE_OPTION_KEYS.get(module_id, ()))
    if any(key not in allowed_options for key in request.options):
        raise ValueError("此模块不支持该配置选项")
    _validate_module_options(
        module_id,
        {key: value for key, value in request.options.items() if key in MODULE_OPTION_KEYS.get(protocol, set())},
        protocol,
    )


def _module_draft_client(module_id: str, request: ModuleSettingsRequest, api_key: str) -> Any:
    protocol = _runtime_protocol(module_id, request.api_url, request.model)
    kwargs = _module_client_kwargs(protocol, request.options)
    if protocol == "image.hermes":
        return HermesClient(
            api_url=request.api_url,
            api_key=api_key,
            model=request.model,
            request_logger=PROVIDER_LOGGER,
            **kwargs,
        )
    if protocol == "image.liblib":
        if _uses_ark_image_gateway(request.api_url, request.model):
            return HermesClient(
                api_url=request.api_url,
                api_key=api_key,
                model=request.model,
                request_logger=PROVIDER_LOGGER,
                **kwargs,
            )
        liblib_timeout = kwargs.get("timeout")
        return LiblibClient(
            api_url=request.api_url,
            api_key=api_key,
            model=request.model,
            request_logger=PROVIDER_LOGGER,
            **({"timeout": liblib_timeout} if liblib_timeout is not None else {}),
        )
    if protocol == "video.veo":
        return GoogleVeoClient(api_url=request.api_url, api_key=api_key, model=request.model)
    if protocol == "video.seedance":
        seedance_kwargs = {
            key: value
            for key, value in kwargs.items()
            if key in {"submit_path", "status_path", "result_path", "timeout"}
        }
        return SeedanceClient(
            api_url=request.api_url,
            api_key=api_key,
            text_model=request.model,
            image_model=request.model,
            request_logger=PROVIDER_LOGGER,
            **seedance_kwargs,
        )
    if protocol == "mix.codex_terra":
        return CodexTerraPlanner(api_url=request.api_url, api_key=api_key, model=request.model)
    raise KeyError(module_id)


def _normalize_provider_models(payload: Any) -> list[dict[str, str]]:
    return _normalize_gateway_models(payload)


def _hermes_client_for_module(module_id: str) -> HermesClient:
    record = _module_settings(module_id, include_key=True)
    protocol = _protocol_for_module(module_id)
    return HermesClient(
        api_url=record["api_url"],
        api_key=record["api_key"],
        model=record["model"],
        request_logger=PROVIDER_LOGGER,
        **_module_client_kwargs(protocol, record["options"]),
    )


def _hermes_client() -> HermesClient:
    return _hermes_client_for_module("image.hermes")


def _image_client_for_provider(provider: str | None) -> Any | None:
    normalized = (provider or "hermes").strip().lower()
    if normalized == "hermes":
        return _hermes_client()
    if normalized in {"hermes_volcano", "liblib"}:
        return _hermes_client_for_module("image.liblib")
    module_id = _module_id_for_provider(normalized)
    if not module_id:
        return None
    spec = MODULE_SPECS.get(module_id)
    if not spec or spec.get("category") != "image":
        return None
    protocol = _protocol_for_module(module_id)
    if protocol in {"image.hermes", "image.liblib"}:
        record = _module_settings(module_id, include_key=True)
        return _module_draft_client(module_id, ModuleSettingsRequest(api_url=record["api_url"], model=record["model"], options=record["options"]), record["api_key"])
    return None


def _video_client_for_provider(provider: str | None) -> Any | None:
    normalized = (provider or "veo").strip().lower()
    module_id = _module_id_for_provider(normalized)
    if not module_id:
        return None
    spec = MODULE_SPECS.get(module_id)
    if not spec or spec.get("category") != "video":
        return None
    record = _module_settings(module_id, include_key=True)
    return _module_draft_client(
        module_id,
        ModuleSettingsRequest(api_url=record["api_url"], model=record["model"], options=record["options"]),
        record["api_key"],
    )


def _resolve_custom_module_identity(request: CreateCustomModuleRequest) -> tuple[str, str, str, str]:
    occupied = _occupied_custom_slugs()
    protocol = request.protocol.strip()
    name = request.name.strip()
    slug = request.slug.strip().lower()
    model = request.model.strip()
    if protocol:
        if protocol not in BUILTIN_MODULE_IDS or MODULE_SPECS[protocol].get("kind") == "custom":
            raise ValueError("协议模板必须是内置模块")
        if MODULE_SPECS[protocol]["category"] != request.category:
            raise ValueError("协议模板与能力分类不匹配")
        if not slug:
            slug = suggested_slug(request.api_url, request.category, occupied, protocol)
        if not name:
            name = suggested_name(protocol, request.api_url)
        return protocol, name, slug, model
    api_key = parse_api_key(request.api_key)
    if api_key:
        detection = detect_gateway(
            api_url=request.api_url,
            api_key=api_key,
            category=request.category,
            model=model,
            occupied_slugs=occupied,
        )
        protocol = detection["protocol"]
        name = name or detection["suggested_name"]
        slug = slug or detection["suggested_slug"]
        model = model or detection["selected_model"]
    else:
        protocol = match_protocol(request.api_url, model=model, category=request.category)
        name = name or suggested_name(protocol, request.api_url)
        slug = slug or suggested_slug(request.api_url, request.category, occupied, protocol)
    if MODULE_SPECS[protocol]["category"] != request.category:
        raise ValueError("识别到的网关与当前能力分类不匹配")
    return protocol, name, slug, model

def _create_custom_module(request: CreateCustomModuleRequest) -> dict[str, Any]:
    protocol, name, slug, model = _resolve_custom_module_identity(request)
    module_id = _custom_module_id(request.category, slug)
    with MODULE_SETTINGS_LOCK:
        stored = _stored_module_records()
        custom_ids = {item_id for item_id, spec in MODULE_SPECS.items() if spec.get("kind") == "custom"}
        custom_ids.update(item_id for item_id, item in stored.items() if isinstance(item, Mapping) and item.get("kind") == "custom")
        if len(custom_ids) >= MAX_CUSTOM_MODULES:
            raise ValueError("自定义模块数量已达上限")
        if module_id in MODULE_SPECS or module_id in stored:
            raise ValueError("该模块 ID 已存在")
        _hydrate_custom_module(module_id, {"protocol": protocol, "name": name})
        MODULE_SPECS[module_id]["name"] = name
    settings = ModuleSettingsRequest(
        api_url=request.api_url,
        api_key=request.api_key,
        model=model,
        enabled=request.enabled,
        options=request.options,
    )
    try:
        return _write_module_settings(module_id, settings)
    except Exception:
        with MODULE_SETTINGS_LOCK:
            records = _stored_module_records()
            records.pop(module_id, None)
            try:
                _atomic_json_write(MODULE_SETTINGS_PATH, {"modules": records})
            except OSError:
                pass
        _forget_custom_module(module_id)
        raise


def _delete_custom_module(module_id: str) -> None:
    if module_id in BUILTIN_MODULE_IDS:
        raise ValueError("不能删除内置模块")
    if module_id not in MODULE_SPECS and module_id not in _stored_module_records():
        raise KeyError(module_id)
    with MODULE_SETTINGS_LOCK:
        spec = MODULE_SPECS.get(module_id)
        prefix = spec.get("env_prefix") if spec else None
        option_keys = set(MODULE_OPTION_KEYS.get(module_id, ()))
        records = _stored_module_records()
        records.pop(module_id, None)
        _atomic_json_write(MODULE_SETTINGS_PATH, {"modules": records})
        if prefix:
            for suffix in ("API_URL", "API_KEY", "MODEL", "TEXT_MODEL", "IMAGE_MODEL"):
                os.environ.pop(f"{prefix}_{suffix}", None)
            for key in option_keys:
                os.environ.pop(f"{prefix}_{key.upper()}", None)
        _forget_custom_module(module_id)
    _restore_module_environments()


def _atomic_json_write(path: Path, payload: Any) -> None:
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _save_batches() -> None:
    with BATCHES_LOCK:
        records = copy.deepcopy(list(BATCHES.values())[-100:])
    try:
        _atomic_json_write(BATCH_STORE_PATH, records)
    except OSError:
        pass


def _load_batches() -> None:
    try:
        records = json.loads(BATCH_STORE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(records, list):
        return
    with BATCHES_LOCK:
        for record in records:
            if not isinstance(record, Mapping) or not isinstance(record.get("id"), str) or not isinstance(record.get("items"), list):
                continue
            restored = copy.deepcopy(dict(record))
            interrupted = False
            for item in restored["items"]:
                if isinstance(item, dict) and item.get("status") in {"queued", "running"}:
                    item.update({"status": "failed", "error": "服务重启中断了此任务，可重新创建失败项"})
                    interrupted = True
            if interrupted:
                statuses = {item.get("status") for item in restored["items"] if isinstance(item, Mapping)}
                restored["status"] = "partial" if "succeeded" in statuses else "failed"
                restored["updated_at"] = _now()
            BATCHES[restored["id"]] = restored


def _save_mixes() -> None:
    with MIXES_LOCK:
        records = copy.deepcopy(list(MIXES.values())[-100:])
    try:
        _atomic_json_write(MIX_STORE_PATH, records)
    except OSError:
        pass


def _load_mixes() -> None:
    try:
        records = json.loads(MIX_STORE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(records, list):
        return
    with MIXES_LOCK:
        for record in records:
            if not isinstance(record, Mapping) or not isinstance(record.get("id"), str):
                continue
            restored = copy.deepcopy(dict(record))
            if restored.get("status") in {"queued", "running"}:
                restored.update({"status": "failed", "phase": "服务重启中断了混剪任务", "updated_at": _now()})
            MIXES[restored["id"]] = restored


def _run_batch_item(batch_id: str, item_index: int) -> None:
    with BATCHES_LOCK:
        item = copy.deepcopy(BATCHES[batch_id]["items"][item_index])
        BATCHES[batch_id]["items"][item_index]["status"] = "running"
        BATCHES[batch_id]["updated_at"] = _now()
    _save_batches()
    try:
        source = resolve_asset(item["image_asset_id"]) if item.get("image_asset_id") else None
        references = [resolve_asset(identifier) for identifier in item.get("reference_asset_ids", [])]
        output = generate_image(
            {"prompt": item["prompt"]}, client=_hermes_client(), provider="hermes", image=source, references=references,
            aspect_ratio=item.get("aspect_ratio", "square"), output_dir=OUTPUTS_DIR / "images",
            poll_interval=1, max_polls=120,
        )
        update = {"status": "succeeded", "output": asset_url(output)}
    except Exception as error:
        _, message, _ = _safe_message(error)
        update = {"status": "failed", "error": message}
    with BATCHES_LOCK:
        BATCHES[batch_id]["items"][item_index].update(update)
        BATCHES[batch_id]["updated_at"] = _now()
    _save_batches()


def _run_batch(batch_id: str) -> None:
    with BATCHES_LOCK:
        batch = BATCHES[batch_id]
        batch["status"] = "running"
        concurrency = int(batch.get("max_concurrency", 2))
        item_count = len(batch["items"])
    _save_batches()
    with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix=f"batch-{batch_id[:8]}") as executor:
        list(executor.map(lambda index: _run_batch_item(batch_id, index), range(item_count)))
    with BATCHES_LOCK:
        batch = BATCHES[batch_id]
        statuses = {item["status"] for item in batch["items"]}
        batch["status"] = "succeeded" if statuses == {"succeeded"} else "failed" if statuses == {"failed"} else "partial"
        batch["updated_at"] = _now()
    _save_batches()


def _run_ffmpeg(arguments: list[str]) -> None:
    completed = subprocess.run(arguments, capture_output=True, text=True, timeout=600, check=False)
    if completed.returncode != 0:
        raise RuntimeError("FFmpeg 混剪失败，请检查素材编码和文件完整性")


def _local_mix_plan(request: MixPlanRequest) -> MixPlan:
    """Produce a reproducible plan without probing or modifying source media."""
    default_duration = min(60000, max(500, request.target_duration_ms // len(request.clips)))
    planned: list[MixPlanClip] = []
    for clip in request.clips:
        duration = (clip.end_ms - clip.start_ms) if clip.end_ms is not None else (clip.duration_ms or default_duration)
        planned.append(MixPlanClip(
            asset_id=clip.asset_id,
            start_ms=clip.start_ms,
            end_ms=clip.end_ms,
            duration_ms=duration,
            transition="hard_cut",
        ))
    total = sum((clip.end_ms - clip.start_ms) if clip.end_ms is not None else (clip.duration_ms or 0) for clip in planned)
    warnings = []
    if total < request.target_duration_ms:
        warnings.append("目标时长超过单素材时长限制，已缩短为可执行计划")
    if request.transition_mode == "fade":
        warnings.append("当前本地执行器仅支持硬切，已使用硬切过渡")
    return MixPlan(
        objective=request.objective,
        target_duration_ms=total,
        clips=planned,
        planner="local",
        transition_mode=request.transition_mode,
        warnings=warnings,
    )


def _mix_planner_module_id() -> str | None:
    _stored_module_records()
    enabled_custom: list[str] = []
    builtin_id: str | None = None
    for module_id, spec in MODULE_SPECS.items():
        if spec.get("category") != "mix_planner":
            continue
        if _protocol_for_module(module_id) != "mix.codex_terra":
            continue
        record = _module_settings(module_id, include_key=True)
        if not record.get("enabled") or not str(record.get("api_url") or "").strip() or not str(record.get("api_key") or "").strip():
            continue
        if spec.get("kind") == "custom":
            enabled_custom.append(module_id)
        elif module_id == "mix.codex_terra":
            builtin_id = module_id
    return enabled_custom[0] if enabled_custom else builtin_id


def _terra_planner() -> CodexTerraPlanner | None:
    """Read Terra credentials only in the server process, never from a request."""
    module_id = _mix_planner_module_id()
    if not module_id:
        return None
    record = _module_settings(module_id, include_key=True)
    api_url = record["api_url"]
    api_key = record["api_key"]
    if not record["enabled"] or not api_url.strip() or not api_key.strip():
        return None
    try:
        _validate_outbound_api_url(api_url)
    except ValueError:
        return None
    return CodexTerraPlanner(
        api_url=api_url,
        api_key=api_key,
        model=record["model"] or "gpt-5.6-terra",
    )


def _terra_plan_prompt(request: MixPlanRequest) -> str:
    return json.dumps(
        {
            "objective": request.objective,
            "target_duration_ms": request.target_duration_ms,
            "transition_mode": request.transition_mode,
            "clips": [clip.model_dump() for clip in request.clips],
        },
        ensure_ascii=False,
    )


def _validated_terra_plan(value: dict[str, Any], request: MixPlanRequest) -> MixPlan:
    """Limit remote suggestions to selected assets and renderer capabilities."""
    required = {"version", "planner", "objective", "target_duration_ms", "clips", "warnings"}
    if not required.issubset(value) or value.get("version") != "aigc-mix-plan/v1" or value.get("planner") != "codex_terra":
        raise ValueError("invalid Terra plan envelope")
    if not isinstance(value.get("objective"), str) or not isinstance(value.get("target_duration_ms"), int) or not isinstance(value.get("warnings"), list):
        raise ValueError("invalid Terra plan fields")
    raw_clips = value.get("clips")
    if not isinstance(raw_clips, list) or not 2 <= len(raw_clips) <= 50:
        raise ValueError("missing clips")
    allowed = {clip.asset_id for clip in request.clips}
    normalized = []
    warned_about_transition = False
    for raw in raw_clips:
        if not isinstance(raw, Mapping) or raw.get("asset_id") not in allowed:
            raise ValueError("unknown asset")
        candidate = {key: raw[key] for key in ("asset_id", "start_ms", "end_ms", "duration_ms", "transition") if key in raw}
        if candidate.get("transition", "hard_cut") != "hard_cut":
            warned_about_transition = True
        candidate["transition"] = "hard_cut"
        normalized.append(MixPlanClip.model_validate(candidate))
    warnings = []
    if warned_about_transition or request.transition_mode == "fade":
        warnings.append("当前本地执行器仅支持硬切，已使用硬切过渡")
    if not all(isinstance(warning, str) and len(warning) <= 500 for warning in value["warnings"]):
        raise ValueError("invalid Terra warnings")
    total = sum((clip.end_ms - clip.start_ms) if clip.end_ms is not None else (clip.duration_ms or 0) for clip in normalized)
    if not 1000 <= total <= 300000:
        raise ValueError("invalid Terra total duration")
    return MixPlan(
        objective=request.objective,
        target_duration_ms=total,
        clips=normalized,
        planner="codex_terra",
        transition_mode=request.transition_mode,
        warnings=warnings,
    )


def _create_mix_plan(request: MixPlanRequest) -> MixPlan:
    fallback = _local_mix_plan(request)
    planner = _terra_planner()
    if planner is None:
        return fallback
    try:
        return _validated_terra_plan(planner.plan(_terra_plan_prompt(request)), request)
    except (TerraPlannerError, ValueError, TypeError):
        # A model outage or malformed output must never block local mixing.
        return fallback.model_copy(update={"warnings": [*fallback.warnings, "智能规划不可用，已改用本地规则计划"]})


def _planning_request(request: MixRequest) -> MixPlanRequest:
    return MixPlanRequest(
        clips=request.clips,
        objective=request.objective,
        target_duration_ms=request.target_duration_ms or 15000,
        transition_mode=request.transition_mode,
    )


def _run_mix(mix_id: str, request: MixRequest) -> None:
    with MIXES_LOCK:
        mix = MIXES[mix_id]
        mix.update({"status": "running", "phase": "正在标准化素材", "updated_at": _now()})
    _save_mixes()
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        with MIXES_LOCK:
            MIXES[mix_id].update({"status": "failed", "phase": "混剪失败", "error": "未找到 FFmpeg，请先安装并加入 PATH", "updated_at": _now()})
        _save_mixes()
        return
    dimensions = {"portrait": (720, 1280), "landscape": (1280, 720), "square": (1024, 1024)}
    width, height = dimensions[request.aspect_ratio]
    video_suffixes = {".mp4", ".mov", ".webm", ".m4v"}
    output_dir = OUTPUTS_DIR / "videos"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"mix_{mix_id}.mp4"
    try:
        with tempfile.TemporaryDirectory(prefix="aigc-mix-") as temporary:
            temp_root = Path(temporary)
            segments = []
            video_filter = f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30"
            clips = request.plan.clips if request.plan else request.clips
            for index, clip in enumerate(clips):
                source = Path(resolve_asset(clip.asset_id))
                segment = temp_root / f"segment_{index:03d}.mp4"
                if source.suffix.lower() in video_suffixes:
                    command = [ffmpeg, "-nostdin", "-v", "error", "-y"]
                    if clip.start_ms:
                        command += ["-ss", f"{clip.start_ms / 1000:.3f}"]
                    command += ["-i", str(source)]
                    duration_ms = (clip.end_ms - clip.start_ms) if clip.end_ms else clip.duration_ms
                    if duration_ms:
                        command += ["-t", f"{duration_ms / 1000:.3f}"]
                else:
                    duration = (clip.duration_ms or 3000) / 1000
                    command = [ffmpeg, "-nostdin", "-v", "error", "-y", "-loop", "1", "-i", str(source), "-t", f"{duration:.3f}"]
                command += ["-vf", video_filter, "-an", "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(segment)]
                _run_ffmpeg(command)
                segments.append(segment)
            concat_file = temp_root / "concat.txt"
            concat_file.write_text("".join(f"file '{segment.as_posix()}'\n" for segment in segments), encoding="utf-8")
            with MIXES_LOCK:
                MIXES[mix_id].update({"phase": "正在合成视频", "updated_at": _now()})
            _save_mixes()
            _run_ffmpeg([ffmpeg, "-nostdin", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file), "-c", "copy", str(output_path)])
        with MIXES_LOCK:
            MIXES[mix_id].update({"status": "succeeded", "phase": "混剪完成", "output": asset_url(output_path), "updated_at": _now()})
    except Exception:
        with MIXES_LOCK:
            # FFmpeg exceptions can contain local source paths and command-line
            # fragments. Keep task output stable and free of local diagnostics.
            MIXES[mix_id].update({"status": "failed", "phase": "混剪失败", "error": "本地混剪失败，请检查素材编码和文件完整性", "updated_at": _now()})
    _save_mixes()


def _provider_available(name: str) -> tuple[bool, str | None]:
    module_id = _module_id_for_provider(name)
    if not module_id:
        raise KeyError(name)
    record = _module_settings(module_id, include_key=True)
    available = bool(record.get("enabled") and record.get("api_key"))
    return available, None if available else "未完成本地 API Key 配置"


def _public_provider_record(module_id: str) -> dict[str, Any]:
    spec = MODULE_SPECS[module_id]
    protocol = _protocol_for_module(module_id)
    available, reason = _provider_available(_public_provider_id(module_id))
    if spec["category"] == "image":
        media_types = ["image"]
        input_modes = ["text", "image", "references"]
    elif spec["category"] == "video":
        media_types = ["video"]
        input_modes = ["text", "public_https_image"] if protocol == "video.seedance" else ["text", "image"]
    else:
        media_types = []
        input_modes = ["text"]
    return {
        "id": _public_provider_id(module_id),
        "module_id": module_id,
        "name": spec["name"],
        "kind": spec.get("kind", "builtin"),
        "protocol": protocol,
        "media_types": media_types,
        "input_modes": input_modes,
        "available": available,
        "reason": reason,
    }


def _media_signature_matches(name: str, header: bytes) -> bool:
    suffix = Path(name).suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return header.startswith(b"\xff\xd8\xff")
    if suffix == ".png":
        return header.startswith(b"\x89PNG\r\n\x1a\n")
    if suffix == ".gif":
        return header.startswith((b"GIF87a", b"GIF89a"))
    if suffix == ".webp":
        return len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP"
    if suffix in {".mp4", ".mov"}:
        return len(header) >= 12 and header[4:8] == b"ftyp"
    if suffix == ".webm":
        return header.startswith(b"\x1aE\xdf\xa3")
    return False


def _assets() -> list[dict[str, Any]]:
    assets: list[dict[str, Any]] = []
    for root in OUTPUT_ROOTS:
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.name.startswith(".") or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".mp4", ".webm", ".mov", ".m4v"}:
                continue
            metadata = path.stat()
            r2_record = _r2_upload_record(path)
            assets.append(
                {
                    "id": asset_id(path),
                    "url": asset_url(path),
                    "name": path.name,
                    "media_type": "video" if path.suffix.lower() in {".mp4", ".webm", ".mov", ".m4v"} else "image",
                    "source": "local" if path.is_relative_to(OUTPUTS_DIR / "imported") else "generated",
                    "size": metadata.st_size,
                    "modified_at": datetime.fromtimestamp(metadata.st_mtime, tz=timezone.utc).isoformat(),
                    "r2_url": r2_record["url"] if r2_record else None,
                    "r2_object_key": r2_record["object_key"] if r2_record else None,
                }
            )
    return sorted(assets, key=lambda item: item["modified_at"], reverse=True)


app = FastAPI(title="AIGC Studio", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type"],
)


@app.on_event("startup")
def startup() -> None:
    _restore_module_environments()
    _load_jobs()
    _load_r2_uploads()
    _load_batches()
    _load_mixes()


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/providers")
def providers() -> dict[str, Any]:
    _stored_module_records()
    listed: list[dict[str, Any]] = []
    seen: set[str] = set()
    for module_id in ("image.hermes", "image.liblib", "video.veo", "video.seedance"):
        record = _public_provider_record(module_id)
        listed.append(record)
        seen.add(record["id"])
    for module_id, spec in MODULE_SPECS.items():
        if spec.get("category") not in {"image", "video"}:
            continue
        record = _public_provider_record(module_id)
        if record["id"] in seen:
            continue
        listed.append(record)
        seen.add(record["id"])
    return {"providers": listed}


@app.get("/api/settings/hermes")
def get_hermes_settings() -> dict[str, Any]:
    return _legacy_hermes_response(_module_settings("image.hermes"))


@app.put("/api/settings/hermes")
def put_hermes_settings(request: HermesSettingsRequest) -> dict[str, Any]:
    try:
        return _write_hermes_settings(request)
    except (OSError, ValueError) as error:
        raise HTTPException(status_code=400, detail={"code": "invalid_settings", "message": str(error)}) from error


@app.post("/api/settings/hermes/test")
def test_hermes_settings(request: HermesSettingsRequest | None = None) -> dict[str, Any]:
    try:
        if request is None:
            client = _hermes_client()
        else:
            current = _module_settings("image.hermes", include_key=True)
            values = request.model_dump(exclude={"clear_api_key", "api_key"})
            _validate_outbound_api_url(values["api_url"])
            values["api_key"] = "" if request.clear_api_key else (parse_api_key(request.api_key) or current["api_key"])
            client = HermesClient(**values)
        client.test_connection()
    except (HermesClientError, ValueError) as error:
        raise HTTPException(status_code=502, detail={"code": "hermes_unavailable", "message": "Hermes 连接失败，请检查 API 地址、模型和密钥"}) from error
    return {"ok": True, "provider": "hermes", "message": "Hermes 连接成功"}


@app.get("/api/settings/modules")
def get_module_settings() -> dict[str, Any]:
    return {"modules": [_module_settings(module_id) for module_id in _registered_module_ids()]}


@app.get("/api/settings/modules/{module_id}")
def get_single_module_settings(module_id: str) -> dict[str, Any]:
    try:
        return _module_settings(module_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail={"code": "unknown_module", "message": "未找到该 API 模块"}) from error


@app.put("/api/settings/modules/{module_id}")
def put_module_settings(module_id: str, request: ModuleSettingsRequest) -> dict[str, Any]:
    try:
        return _write_module_settings(module_id, request)
    except KeyError as error:
        raise HTTPException(status_code=404, detail={"code": "unknown_module", "message": "未找到该 API 模块"}) from error
    except (OSError, ValueError) as error:
        raise HTTPException(status_code=400, detail={"code": "invalid_settings", "message": str(error)}) from error


@app.post("/api/settings/modules/{module_id}/test")
def test_module_settings(module_id: str, request: ModuleSettingsRequest) -> dict[str, Any]:
    try:
        _validate_module_draft(module_id, request)
    except KeyError as error:
        raise HTTPException(status_code=404, detail={"code": "unknown_module", "message": "未找到该 API 模块"}) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail={"code": "invalid_settings", "message": str(error)}) from error
    try:
        current = _module_settings(module_id, include_key=True)
        api_key = "" if request.clear_api_key else (parse_api_key(request.api_key) or current["api_key"])
        if not api_key:
            raise ValueError("API Key 未配置")
        _module_draft_client(module_id, request, api_key).test_connection()
    except (HermesClientError, LiblibClientError, VideoProviderRequestError, TerraPlannerError, OSError, ValueError) as error:
        PROVIDER_LOGGER.error(
            "module connection failed module=%s error_type=%s status=%s detail=%s",
            module_id,
            type(error).__name__,
            getattr(error, "status", None),
            getattr(error, "response_detail", None) or str(error),
        )
        raise HTTPException(status_code=502, detail={"code": "module_connection_failed", "message": "模块连接失败，请检查 API 地址、模型和密钥"}) from error
    return {"ok": True, "module_id": module_id, "message": "模块配置可用"}


@app.post("/api/settings/modules/{module_id}/models")
def discover_module_models(module_id: str, request: ModuleSettingsRequest) -> dict[str, Any]:
    try:
        _validate_module_draft(module_id, request)
        current = _module_settings(module_id, include_key=True)
        api_key = "" if request.clear_api_key else (parse_api_key(request.api_key) or current["api_key"])
        if not api_key:
            raise ValueError("API Key is required for model discovery")
    except KeyError as error:
        raise HTTPException(
            status_code=404,
            detail={"code": "unknown_module", "message": "API module was not found"},
        ) from error
    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_settings", "message": str(error)},
        ) from error
    try:
        raw_models = _module_draft_client(module_id, request, api_key).list_models()
        models = _normalize_provider_models(raw_models)
    except (HermesClientError, LiblibClientError, VideoProviderRequestError, TerraPlannerError, OSError, ValueError) as error:
        raise HTTPException(
            status_code=502,
            detail={
                "code": "model_discovery_failed",
                "message": "Could not discover models; check the API address and key, or enter a model ID manually",
            },
        ) from error
    selected_model = (
        request.model
        if any(item["id"] == request.model for item in models)
        else (models[0]["id"] if models else request.model)
    )
    return {
        "ok": True,
        "module_id": module_id,
        "models": models,
        "source": "provider" if models else "manual",
        "manual_entry": True,
        "selected_model": selected_model,
        "message": (
            "Models discovered"
            if models
            else "The provider returned no recognizable models; enter a model ID manually"
        ),
    }


@app.post("/api/settings/gateways/detect")
def detect_production_gateway(request: GatewayDetectRequest) -> dict[str, Any]:
    try:
        api_url = _validate_outbound_api_url(request.api_url)
        api_key = parse_api_key(request.api_key)
        if not api_key:
            raise ValueError("自动识别网关需要 API Key")
        detection = detect_gateway(
            api_url=api_url,
            api_key=api_key,
            category=request.category,
            model=request.model,
            occupied_slugs=_occupied_custom_slugs(),
        )
    except GatewayCategoryMismatch as error:
        raise HTTPException(status_code=400, detail={"code": "gateway_category_mismatch", "message": str(error)}) from error
    except GatewayDetectionError as error:
        raise HTTPException(
            status_code=502,
            detail={"code": "gateway_detection_failed", "message": "无法识别该网关，请检查 API 地址和密钥"},
        ) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail={"code": "invalid_settings", "message": str(error)}) from error
    return {
        "ok": True,
        "protocol": detection["protocol"],
        "category": detection["category"],
        "label": detection["label"],
        "models": detection["models"],
        "selected_model": detection["selected_model"],
        "suggested_name": detection["suggested_name"],
        "suggested_slug": detection["suggested_slug"],
        "source": detection["source"],
        "manual_entry": True,
        "message": "已识别生产网关" if detection["models"] else "已匹配生产网关，请手动填写模型 ID",
    }


@app.post("/api/settings/modules", status_code=201)
def create_custom_module(request: CreateCustomModuleRequest) -> dict[str, Any]:
    try:
        return _create_custom_module(request)
    except GatewayCategoryMismatch as error:
        raise HTTPException(status_code=400, detail={"code": "gateway_category_mismatch", "message": str(error)}) from error
    except GatewayDetectionError as error:
        raise HTTPException(
            status_code=502,
            detail={"code": "gateway_detection_failed", "message": "无法识别该网关，请检查 API 地址和密钥"},
        ) from error
    except (OSError, ValueError) as error:
        raise HTTPException(status_code=400, detail={"code": "invalid_settings", "message": str(error)}) from error


@app.delete("/api/settings/modules/{module_id}", status_code=204)
def delete_custom_module(module_id: str) -> Response:
    try:
        _delete_custom_module(module_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail={"code": "unknown_module", "message": "未找到该 API 模块"}) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail={"code": "invalid_settings", "message": str(error)}) from error
    return Response(status_code=204)


@app.post("/api/generation-batches", status_code=202)
def create_batch(request: BatchRequest) -> dict[str, Any]:
    available, reason = _provider_available("hermes")
    if not available:
        raise HTTPException(status_code=503, detail={"code": "provider_unavailable", "message": reason})
    try:
        for item in request.items:
            if item.image_asset_id:
                resolve_asset(item.image_asset_id)
            for identifier in item.reference_asset_ids:
                resolve_asset(identifier)
    except ValueError as error:
        raise HTTPException(status_code=422, detail={"code": "invalid_asset", "message": str(error)}) from error
    batch_id = uuid.uuid4().hex
    batch = {
        "id": batch_id, "status": "queued", "idempotency_key": request.idempotency_key,
        "max_concurrency": request.options.max_concurrency,
        "items": [item.model_dump() | {"status": "queued"} for item in request.items],
        "created_at": _now(), "updated_at": _now(),
    }
    with BATCHES_LOCK:
        if request.idempotency_key:
            existing = next((stored for stored in BATCHES.values() if stored.get("idempotency_key") == request.idempotency_key), None)
            if existing:
                return {"batch_id": existing["id"], "status": existing["status"], "total": len(existing["items"])}
        BATCHES[batch_id] = batch
    _save_batches()
    BATCH_EXECUTOR.submit(_run_batch, batch_id)
    return {"batch_id": batch_id, "status": "queued", "total": len(batch["items"])}


@app.get("/api/generation-batches/{batch_id}")
def get_batch(batch_id: str) -> dict[str, Any]:
    with BATCHES_LOCK:
        batch = BATCHES.get(batch_id)
        if batch is None:
            raise HTTPException(status_code=404, detail="批量任务不存在")
        return copy.deepcopy(batch)


@app.post("/api/mixes/plan")
def create_mix_plan(request: MixPlanRequest) -> dict[str, Any]:
    """Return a validated, renderer-compatible intelligent edit plan."""
    try:
        for clip in request.clips:
            resolve_asset(clip.asset_id)
    except ValueError as error:
        raise HTTPException(status_code=422, detail={"code": "invalid_asset", "message": str(error)}) from error
    return _create_mix_plan(request).model_dump()


@app.post("/api/mixes", status_code=202)
def create_mix(request: MixRequest) -> dict[str, Any]:
    if not shutil.which("ffmpeg"):
        raise HTTPException(status_code=503, detail={"code": "ffmpeg_unavailable", "message": "未找到 FFmpeg，请先安装并加入 PATH"})
    try:
        for clip in request.clips:
            resolve_asset(clip.asset_id)
    except ValueError as error:
        raise HTTPException(status_code=422, detail={"code": "invalid_asset", "message": str(error)}) from error
    if request.auto_plan:
        request = request.model_copy(update={"plan": _create_mix_plan(_planning_request(request))})
    if request.plan:
        try:
            for clip in request.plan.clips:
                resolve_asset(clip.asset_id)
        except ValueError as error:
            raise HTTPException(status_code=422, detail={"code": "invalid_plan_asset", "message": str(error)}) from error
    mix_id = uuid.uuid4().hex
    effective_clips = request.plan.clips if request.plan else request.clips
    record = {
        "id": mix_id,
        "status": "queued",
        "phase": "等待智能混剪",
        "clips": len(effective_clips),
        "plan": request.plan.model_dump() if request.plan else None,
        "created_at": _now(),
        "updated_at": _now(),
    }
    with MIXES_LOCK:
        MIXES[mix_id] = record
    _save_mixes()
    MIX_EXECUTOR.submit(_run_mix, mix_id, request)
    return {"mix_id": mix_id, "status": "queued", "plan": record["plan"]}


@app.get("/api/mixes/{mix_id}")
def get_mix(mix_id: str) -> dict[str, Any]:
    with MIXES_LOCK:
        mix = MIXES.get(mix_id)
        if mix is None:
            raise HTTPException(status_code=404, detail="混剪任务不存在")
        return copy.deepcopy(mix)


@app.post("/api/media/import")
async def import_media(files: list[UploadFile] = File(...), relative_paths: list[str] = Form(default=[])) -> dict[str, Any]:
    if len(files) > 200:
        raise HTTPException(status_code=422, detail="单次最多导入 200 个文件")
    import_id = uuid.uuid4().hex
    target_root = OUTPUTS_DIR / "imported" / import_id
    allowed = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4", ".mov", ".webm"}
    errors: list[str] = []
    staging_parent = OUTPUTS_DIR / "imported"
    staging_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".aigc-import-", dir=staging_parent) as temporary:
        staging_root = Path(temporary)
        staged: list[tuple[str, Path]] = []
        for index, upload in enumerate(files):
            raw_name = relative_paths[index] if index < len(relative_paths) else (upload.filename or "")
            name = Path(raw_name.replace("\\", "/")).name
            suffix = Path(name).suffix.lower()
            if not name or suffix not in allowed:
                errors.append(f"{name or '未命名文件'}: 不支持的文件类型")
                continue
            staged_name = f"{index + 1:04d}_{name}"
            staged_path = staging_root / staged_name
            size = 0
            header = b""
            with staged_path.open("wb") as destination:
                while chunk := await upload.read(1024 * 1024):
                    if len(header) < 16:
                        header = (header + chunk)[:16]
                    size += len(chunk)
                    if size > 100 * 1024 * 1024:
                        break
                    destination.write(chunk)
            if size == 0:
                errors.append(f"{name}: 文件为空")
                staged_path.unlink(missing_ok=True)
                continue
            if size > 100 * 1024 * 1024:
                errors.append(f"{name}: 超过 100 MiB 限制")
                staged_path.unlink(missing_ok=True)
                continue
            if not _media_signature_matches(name, header):
                errors.append(f"{name}: 文件内容与扩展名不匹配")
                staged_path.unlink(missing_ok=True)
                continue
            staged.append((staged_name, staged_path))
        if errors:
            raise HTTPException(status_code=422, detail={"code": "invalid_media_import", "message": "；".join(errors[:5])})
        if not staged:
            raise HTTPException(status_code=422, detail={"code": "empty_media_import", "message": "没有可导入的图片或视频"})
        try:
            target_root.mkdir(parents=True, exist_ok=False)
            for name, staged_path in staged:
                shutil.move(str(staged_path), target_root / name)
        except OSError as error:
            shutil.rmtree(target_root, ignore_errors=True)
            raise HTTPException(status_code=500, detail={"code": "media_import_failed", "message": "本地素材导入失败"}) from error
    return {"assets": [item for item in _assets() if item["id"].startswith(f"imported/{import_id}/")]}


@app.post("/api/prompts/preview")
def prompt_preview(payload: PromptPreviewRequest) -> dict[str, Any]:
    try:
        prompt = preview_prompt(payload.mode, payload.request, provider=payload.provider)
    except (ValueError, ShapewearRequestError, ClothingImageRequestError, TikTokClothingRequestError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    warnings = []
    if payload.mode == "tiktok_10s":
        warnings.append("10 秒与 9:16 为 Prompt 目标，当前 Provider 参数尚未强制锁定。")
    return {"prompt": prompt, "warnings": warnings}


@app.post("/api/generations", status_code=202)
def create_generation(payload: GenerationRequest) -> dict[str, str]:
    try:
        # Resolve local references before checking provider availability so bad
        # asset identifiers fail synchronously at task creation time.
        _resolve_reference_images(payload.reference_images)
    except ValueError as error:
        raise HTTPException(status_code=422, detail={"code": "invalid_reference_images", "message": str(error)}) from error
    provider = _provider_for_job(payload)
    available, reason = _provider_available(provider)
    if not available:
        raise HTTPException(status_code=503, detail={"code": "provider_unavailable", "message": reason})
    try:
        preview_prompt(payload.mode, payload.request, provider=payload.provider)
    except (ValueError, ShapewearRequestError, ClothingImageRequestError, TikTokClothingRequestError) as error:
        raise HTTPException(status_code=400, detail={"code": "validation_error", "message": str(error)}) from error
    job = GenerationJob(id=uuid.uuid4().hex, payload=payload)
    with JOBS_LOCK:
        JOBS[job.id] = job
        _save_jobs()
    JOB_EXECUTOR.submit(_run_job, job.id)
    return {"job_id": job.id, "status": "queued"}


@app.get("/api/jobs")
def list_jobs(limit: int = 20) -> dict[str, Any]:
    bounded_limit = max(1, min(limit, 100))
    with JOBS_LOCK:
        jobs = sorted(JOBS.values(), key=lambda item: item.created_at, reverse=True)[:bounded_limit]
        return {"jobs": [job.public() for job in jobs]}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        return job.public()


@app.get("/api/assets")
def list_assets() -> dict[str, Any]:
    return {"assets": _assets()}


@app.get("/api/storage/r2")
def r2_status() -> dict[str, Any]:
    return _r2_status()


@app.get("/api/assets/{identifier:path}")
def get_asset(identifier: str) -> Response:
    try:
        path = Path(resolve_asset(identifier))
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=content_type, content_disposition_type="inline", filename=path.name)


@app.post("/api/storage/r2/assets/{identifier:path}")
def upload_asset_to_r2(identifier: str, request: R2UploadRequest) -> dict[str, Any]:
    try:
        path = Path(resolve_asset(identifier))
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    try:
        record = _publish_asset_to_r2(path, request)
    except R2ConfigurationError as error:
        raise HTTPException(
            status_code=503,
            detail={"code": "r2_unavailable", "message": "Cloudflare R2 尚未完成本地配置"},
        ) from error
    except R2ValidationError as error:
        raise HTTPException(status_code=422, detail={"code": "r2_validation_error", "message": str(error)}) from error
    except R2UploadError as error:
        raise HTTPException(
            status_code=502,
            detail={"code": "r2_upload_failed", "message": "Cloudflare R2 上传失败，请检查连接与存储配置后重试"},
        ) from error
    return {"asset_id": asset_id(path), "url": record["url"], "object_key": record["object_key"], "uploaded_at": record["uploaded_at"]}


@app.post("/api/assets/{identifier:path}/open-directory")
def open_asset_directory(identifier: str) -> dict[str, bool]:
    try:
        path = Path(resolve_asset(identifier))
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    if not hasattr(os, "startfile"):
        raise HTTPException(status_code=501, detail="当前系统不支持打开本地输出目录")
    try:
        os.startfile(str(path.parent))  # type: ignore[attr-defined]  # Windows local-workbench convenience.
    except OSError as error:
        raise HTTPException(status_code=500, detail="无法打开本地输出目录") from error
    return {"opened": True}


if APP_ROOT.is_dir():
    app.mount("/", StaticFiles(directory=APP_ROOT, html=True), name="studio")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("api_server:app", host="127.0.0.1", port=8000, reload=False)
