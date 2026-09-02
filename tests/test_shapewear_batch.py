import copy
import tempfile
import unittest
from pathlib import Path

from harness.intelligent_editing.runner import IntelligentEditingHarness, LocalApiClient
from harness.intelligent_editing.shapewear_batch import ShapewearBatchHarness
from skills.shapewear_video_generator.batch import (
    BATCH_VERSION,
    ShapewearBatchError,
    batch_manifest,
    plan_batch,
    validate_batch,
)


def request(**overrides):
    value = {
        "batch_id": "campaign-001",
        "product": "black high-waist shapewear",
        "platform": "tiktok",
        "market": "US",
        "locale": "en-US",
        "objective": "show fit, seamless edges, and care details",
        "selected_assets": [
            {"asset_id": "images/product.png", "media_type": "image", "duration_ms": 3000},
            {"asset_id": "videos/fit.mp4", "media_type": "video", "duration_ms": 7000},
        ],
        "variant_count": 4,
        "claims_reviewed": True,
    }
    value.update(overrides)
    return value


class ShapewearBatchTests(unittest.TestCase):
    def test_planning_is_deterministic_and_contains_four_beats(self):
        first = plan_batch(request())
        second = plan_batch(request())
        self.assertEqual(first, second)
        self.assertEqual(first["version"], BATCH_VERSION)
        self.assertEqual([beat["type"] for beat in first["variants"][0]["beats"]], ["hook", "demonstration", "detail", "cta"])
        self.assertTrue(all(item["variant_id"].startswith("swv-") for item in first["variants"]))
        self.assertTrue(all(item["editing_request"]["selected_assets"][0]["asset_id"] == "images/product.png" for item in first["variants"]))

    def test_platform_presets_are_bounded_and_recorded(self):
        for platform in ("tiktok", "reels", "shorts"):
            with self.subTest(platform=platform):
                result = plan_batch(request(platform=platform, variant_count=1))
                self.assertEqual(result["platform"], platform)
                variant = result["variants"][0]
                self.assertEqual(variant["aspect_ratio"], "portrait")
                self.assertEqual(sum(beat["duration_ms"] for beat in variant["beats"]), variant["duration_ms"])
                self.assertIn("safe_zone", variant)

    def test_rejects_paths_urls_secrets_and_invalid_asset_shapes(self):
        for assets in (
            [{"asset_id": "C:/private/video.mp4", "media_type": "video"}, {"asset_id": "images/a.png", "media_type": "image"}],
            [{"asset_id": "videos/a.mp4?token=x", "media_type": "video"}, {"asset_id": "images/a.png", "media_type": "image"}],
            [{"asset_id": "videos/a.mp4", "media_type": "video", "api_key": "secret"}, {"asset_id": "images/a.png", "media_type": "image"}],
        ):
            with self.subTest(assets=assets), self.assertRaises(ShapewearBatchError):
                plan_batch(request(selected_assets=assets))

    def test_unreviewed_claims_remain_manual_review(self):
        result = plan_batch(request(claims_reviewed=False, variant_count=1))
        quality = result["variants"][0]["quality"]
        self.assertFalse(quality["claim_reviewed"])
        self.assertTrue(any("声明" in item for item in quality["manual_review"]))
        with self.assertRaises(ShapewearBatchError):
            plan_batch(request(claims_reviewed="false"))

    def test_validation_rejects_tampered_variant_and_manifest_is_safe(self):
        result = plan_batch(request(variant_count=1))
        tampered = copy.deepcopy(result)
        tampered["variants"][0]["editing_request"]["selected_assets"][0]["asset_id"] = "C:/secret.mp4"
        with self.assertRaises(ShapewearBatchError):
            validate_batch(tampered)
        manifest = batch_manifest(result, rendered={result["variants"][0]["variant_id"]: {"output": "/api/assets/shapewear/video.mp4", "path": "C:/secret.mp4"}})
        self.assertEqual(manifest["version"], BATCH_VERSION)
        encoded = str(manifest)
        self.assertIn("/api/assets/shapewear/video.mp4", encoded)
        self.assertNotIn("C:/secret.mp4", encoded)

    def test_validation_rejects_unsafe_copy_and_unselected_beat_asset(self):
        result = plan_batch(request(variant_count=1))
        for unsafe_copy in (
            "guaranteed weight loss today",
            "doctor-approved pain relief",
            "fix your body",
            "fixing your body",
            "reshape your body",
            "reduce pain",
            "pain reduction",
            "pain-free fit",
            "body transformation",
            "sculpting your body",
            "burn fat",
            "No. 1 shapewear",
            "clinical fit",
            "reduce pain",
            "effective shaping",
            "seductive styling",
            "teen shapewear campaign",
            "nude fetish styling",
            "official SKIMS endorsement",
            "clinically proven results",
            "FDA approved slimming",
            "doctor recommended posture correction",
            "slims your waist",
            "compression improves posture",
            "waist trainer melts fat",
            "best shapewear ever",
            "number one shapewear",
            "100% effective",
            "fake customer testimonial",
            "sexy pose",
            "fat rolls",
            "SKIMS",
        ):
            with self.subTest(unsafe_copy=unsafe_copy):
                unsafe = copy.deepcopy(result)
                unsafe["variants"][0]["creative"]["cta"] = unsafe_copy
                with self.assertRaises(ShapewearBatchError):
                    validate_batch(unsafe)
        unselected = copy.deepcopy(result)
        unselected["variants"][0]["beats"][0]["asset_id"] = "images/other.png"
        with self.assertRaises(ShapewearBatchError):
            validate_batch(unselected)

    def test_validation_rejects_malformed_beats_and_request_mismatch(self):
        result = plan_batch(request(variant_count=1))
        malformed = copy.deepcopy(result)
        malformed["variants"][0]["beats"][0] = None
        with self.assertRaises(ShapewearBatchError):
            validate_batch(malformed)
        mismatch = copy.deepcopy(result)
        mismatch["variants"][0]["editing_request"]["target_duration_ms"] += 1000
        with self.assertRaises(ShapewearBatchError):
            validate_batch(mismatch)
        mismatch_assets = copy.deepcopy(result)
        mismatch_assets["selected_assets"][0]["duration_ms"] = 4000
        with self.assertRaises(ShapewearBatchError):
            validate_batch(mismatch_assets)

    def test_batch_harness_delegates_preview_confirm_render_and_status(self):
        class FakeApi:
            def __init__(self):
                self.calls = []
                self.mix_count = 0

            def __call__(self, method, path, payload):
                self.calls.append((method, path, payload))
                if path == "/api/assets":
                    return {"assets": [
                        {"id": "images/product.png", "media_type": "image", "size": 10},
                        {"id": "videos/fit.mp4", "media_type": "video", "size": 20},
                    ]}
                if path == "/api/mixes":
                    self.mix_count += 1
                    return {"mix_id": f"mix-{self.mix_count}", "status": "queued"}
                if path.startswith("/api/mixes/"):
                    return {"status": "succeeded", "output": "/api/assets/videos/mix.mp4"}
                raise AssertionError(path)

        batch = plan_batch(request(variant_count=2))
        api = FakeApi()
        with tempfile.TemporaryDirectory() as state_dir:
            editing = IntelligentEditingHarness(state_dir, LocalApiClient(transport=api))
            harness = ShapewearBatchHarness(editing)
            preview = harness.preview(batch)
            self.assertEqual(preview["variant_count"], 2)
            hashes = {item["variant_id"]: item["plan_hash"] for item in preview["variants"]}
            self.assertTrue(all(item["plan"]["clips"] for item in preview["variants"]))
            self.assertTrue(all(item["plan"]["transition_mode"] == "hard_cut" for item in preview["variants"]))
            self.assertTrue(all(isinstance(item["plan"]["warnings"], list) for item in preview["variants"]))
            confirmed = harness.confirm(batch, hashes)
            self.assertTrue(all(item["accepted_plan_hash"] == hashes[item["variant_id"]] for item in confirmed["variants"]))
            rendered = harness.render(batch, hashes)
            self.assertEqual(len([call for call in api.calls if call[1] == "/api/mixes"]), 2)
            self.assertTrue(all(item["mix_id"] for item in rendered["variants"]))
            status = harness.status(batch)
            self.assertTrue(all(item["status"] == "succeeded" for item in status["variants"]))

    def test_batch_harness_requires_one_hash_per_variant(self):
        batch = plan_batch(request(variant_count=1))
        with tempfile.TemporaryDirectory() as state_dir:
            harness = ShapewearBatchHarness(IntelligentEditingHarness(state_dir, LocalApiClient(transport=lambda *_: {"assets": []})))
            with self.assertRaisesRegex(Exception, "每个变体"):
                harness.confirm(batch, {})


if __name__ == "__main__":
    unittest.main()
