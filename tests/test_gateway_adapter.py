"""Gateway URL matching does not require a protocol template."""
from __future__ import annotations

import unittest
from unittest.mock import Mock

from providers.gateway_adapter import (
    GatewayCategoryMismatch,
    detect_gateway,
    match_protocol,
    suggested_slug,
)


class GatewayAdapterTests(unittest.TestCase):
    def test_ark_image_url_matches_liblib_protocol(self) -> None:
        self.assertEqual(
            match_protocol("https://ark.cn-beijing.volces.com/api/v3", category="image"),
            "image.liblib",
        )

    def test_ark_video_url_matches_seedance_protocol(self) -> None:
        self.assertEqual(
            match_protocol("https://ark.cn-beijing.volces.com/api/v3", category="video"),
            "video.seedance",
        )

    def test_stored_hermes_protocol_yields_to_ark_host(self) -> None:
        self.assertEqual(
            match_protocol(
                "https://ark.cn-beijing.volces.com/api/v3",
                category="image",
                stored_protocol="image.hermes",
            ),
            "image.liblib",
        )

    def test_detect_gateway_probes_matched_protocol(self) -> None:
        probe = Mock(return_value={"data": [{"id": "doubao-seedream-5-0-pro-260628", "name": "Seedream"}]})
        detection = detect_gateway(
            api_url="https://ark.cn-beijing.volces.com/api/v3",
            api_key="ark-secret",
            category="image",
            occupied_slugs=set(),
            probe=probe,
        )
        self.assertEqual(detection["protocol"], "image.liblib")
        self.assertEqual(detection["selected_model"], "doubao-seedream-5-0-pro-260628")
        self.assertEqual(detection["suggested_slug"], "ark")
        probe.assert_called_once()
        self.assertEqual(probe.call_args.args[0], "image.liblib")

    def test_detect_gateway_rejects_category_mismatch(self) -> None:
        with self.assertRaises(GatewayCategoryMismatch):
            detect_gateway(
                api_url="https://generativelanguage.googleapis.com/v1beta",
                api_key="veo-secret",
                category="image",
                probe=Mock(),
            )

    def test_suggested_slug_avoids_reserved_names(self) -> None:
        slug = suggested_slug(
            "https://aiapi.yicheng.bj.cn/v1",
            "image",
            {"hermes_gw"},
            "image.hermes",
        )
        self.assertEqual(slug, "hermes_gw_gw")


if __name__ == "__main__":
    unittest.main()
