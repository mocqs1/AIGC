import json
import tempfile
import unittest

from harness.model_outfit_swap import (
    ModelOutfitSwapHarness,
    ModelOutfitSwapHarnessError,
    build_outfit_request,
    validate_outfit_selection,
)


class FakeApi:
    def __init__(self):
        self.calls = []

    def __call__(self, method, path, payload):
        self.calls.append((method, path, payload))
        if path == "/api/assets":
            return {
                "assets": [
                    {"id": "images/model.png", "media_type": "image"},
                    {"id": "images/outfit.png", "media_type": "image"},
                    {"id": "images/detail.png", "media_type": "image"},
                ]
            }
        if path == "/api/generations":
            return {
                "job_id": "job-001",
                "status": "queued",
                "phase": r"C:\\private\\provider-output.png",
                "model_name": "Bearer topsecret",
                "provider": "Authorization Bearer topsecret",
                "outputs": [
                    "/api/assets/images/result.png",
                    "C:/private/provider-output.png",
                    "/api/assets/../../secret.png?token=secret",
                ],
                "raw_provider_response": {"api_key": "secret"},
            }
        raise AssertionError(path)


class ModelOutfitSwapHarnessTests(unittest.TestCase):
    def test_first_reference_is_model_and_later_references_keep_order(self):
        selection = validate_outfit_selection(
            [
                "images/model.png",
                {"asset_id": "images/outfit.png", "role": "outfit"},
                "images/detail.png",
            ]
        )
        self.assertEqual(selection["model_asset_id"], "images/model.png")
        self.assertEqual(selection["outfit_asset_ids"], ["images/outfit.png", "images/detail.png"])
        self.assertEqual(selection["roles"], ["model", "outfit", "outfit"])

    def test_rejects_wrong_role_duplicate_video_path_and_short_selection(self):
        cases = [
            ([{"asset_id": "images/model.png", "role": "outfit"}, "images/outfit.png"], "role"),
            (["images/model.png", "images/model.png"], "distinct"),
            (["images/model.png", "videos/outfit.mp4"], "image"),
            (["images/model.png"], "2 to 10"),
            (["C:/private/model.png", "images/outfit.png"], "managed"),
        ]
        for references, message in cases:
            with self.subTest(references=references), self.assertRaisesRegex(ModelOutfitSwapHarnessError, message):
                validate_outfit_selection(references)

    def test_build_request_contains_only_opaque_ids(self):
        payload, selection = build_outfit_request(
            ["images/model.png", "images/outfit.png"],
            {"product": "black seamless shapewear", "scene": "clean studio"},
            provider="hermes",
        )
        self.assertEqual(payload["mode"], "model_outfit_swap")
        self.assertEqual(payload["request"], {})
        self.assertEqual(
            [item["value"] for item in payload["reference_images"]],
            selection["input_order"],
        )
        self.assertNotIn("C:\\", json.dumps(payload))
        self.assertNotIn("api_key", json.dumps(payload).lower())

    def test_build_request_normalizes_legacy_alias_and_accepts_seedream(self):
        for provider in ("hermes_volcano", "liblib"):
            with self.subTest(provider=provider):
                payload, _ = build_outfit_request(
                    ["images/model.png", "images/outfit.png"],
                    provider=provider,
                )
                self.assertEqual(payload["provider"], "hermes_volcano")

    def test_build_request_rejects_unknown_provider(self):
        with self.assertRaisesRegex(ModelOutfitSwapHarnessError, "Hermes and Hermes Volcano"):
            build_outfit_request(
                ["images/model.png", "images/outfit.png"],
                provider="unknown",
            )

    def test_build_request_reuses_runtime_prompt_safety(self):
        for prompt in (
            "Change the model identity and background",
            "Reshape the body and alter the pose",
            "make a nude minor image",
            "reduce pain with this garment",
            "Change clothing and background",
            "Change only the clothing and alter identity",
            "Edit garment and remove background",
            "Replace the outfit and change the face",
            "Change clothing, then change identity and background",
        ):
            with self.subTest(prompt=prompt), self.assertRaisesRegex(ModelOutfitSwapHarnessError, "request"):
                build_outfit_request(["images/model.png", "images/outfit.png"], {"prompt": prompt})

    def test_preview_checks_catalog_and_submit_redacts_response(self):
        fake = FakeApi()
        harness = ModelOutfitSwapHarness(transport=fake)
        preview = harness.preview(
            ["images/model.png", "images/outfit.png", "images/detail.png"],
            {"prompt": "commercial catalog image"},
        )
        self.assertEqual(preview["input_order"], ["images/model.png", "images/outfit.png", "images/detail.png"])
        self.assertEqual(preview["model_asset_id"], "images/model.png")
        self.assertEqual(preview["outfit_asset_ids"], ["images/outfit.png", "images/detail.png"])
        self.assertEqual(preview["manifest"]["reference_roles"], ["model", "outfit", "outfit"])
        self.assertTrue(preview["manual_review"])
        self.assertIn("color", " ".join(preview["manual_review"]).lower())
        fidelity = preview["garment_fidelity"]
        self.assertEqual(fidelity["standard"], "exact_visual_match")
        self.assertEqual(fidelity["target"], "same_selected_garment")
        self.assertEqual(fidelity["verification_status"], "pending_manual_review")
        self.assertTrue(fidelity["requires_visual_review"])
        self.assertFalse(fidelity["automatic_guarantee"])
        self.assertGreaterEqual(len(fidelity["dimensions"]), 7)
        dimensions = " ".join(fidelity["dimensions"]).lower()
        for detail in ("weave", "thickness", "stitch", "zipper", "pattern", "invented"):
            self.assertIn(detail, dimensions)
        self.assertEqual(preview["manifest"]["garment_fidelity"], fidelity)

        submitted = harness.submit(["images/model.png", "images/outfit.png"], {"product": "coat"})
        self.assertEqual(submitted["job"], {
            "job_id": "job-001",
            "status": "queued",
            "outputs": ["/api/assets/images/result.png"],
        })
        self.assertEqual(submitted["manifest"]["model_asset_id"], "images/model.png")
        self.assertEqual(submitted["garment_fidelity"], fidelity)
        encoded = json.dumps(submitted, ensure_ascii=False)
        self.assertNotIn("private", encoded.lower())
        self.assertNotIn("secret", encoded.lower())
        self.assertEqual(fake.calls[-1][1], "/api/generations")
        self.assertEqual(fake.calls[-1][2]["reference_images"][0]["value"], "images/model.png")

    def test_catalog_video_is_rejected_before_submit(self):
        class VideoCatalog(FakeApi):
            def __call__(self, method, path, payload):
                if path == "/api/assets":
                    return {"assets": [
                        {"id": "images/model.png", "media_type": "image"},
                        {"id": "images/outfit.png", "media_type": "video"},
                    ]}
                return super().__call__(method, path, payload)

        fake = VideoCatalog()
        harness = ModelOutfitSwapHarness(transport=fake)
        with self.assertRaisesRegex(ModelOutfitSwapHarnessError, "image"):
            harness.preview(["images/model.png", "images/outfit.png"])
        self.assertFalse(any(path == "/api/generations" for _, path, _ in fake.calls))


if __name__ == "__main__":
    unittest.main()
