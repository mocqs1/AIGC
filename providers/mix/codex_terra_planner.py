"""Server-side adapter for an OpenAI-compatible Codex Terra planning endpoint.

The browser never calls this module and no credential is included in returned
plans.  A caller should always retain a deterministic local planning fallback.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request

from config import env_value
from providers.http_safety import safe_urlopen


class TerraPlannerError(RuntimeError):
    """Raised when the configured Terra endpoint cannot produce a plan."""


class CodexTerraPlanner:
    """Small OpenAI-compatible JSON planner adapter.

    ``api_url`` may be an OpenAI-compatible API root or a full completion
    endpoint so installations can use a gateway without browser-side changes.
    """

    def __init__(
        self,
        *,
        api_url: str,
        api_key: str,
        model: str = "gpt-5.6-terra",
        proxy_url: str | None = None,
        pinned_ip: str | None = None,
        transport: Callable[[Request], bytes] | None = None,
    ) -> None:
        self.api_url = api_url.strip()
        self.api_key = api_key.strip()
        self.model = model.strip() or "gpt-5.6-terra"
        self.proxy_url = (env_value("CODEX_TERRA_PROXY_URL", "") if proxy_url is None else proxy_url or "").strip()
        self.pinned_ip = (env_value("CODEX_TERRA_PINNED_IP", "") if pinned_ip is None else pinned_ip or "").strip()
        self.transport = transport

    def plan(self, prompt: str) -> dict[str, Any]:
        if not self.api_url or not self.api_key:
            raise TerraPlannerError("Codex Terra is not configured")
        payload = json.dumps(
            {
                "model": self.model,
                "temperature": 0,
                "response_format": {"type": "json_object"},
                "messages": [
                    {
                        "role": "system",
                        "content": "You are a video editor. Return only a complete JSON object with "
                        "version='aigc-mix-plan/v1', planner='codex_terra', objective, "
                        "target_duration_ms, clips, and warnings. Each clip has asset_id, start_ms, "
                        "end_ms or duration_ms, and transition. Use only supplied asset ids. "
                        "Transitions are hard_cut or fade.",
                    },
                    {"role": "user", "content": prompt},
                ],
            }, ensure_ascii=False).encode("utf-8")
        request = Request(
            self._completion_url(),
            data=payload,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"},
            method="POST",
        )
        try:
            raw = self.transport(request) if self.transport else self._request(request)
            response = json.loads(raw.decode("utf-8"))
            content = response["choices"][0]["message"]["content"]
            if isinstance(content, list):
                content = "".join(str(part.get("text", "")) for part in content if isinstance(part, Mapping))
            if not isinstance(content, str):
                raise ValueError("response content is not text")
            return self._parse_json(content)
        except (HTTPError, URLError, OSError, KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise TerraPlannerError("Codex Terra planning request failed") from error

    def _request(self, request: Request) -> bytes:
        with safe_urlopen(
            request,
            timeout=45,
            proxy_url=self.proxy_url,
            pinned_ip=self.pinned_ip,
        ) as response:
            return response.read()

    def test_connection(self) -> dict[str, Any]:
        return self.list_models()

    def list_models(self) -> dict[str, Any]:
        request = Request(
            self._models_url(),
            headers={"Accept": "application/json", "Authorization": f"Bearer {self.api_key}"},
            method="GET",
        )
        try:
            raw = self.transport(request) if self.transport else self._request(request)
            return json.loads(raw.decode("utf-8")) if raw else {}
        except Exception as error:
            raise TerraPlannerError("Codex Terra connection test failed") from error

    def _models_url(self) -> str:
        parts = urlsplit(self._completion_url())
        path = parts.path.rstrip("/")
        for suffix in ("/chat/completions", "/responses", "/completions"):
            if path.endswith(suffix):
                path = path[: -len(suffix)]
                break
        if not path.endswith("/models"):
            path += "/models"
        return urlunsplit((parts.scheme, parts.netloc, path, "", ""))

    def _completion_url(self) -> str:
        parts = urlsplit(self.api_url)
        path = parts.path.rstrip("/")
        if not path.endswith(("/chat/completions", "/responses", "/completions")):
            path += "/chat/completions"
        return urlunsplit((parts.scheme, parts.netloc, path, "", ""))

    @staticmethod
    def _parse_json(content: str) -> dict[str, Any]:
        cleaned = content.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[-1]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
        value = json.loads(cleaned.strip())
        if not isinstance(value, dict):
            raise ValueError("response is not an object")
        return value
