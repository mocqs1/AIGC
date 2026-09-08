"""Match a pasted API URL and key to a builtin production-gateway protocol.

Users should not pick Hermes/Veo/Seedance templates. The adapter infers the
protocol from the host, model id, and a bounded ``/models`` probe, then returns
a catalog the settings UI can save.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any
from urllib.parse import urlparse

from providers.credential_parser import parse_api_key, parse_api_url
from providers.image.hermes_client import HermesClient, HermesClientError
from providers.liblib.client import LiblibClient, LiblibClientError
from providers.mix.codex_terra_planner import CodexTerraPlanner, TerraPlannerError
from providers.video.google_veo_provider import GoogleVeoClient
from providers.video.rest_client import VideoProviderRequestError
from providers.video.seedance_provider import SeedanceClient


PROBE_TIMEOUT = 15.0
ARK_HOST = "ark.cn-beijing.volces.com"

PROTOCOL_LABELS = {
    "image.hermes": "Hermes 兼容图片网关",
    "image.liblib": "火山方舟 / Liblib 图片网关",
    "video.veo": "Google Veo 视频网关",
    "video.seedance": "Seedance 视频网关",
    "mix.codex_terra": "OpenAI 兼容规划网关",
}
PROTOCOL_CATEGORY = {
    "image.hermes": "image",
    "image.liblib": "image",
    "video.veo": "video",
    "video.seedance": "video",
    "mix.codex_terra": "mix_planner",
}
CATEGORY_PROTOCOLS = {
    "image": ("image.hermes", "image.liblib"),
    "video": ("video.veo", "video.seedance"),
    "mix_planner": ("mix.codex_terra",),
}
HOST_PROTOCOL = {
    "generativelanguage.googleapis.com": "video.veo",
    "openapi.liblib.art": "image.liblib",
    "www.liblib.art": "image.liblib",
    "api.evolink.ai": "video.seedance",
    "files-api.evolink.ai": "video.seedance",
    "aiapi.yicheng.bj.cn": "image.hermes",
    ARK_HOST: "ark",
}
HOST_SLUGS = {
    "generativelanguage.googleapis.com": "veo",
    "openapi.liblib.art": "liblib_gw",
    "www.liblib.art": "liblib_gw",
    "api.evolink.ai": "evolink",
    "files-api.evolink.ai": "evolink",
    "aiapi.yicheng.bj.cn": "hermes_gw",
    ARK_HOST: "ark",
}
RESERVED_SLUGS = {"hermes", "liblib", "veo", "seedance", "codex_terra"}


class GatewayDetectionError(RuntimeError):
    """Raised when URL+key cannot be matched to a supported production gateway."""


class GatewayCategoryMismatch(ValueError):
    """Raised when the URL matches a gateway in a different capability tab."""


def hostname_of(api_url: str) -> str:
    try:
        return (urlparse(parse_api_url(api_url)).hostname or "").lower().rstrip(".")
    except ValueError:
        return (urlparse(api_url).hostname or "").lower().rstrip(".")


def classify_host(api_url: str, *, model: str = "", category: str | None = None) -> str | None:
    """Return a protocol id from a well-known production hostname."""
    host = hostname_of(api_url)
    hinted = HOST_PROTOCOL.get(host)
    if hinted == "ark":
        model_id = model.lower()
        if category == "video" or "seedance" in model_id:
            return "video.seedance"
        return "image.liblib"
    if hinted:
        return hinted
    if host.endswith(".googleapis.com") and "generativelanguage" in host:
        return "video.veo"
    if host.endswith(".volces.com") and host.startswith("ark."):
        if category == "video" or "seedance" in model.lower():
            return "video.seedance"
        return "image.liblib"
    return None


def classify_model_id(model: str) -> str | None:
    text = model.strip().lower()
    if not text:
        return None
    if "seedance" in text or text.startswith("doubao-seedance"):
        return "video.seedance"
    if "seedream" in text or text.startswith("doubao-seedream"):
        return "image.liblib"
    if text.startswith("veo-") or "veo" in text.split("-")[0:2]:
        return "video.veo"
    if "gpt-image" in text:
        return "image.hermes"
    if "terra" in text or text.startswith("gpt-5"):
        return "mix.codex_terra"
    return None


def match_protocol(
    api_url: str,
    *,
    model: str = "",
    category: str | None = None,
    stored_protocol: str | None = None,
) -> str:
    """Pick the builtin protocol a configured module should speak.

    Host and model fingerprints outrank a stored template so a custom module
    created from Hermes can still talk to Ark after the user pastes a Volcengine
    URL. A stored protocol is the fallback for generic OpenAI-compatible hosts.
    """
    hinted = classify_host(api_url, model=model, category=category)
    if hinted and (not category or PROTOCOL_CATEGORY[hinted] == category):
        return hinted
    model_hint = classify_model_id(model)
    if model_hint and (not category or PROTOCOL_CATEGORY[model_hint] == category):
        return model_hint
    if stored_protocol in PROTOCOL_CATEGORY and (
        not category or PROTOCOL_CATEGORY[stored_protocol] == category
    ):
        return stored_protocol
    if category in CATEGORY_PROTOCOLS:
        return CATEGORY_PROTOCOLS[category][0]
    return "image.hermes"


def candidate_protocols(api_url: str, *, category: str | None = None, model: str = "") -> list[str]:
    hinted = classify_host(api_url, model=model, category=category)
    if hinted:
        hinted_category = PROTOCOL_CATEGORY[hinted]
        if category and hinted_category != category:
            raise GatewayCategoryMismatch(
                f"该地址匹配{PROTOCOL_LABELS[hinted]}，请切换到「{_category_label(hinted_category)}」后再识别"
            )
        return [hinted]
    model_hint = classify_model_id(model)
    if model_hint:
        model_category = PROTOCOL_CATEGORY[model_hint]
        if category and model_category != category:
            raise GatewayCategoryMismatch(
                f"该模型 ID 匹配{PROTOCOL_LABELS[model_hint]}，请切换到「{_category_label(model_category)}」后再识别"
            )
        rest = [item for item in CATEGORY_PROTOCOLS[model_category] if item != model_hint]
        return [model_hint, *rest]
    if category in CATEGORY_PROTOCOLS:
        return list(CATEGORY_PROTOCOLS[category])
    return [
        "image.hermes",
        "image.liblib",
        "video.veo",
        "video.seedance",
        "mix.codex_terra",
    ]


def _category_label(category: str) -> str:
    return {"image": "图片", "video": "视频", "mix_planner": "智能混剪"}.get(category, category)


def probe_client(protocol: str, api_url: str, api_key: str, model: str = "") -> Any:
    """Build a short-timeout client used only for catalog probes."""
    if protocol == "image.hermes":
        return HermesClient(
            api_url=api_url,
            api_key=api_key,
            model=model or "gpt-image-2",
            timeout=PROBE_TIMEOUT,
            query_timeout=PROBE_TIMEOUT,
            result_timeout=PROBE_TIMEOUT,
            download_timeout=PROBE_TIMEOUT,
        )
    if protocol == "image.liblib":
        host = hostname_of(api_url)
        if host == ARK_HOST or host.endswith(".volces.com") or "seedream" in model.lower():
            return HermesClient(
                api_url=api_url,
                api_key=api_key,
                model=model or "doubao-seedream-5-0-pro-260628",
                timeout=PROBE_TIMEOUT,
                query_timeout=PROBE_TIMEOUT,
                result_timeout=PROBE_TIMEOUT,
                download_timeout=PROBE_TIMEOUT,
            )
        return LiblibClient(api_url=api_url, api_key=api_key, model=model or "", timeout=PROBE_TIMEOUT)
    if protocol == "video.veo":
        return GoogleVeoClient(
            api_url=api_url,
            api_key=api_key,
            model=model or "veo-3.1-fast-generate-preview",
            timeout=PROBE_TIMEOUT,
        )
    if protocol == "video.seedance":
        return SeedanceClient(
            api_url=api_url,
            api_key=api_key,
            text_model=model or "doubao-seedance-2-0-260128",
            image_model=model or "doubao-seedance-2-0-260128",
            timeout=PROBE_TIMEOUT,
        )
    if protocol == "mix.codex_terra":
        return CodexTerraPlanner(api_url=api_url, api_key=api_key, model=model or "gpt-5.6-terra")
    raise ValueError(f"unsupported protocol: {protocol}")


def list_models_for_protocol(protocol: str, api_url: str, api_key: str, model: str = "") -> Any:
    return probe_client(protocol, api_url, api_key, model).list_models()


def normalize_provider_models(payload: Any) -> list[dict[str, str]]:
    candidates: Any = payload
    if isinstance(payload, Mapping):
        candidates = next(
            (
                payload[key]
                for key in ("data", "models", "items", "result")
                if isinstance(payload.get(key), list)
            ),
            [],
        )
    if not isinstance(candidates, list):
        return []
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for candidate in candidates:
        if isinstance(candidate, str):
            model_id = candidate.strip()
            name = model_id
        elif isinstance(candidate, Mapping):
            raw_id = (
                candidate.get("id")
                or candidate.get("model")
                or candidate.get("model_id")
                or candidate.get("modelId")
                or candidate.get("name")
            )
            if not isinstance(raw_id, str):
                continue
            model_id = raw_id.strip()
            if model_id.startswith("models/"):
                model_id = model_id[7:]
            raw_name = (
                candidate.get("displayName")
                or candidate.get("display_name")
                or candidate.get("label")
                or candidate.get("name")
            )
            name = raw_name.strip() if isinstance(raw_name, str) and raw_name.strip() else model_id
        else:
            continue
        if model_id and model_id not in seen:
            seen.add(model_id)
            normalized.append({"id": model_id, "name": name})
    return normalized


def _refine_protocol(protocol: str, models: Sequence[Mapping[str, str]], category: str | None) -> str:
    votes: dict[str, int] = {}
    for item in models:
        hinted = classify_model_id(str(item.get("id") or ""))
        if not hinted:
            continue
        if category and PROTOCOL_CATEGORY[hinted] != category:
            continue
        votes[hinted] = votes.get(hinted, 0) + 1
    if not votes:
        return protocol
    winner = max(votes, key=votes.get)
    if winner != protocol and PROTOCOL_CATEGORY.get(winner) == PROTOCOL_CATEGORY.get(protocol):
        return winner
    return protocol


def _select_model(models: Sequence[Mapping[str, str]], requested: str, protocol: str) -> str:
    ids = [item["id"] for item in models if item.get("id")]
    if requested and requested in ids:
        return requested
    preferred = classify_model_id(requested)
    if preferred:
        for item in ids:
            if classify_model_id(item) == preferred:
                return item
    for item in ids:
        if classify_model_id(item) == protocol:
            return item
    return ids[0] if ids else requested


def suggested_slug(
    api_url: str,
    category: str,
    occupied: set[str],
    protocol: str,
) -> str:
    host = hostname_of(api_url)
    base = HOST_SLUGS.get(host) or host.split(".")[0] or protocol.split(".", 1)[-1]
    slug = "".join(character if character.isalnum() else "_" for character in base.lower()).strip("_")
    slug = slug[:24] or protocol.split(".", 1)[-1]
    if slug[0].isdigit():
        slug = f"gw_{slug}"
    blocked = occupied | RESERVED_SLUGS
    if slug not in blocked:
        return slug
    candidate = f"{slug}_gw"[:32]
    index = 2
    while candidate in blocked:
        suffix = f"_{index}"
        candidate = f"{slug[: 32 - len(suffix)]}{suffix}"
        index += 1
    return candidate


def suggested_name(protocol: str, api_url: str) -> str:
    host = hostname_of(api_url)
    if host == ARK_HOST or (host.endswith(".volces.com") and host.startswith("ark.")):
        if protocol == "video.seedance":
            return "火山方舟 Seedance 网关"
        return "火山方舟图片网关"
    if host in HOST_PROTOCOL:
        return PROTOCOL_LABELS.get(protocol, "自定义网关")
    return PROTOCOL_LABELS.get(protocol, "自定义网关")


def detect_gateway(
    *,
    api_url: str,
    api_key: str,
    category: str | None = None,
    model: str = "",
    occupied_slugs: set[str] | None = None,
    probe: Callable[[str, str, str, str], Any] | None = None,
) -> dict[str, Any]:
    """Probe the gateway and return protocol, models, and a suggested module id."""
    parsed_url = parse_api_url(api_url)
    parsed_key = parse_api_key(api_key)
    if not parsed_key:
        raise GatewayDetectionError("自动识别网关需要 API Key")
    protocols = candidate_protocols(parsed_url, category=category, model=model)
    probe_fn = probe or list_models_for_protocol
    last_error: Exception | None = None
    for protocol in protocols:
        try:
            raw = probe_fn(protocol, parsed_url, parsed_key, model)
            models = normalize_provider_models(raw)
            resolved = _refine_protocol(protocol, models, category or PROTOCOL_CATEGORY[protocol])
            selected = _select_model(models, model, resolved)
            occupied = occupied_slugs or set()
            return {
                "protocol": resolved,
                "category": PROTOCOL_CATEGORY[resolved],
                "label": PROTOCOL_LABELS[resolved],
                "models": models,
                "selected_model": selected,
                "suggested_name": suggested_name(resolved, parsed_url),
                "suggested_slug": suggested_slug(
                    parsed_url,
                    PROTOCOL_CATEGORY[resolved],
                    occupied,
                    resolved,
                ),
                "source": "provider" if models else "manual",
                "manual_entry": True,
            }
        except (
            HermesClientError,
            LiblibClientError,
            VideoProviderRequestError,
            TerraPlannerError,
            OSError,
            ValueError,
            TypeError,
            RuntimeError,
        ) as error:
            last_error = error
            continue
    raise GatewayDetectionError("无法识别该网关，请确认 API 地址、密钥和当前能力分类") from last_error
