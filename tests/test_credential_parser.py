"""Pasted API URL and key normalization for settings and provider clients."""

from __future__ import annotations

import unittest

from providers.credential_parser import parse_api_key, parse_api_url, parse_provider_settings
from providers.image.hermes_client import HermesClient
from providers.video.seedance_provider import SeedanceClient


class CredentialParserTests(unittest.TestCase):
    def test_parse_api_key_strips_bearer_quotes_and_invisible_characters(self) -> None:
        cases = (
            ("Bearer sk-live-1", "sk-live-1"),
            ("bearer  sk-live-1", "sk-live-1"),
            ('"sk-live-1"', "sk-live-1"),
            ("\ufeffsk-live-1\u200b", "sk-live-1"),
            ("API_KEY=sk-live-1", "sk-live-1"),
            ("Authorization: Bearer sk-live-1", "sk-live-1"),
            ("Bearer Bearer sk-live-1", "sk-live-1"),
            ("sk-\nlive-1", "sk-live-1"),
            ("   ", ""),
            (None, ""),
        )
        for raw, expected in cases:
            with self.subTest(raw=raw):
                self.assertEqual(parse_api_key(raw), expected)

    def test_parse_api_url_recovers_root_from_console_and_resource_pastes(self) -> None:
        cases = (
            ("https://api.example.com/v1/", "https://api.example.com/v1"),
            ("https://api.example.com/v1/images/generations", "https://api.example.com/v1"),
            ("https://api.example.com/v1/chat/completions", "https://api.example.com/v1"),
            ("https://api.example.com/v1/models", "https://api.example.com/v1"),
            ("https://generativelanguage.googleapis.com/v1beta/models/veo-fast:predictLongRunning", "https://generativelanguage.googleapis.com/v1beta"),
            ("https://api.evolink.ai/v1/videos/generations", "https://api.evolink.ai"),
            ("https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks", "https://ark.cn-beijing.volces.com/api/v3"),
            ("api.example.com/v1", "https://api.example.com/v1"),
            ('  "https://api.example.com/v1"  ', "https://api.example.com/v1"),
            ("https://api.example.com/v1?api_key=leaked#fragment", "https://api.example.com/v1"),
            ("https://user:pass@api.example.com/v1", "https://api.example.com/v1"),
            (
                "curl https://api.example.com/v1/images/generations -H 'Authorization: Bearer sk-live-1'",
                "https://api.example.com/v1",
            ),
        )
        for raw, expected in cases:
            with self.subTest(raw=raw):
                self.assertEqual(parse_api_url(raw), expected)

    def test_parse_api_url_rejects_non_http_and_non_credential_query(self) -> None:
        for raw in ("ftp://api.example.com/v1", "https://api.example.com/v1?foo=1", ""):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_api_url(raw)

    def test_parse_provider_settings_recovers_key_hidden_in_url_field(self) -> None:
        url, key = parse_provider_settings(
            "https://api.example.com/v1/images/generations?api_key=sk-from-query",
            "",
        )
        self.assertEqual(url, "https://api.example.com/v1")
        self.assertEqual(key, "sk-from-query")

        url, key = parse_provider_settings(
            "API URL: https://api.example.com/v1\nAPI Key: sk-from-label",
            None,
        )
        self.assertEqual(url, "https://api.example.com/v1")
        self.assertEqual(key, "sk-from-label")

    def test_hermes_client_does_not_send_double_bearer(self) -> None:
        seen = {}

        def transport(method, url, headers, body, timeout):
            seen["authorization"] = headers.get("Authorization")
            seen["url"] = url
            return 200, {"Content-Type": "application/json"}, b'{"data":[]}'

        client = HermesClient(
            api_url="https://hermes.example/v1/images/generations",
            api_key="Bearer secret-key",
            transport=transport,
        )
        client.test_connection()
        self.assertEqual(seen["authorization"], "Bearer secret-key")
        self.assertEqual(seen["url"], "https://hermes.example/v1/models")

    def test_seedance_client_normalizes_pasted_resource_urls(self) -> None:
        ark = SeedanceClient(
            api_key="ark-key",
            api_url="curl https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks -H 'Authorization: Bearer ark-key'",
            text_model="seedance-2.5-text-to-video",
            transport=lambda *args: (_ for _ in ()).throw(AssertionError("transport must not run")),
        )
        self.assertTrue(ark.uses_ark)
        self.assertEqual(ark.api_url, "https://ark.cn-beijing.volces.com/api/v3")

        evolink = SeedanceClient(
            api_key="test-seedance-key",
            api_url="https://api.evolink.ai/v1/videos/generations",
            transport=lambda *args: (_ for _ in ()).throw(AssertionError("transport must not run")),
        )
        self.assertFalse(evolink.uses_ark)
        self.assertEqual(evolink.api_url, "https://api.evolink.ai")
        self.assertEqual(evolink.submit_path, "/v1/videos/generations")


if __name__ == "__main__":
    unittest.main()
