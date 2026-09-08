"""Normalize pasted provider API URLs and keys before they are stored or sent.

Users commonly paste console snippets, curl commands, ``Bearer`` prefixes,
quoted values, invisible characters, credential query strings, or a full
resource path such as ``/v1/images/generations``. Those inputs otherwise
become ``Authorization: Bearer Bearer …`` or doubled request paths.
"""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import parse_qsl, urlparse, urlunparse

_INVISIBLE_CHARACTERS = dict.fromkeys(
    map(
        ord,
        (
            "\ufeff",
            "\u200b",
            "\u200c",
            "\u200d",
            "\u2060",
            "\u00ad",
            "\u180e",
            "\u2028",
            "\u2029",
        ),
    )
)
_QUOTE_CHARACTERS = "\"'`“”‘’「」『』‹›«»<>"
_CREDENTIAL_QUERY_KEYS = {"api_key", "apikey", "key", "access_token", "token", "auth"}
_RESOURCE_SUFFIXES = (
    "/contents/generations/tasks",
    "/images/generations",
    "/images/edits",
    "/chat/completions",
    "/completions",
    "/responses",
    "/v1/videos/generations",
    "/v1/videos",
)
_MODELS_RESOURCE_RE = re.compile(r"/models(?:/[^/]+)?$", re.IGNORECASE)
_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)
_URL_RE = re.compile(r"https?://[^\s'\"\\]+", re.IGNORECASE)
_AUTHORIZATION_RE = re.compile(
    r"(?i)(?:authorization\s*[:=]\s*)?(?:bearer|token|api[-_]?key)\s+([^\s'\"\\,;]+)"
)
_LABELED_KEY_RE = re.compile(
    r"(?i)(?:(?:api[-_\s]?key|secret|token|authorization)\s*[:=]\s*)(.+)"
)
_KEY_PREFIX_RE = re.compile(r"(?i)^(?:bearer|token|api[-_]?key)\s+")
_ENV_KEY_RE = re.compile(
    r"(?im)^(?:export\s+)?[A-Z][A-Z0-9_]*(?:API_KEY|ACCESS_TOKEN|TOKEN|SECRET)\s*=\s*(.+)$"
)


def _clean_paste(value: str) -> str:
    text = unicodedata.normalize("NFKC", value).translate(_INVISIBLE_CHARACTERS)
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in _QUOTE_CHARACTERS:
        text = text[1:-1].strip()
    return text.strip(_QUOTE_CHARACTERS + " \t\n")


def _first_url(text: str) -> str:
    match = _URL_RE.search(text)
    if not match:
        return ""
    return match.group(0).rstrip("\\,;.)]")


def _extract_labeled_key(text: str) -> str:
    env_match = _ENV_KEY_RE.search(text)
    if env_match:
        return env_match.group(1).strip().strip(_QUOTE_CHARACTERS)
    auth_match = _AUTHORIZATION_RE.search(text)
    if auth_match:
        return auth_match.group(1).strip().strip(_QUOTE_CHARACTERS)
    labeled = _LABELED_KEY_RE.search(text)
    if labeled:
        return labeled.group(1).strip().strip(_QUOTE_CHARACTERS)
    return ""


def parse_api_key(value: str | None) -> str:
    """Return a header-ready secret, or an empty string when none is present."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("API Key 格式无效")
    text = _clean_paste(value)
    if not text:
        return ""
    extracted = _extract_labeled_key(text)
    if extracted:
        candidate = _clean_paste(extracted)
        if candidate.lower() not in {"bearer", "token", "undefined", "null"}:
            text = candidate
    while True:
        stripped = _KEY_PREFIX_RE.sub("", text).strip().strip(_QUOTE_CHARACTERS)
        if stripped == text:
            break
        text = stripped
    # Provider keys are tokens. Internal whitespace is almost always a copy artifact.
    text = re.sub(r"\s+", "", text)
    if text.lower() in {"bearer", "token", "undefined", "null"}:
        return ""
    return text


def _strip_resource_path(path: str) -> str:
    normalized = path.rstrip("/") or "/"
    lowered = normalized.lower()
    changed = True
    while changed:
        changed = False
        for suffix in _RESOURCE_SUFFIXES:
            if lowered.endswith(suffix):
                normalized = normalized[: -len(suffix)] or "/"
                lowered = normalized.lower()
                changed = True
                break
        models_match = _MODELS_RESOURCE_RE.search(lowered)
        if models_match:
            normalized = normalized[: models_match.start()] or "/"
            lowered = normalized.lower()
            changed = True
    return normalized.rstrip("/") or "/"


def _public_netloc(parsed) -> str:
    host = parsed.hostname
    if not host:
        raise ValueError("API 地址必须是 HTTP 或 HTTPS URL")
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    if parsed.port:
        return f"{host}:{parsed.port}"
    return host


def parse_api_url(value: str) -> str:
    """Return a credential-free API root suitable for provider clients."""
    if not isinstance(value, str):
        raise ValueError("API 地址格式无效")
    text = _clean_paste(value)
    if not text:
        raise ValueError("API 地址不能为空")
    extracted = _first_url(text)
    if extracted:
        text = _clean_paste(extracted)
    if text.startswith("//"):
        text = "https:" + text
    elif not _SCHEME_RE.match(text):
        text = "https://" + text.lstrip("/")
    parsed = urlparse(text)
    if parsed.scheme.lower() not in {"http", "https"}:
        raise ValueError("API 地址必须是 HTTP 或 HTTPS URL")
    leftover_query = [
        (key, item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in _CREDENTIAL_QUERY_KEYS
    ]
    if leftover_query:
        raise ValueError("API 地址必须是无凭据、无查询参数的 HTTP 或 HTTPS URL")
    cleaned = urlunparse(
        (
            parsed.scheme.lower(),
            _public_netloc(parsed),
            _strip_resource_path(parsed.path or "/"),
            "",
            "",
            "",
        )
    )
    return cleaned.rstrip("/")


def parse_provider_settings(api_url: str, api_key: str | None = None) -> tuple[str, str]:
    """Normalize a pasted URL/key pair, recovering a key hidden in the URL field."""
    text = api_url if isinstance(api_url, str) else ""
    recovered_key = parse_api_key(api_key)
    if not recovered_key:
        candidate = _first_url(_clean_paste(text)) or _clean_paste(text)
        parsed = urlparse(
            candidate if "://" in candidate or candidate.startswith("//") else f"https://{candidate}"
        )
        if parsed.scheme in {"http", "https"}:
            for key, item in parse_qsl(parsed.query, keep_blank_values=True):
                if key.lower() in _CREDENTIAL_QUERY_KEYS and item.strip():
                    recovered_key = parse_api_key(item)
                    break
            if not recovered_key and parsed.password and not parsed.username:
                recovered_key = parse_api_key(parsed.password)
    if not recovered_key:
        recovered_key = parse_api_key(_extract_labeled_key(_clean_paste(text)) or "")
    return parse_api_url(api_url), recovered_key
