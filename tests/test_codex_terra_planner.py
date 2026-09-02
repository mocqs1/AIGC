"""Tests for the server-only Codex Terra planning adapter."""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request

from providers.mix.codex_terra_planner import CodexTerraPlanner, TerraPlannerError


class CodexTerraPlannerTests(unittest.TestCase):
    def test_openai_compatible_response_is_parsed_without_exposing_key(self) -> None:
        seen = {}

        def transport(request):
            seen["authorization"] = request.get_header("Authorization")
            seen["body"] = json.loads(request.data.decode("utf-8"))
            content = "```json\n" + json.dumps({"clips": [{"asset_id": "images/a.png"}]}) + "\n```"
            return json.dumps({"choices": [{"message": {"content": content}}]}).encode("utf-8")

        planner = CodexTerraPlanner(
            api_url="https://planner.example/v1/chat/completions",
            api_key="test-key",
            proxy_url="",
            pinned_ip="",
            transport=transport,
        )
        result = planner.plan("make a plan")

        self.assertEqual(result, {"clips": [{"asset_id": "images/a.png"}]})
        self.assertEqual(seen["authorization"], "Bearer test-key")
        self.assertEqual(seen["body"]["model"], "gpt-5.6-terra")

    def test_default_transport_uses_no_redirect_opener(self) -> None:
        planner = CodexTerraPlanner(
            api_url="https://planner.example/v1",
            api_key="test-key",
            proxy_url="",
            pinned_ip="",
        )
        with patch("providers.mix.codex_terra_planner.safe_urlopen", side_effect=HTTPError("https://planner.example/v1", 302, "redirect blocked", {}, None)) as opener:
            with self.assertRaises(HTTPError):
                planner._request(Request("https://planner.example/v1"))
        opener.assert_called_once_with(
            unittest.mock.ANY,
            timeout=45,
            proxy_url="",
            pinned_ip="",
        )

    def test_completion_url_normalizes_openai_root_and_preserves_full_endpoint(self) -> None:
        cases = (
            ("https://planner.example/v1", "https://planner.example/v1/chat/completions"),
            ("https://planner.example/v1/", "https://planner.example/v1/chat/completions"),
            ("https://planner.example/v1/chat/completions", "https://planner.example/v1/chat/completions"),
            ("https://planner.example/chat/completions", "https://planner.example/chat/completions"),
        )
        for api_url, expected in cases:
            seen = {}

            def transport(request):
                seen["url"] = request.full_url
                return b'{"choices":[{"message":{"content":"{}"}}]}'

            with self.subTest(api_url=api_url):
                planner = CodexTerraPlanner(
                    api_url=api_url,
                    api_key="test-key",
                    proxy_url="",
                    pinned_ip="",
                    transport=transport,
                )
                self.assertEqual(planner.plan("make a plan"), {})
                self.assertEqual(seen["url"], expected)

    def test_default_transport_forwards_explicit_proxy_and_pinned_ip(self) -> None:
        planner = CodexTerraPlanner(
            api_url="https://planner.example/v1",
            api_key="test-key",
            proxy_url="http://127.0.0.1:8080",
            pinned_ip="93.184.216.34",
        )
        response = unittest.mock.MagicMock()
        response.__enter__.return_value.read.return_value = b"{}"
        with patch(
            "providers.mix.codex_terra_planner.safe_urlopen",
            return_value=response,
        ) as opener:
            self.assertEqual(planner._request(Request("https://planner.example/v1")), b"{}")
        opener.assert_called_once_with(
            unittest.mock.ANY,
            timeout=45,
            proxy_url="http://127.0.0.1:8080",
            pinned_ip="93.184.216.34",
        )

    def test_model_catalog_uses_openai_root_for_completion_endpoint(self) -> None:
        seen = {}

        def transport(request):
            seen["url"] = request.full_url
            seen["authorization"] = request.get_header("Authorization")
            return b'{"data":[{"id":"gpt-5.6-terra"}]}'

        planner = CodexTerraPlanner(
            api_url="https://planner.example/v1/chat/completions",
            api_key="test-key",
            proxy_url="",
            pinned_ip="",
            transport=transport,
        )

        self.assertEqual(planner.list_models(), {"data": [{"id": "gpt-5.6-terra"}]})
        self.assertEqual(seen["url"], "https://planner.example/v1/models")
        self.assertEqual(seen["authorization"], "Bearer test-key")

    def test_timeout_and_malformed_json_are_normalized(self) -> None:
        timeout = CodexTerraPlanner(api_url="https://planner.example/v1/chat/completions", api_key="test-key", proxy_url="", pinned_ip="", transport=lambda _request: (_ for _ in ()).throw(TimeoutError("provider-secret")))
        malformed = CodexTerraPlanner(api_url="https://planner.example/v1/chat/completions", api_key="test-key", proxy_url="", pinned_ip="", transport=lambda _request: b'{"choices":[}')

        with self.assertRaises(TerraPlannerError) as timeout_error:
            timeout.plan("make a plan")
        with self.assertRaises(TerraPlannerError) as malformed_error:
            malformed.plan("make a plan")

        self.assertNotIn("provider-secret", str(timeout_error.exception))
        self.assertNotIn("provider-secret", str(malformed_error.exception))

    def test_default_transport_rejects_private_probe_target(self) -> None:
        planner = CodexTerraPlanner(api_url="https://127.0.0.1/v1", api_key="key", proxy_url="", pinned_ip="")
        with self.assertRaises(TerraPlannerError):
            planner.test_connection()
