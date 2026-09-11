import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from skills.tiktok_clothing_main_image.runtime import (
    IMMUTABLE_PRODUCT_CONTRACT,
    SOURCE_LOCK,
    TikTokClothingRequestError,
    build_prompt,
    generate_image,
)


class FakeClient:
    def __init__(self):
        self.submitted = []

    def submit_generation(self, prompt, **options):
        self.submitted.append((prompt, options))
        return {"task_id": "tiktok-clothing-task"}

    def query_task(self, task_id):
        return {"status": "completed"}

    def get_result(self, task_id):
        return b"tiktok-clothing-image"


class TikTokClothingMainImageTests(unittest.TestCase):
    def test_generates_master_first_with_ordered_detail_images(self):
        client = FakeClient()
        with tempfile.TemporaryDirectory() as output_dir:
            manifest = generate_image(
                {
                    "purpose": "shop_listing",
                    "style": "studio_detail",
                    "scene": "neutral studio",
                    "claims": "78% nylon, 22% elastane",
                },
                product_images=["master.png", "fabric.png", "closure.png"],
                client=client,
                output_dir=output_dir,
            )
            self.assertEqual(manifest["skill"], "tiktok-clothing-main-image")
            self.assertEqual(manifest["workflow"], "tiktok_clothing_image")
            self.assertEqual(manifest["provider"], "hermes")
            self.assertEqual(manifest["purpose"], "shop_listing")
            self.assertEqual(manifest["aspect_ratio"], "9:16")
            self.assertEqual(manifest["source_roles"], ["product_master", "same_product_detail_or_alternate_view", "same_product_detail_or_alternate_view"])
            self.assertTrue(Path(manifest["outputs"][0]).is_file())
            self.assertTrue(manifest["quality"]["passed"])
            self.assertEqual(manifest["quality"]["policy_status"], "unreviewed")
        options = client.submitted[0][1]
        self.assertEqual(options["image"], "master.png")
        self.assertEqual(options["references"], ["fabric.png", "closure.png"])
        self.assertEqual(options["aspect_ratio"], "portrait")
        self.assertIn(SOURCE_LOCK, client.submitted[0][0])
        self.assertIn(IMMUTABLE_PRODUCT_CONTRACT, client.submitted[0][0])
        self.assertIn("78% nylon, 22% elastane", client.submitted[0][0])

    def test_prompt_defaults_to_source_locked_vertical_listing(self):
        prompt = build_prompt({"purpose": "video_cover", "style": "creator_ugc"})
        self.assertTrue(prompt.startswith(SOURCE_LOCK))
        self.assertIn("FORMAT: 9:16", prompt)
        self.assertIn("complete garment centered", prompt)
        self.assertIn("do not add prices", prompt.lower())

    def test_prompt_supports_market_locale_and_adult_presentation_contract(self):
        prompt = build_prompt({
            "purpose": "ugc_variant",
            "style": "creator_ugc",
            "market": "GB",
            "locale": "en-GB",
            "presentation_mode": "adult_model_fully_covered",
        })
        self.assertIn("MARKET: GB", prompt)
        self.assertIn("LOCALE: en-GB", prompt)
        self.assertIn("unmistakable adult", prompt)

    def test_rejects_invalid_presentation_and_locale(self):
        with self.assertRaises(TikTokClothingRequestError):
            build_prompt({"presentation_mode": "model"})
        with self.assertRaises(TikTokClothingRequestError):
            build_prompt({"locale": "english"})

    def test_rejects_invalid_purpose_style_ratio_and_reference_count(self):
        with self.assertRaises(TikTokClothingRequestError):
            build_prompt({"purpose": "ad_campaign"})
        with self.assertRaises(TikTokClothingRequestError):
            build_prompt({"style": "editorial"})
        with self.assertRaises(TikTokClothingRequestError):
            build_prompt({"aspect_ratio": "4:3"})
        with self.assertRaises(TikTokClothingRequestError):
            generate_image({}, product_images=[] , client=FakeClient())
        with self.assertRaises(TikTokClothingRequestError):
            generate_image({}, product_images=[f"image-{index}.png" for index in range(11)], client=FakeClient())

    def test_rejects_unsafe_or_garment_mutation_briefs_before_provider(self):
        client = FakeClient()
        for brief in ("Recolor the garment blue", "nude model", "Add a fake five-star review", "doctor-approved slimming"):
            with self.subTest(brief=brief), self.assertRaises(TikTokClothingRequestError):
                generate_image({"prompt": brief}, product_images=["master.png"], client=client)
        self.assertEqual(client.submitted, [])

    def test_accepts_public_https_image_with_extension_without_query(self):
        client = FakeClient()
        with patch("skills.tiktok_clothing_main_image.runtime.engine_generate_image") as engine:
            output = Path(tempfile.gettempdir()) / "tiktok-clothing-test.png"
            output.write_bytes(b"result")
            engine.return_value = str(output)
            manifest = generate_image({}, product_images=["https://cdn.example.com/master.png", "https://cdn.example.com/detail.webp"], client=client)
        self.assertEqual(manifest["source_count"], 2)

    def test_hermes_volcano_and_liblib_alias_are_accepted(self):
        client = FakeClient()
        with tempfile.TemporaryDirectory() as temporary:
            master = Path(temporary) / "master.png"
            master.write_bytes(b"master")
            for provider, expected in (("hermes_volcano", "hermes_volcano"), ("liblib", "hermes_volcano")):
                with self.subTest(provider=provider):
                    client.submitted.clear()
                    manifest = generate_image(
                        {"purpose": "shop_listing", "style": "studio_detail"},
                        product_images=[str(master)],
                        provider=provider,
                        client=client,
                        output_dir=temporary,
                    )
                    self.assertEqual(manifest["provider"], expected)

    def test_non_image_provider_is_rejected_before_generation(self):
        with tempfile.TemporaryDirectory() as temporary:
            master = Path(temporary) / "master.png"
            master.write_bytes(b"master")
            with self.assertRaisesRegex(TikTokClothingRequestError, "Hermes"):
                generate_image(
                    {"purpose": "shop_listing", "style": "studio_detail"},
                    product_images=[str(master)],
                    provider="veo",
                    client=FakeClient(),
                    output_dir=temporary,
                )

    def test_api_contract_requires_master_and_defaults_to_configured_provider(self):
        import api_server

        with patch(
            "api_server._provider_available",
            side_effect=lambda name: (
                str(name).strip().lower() == "hermes_volcano",
                None if str(name).strip().lower() == "hermes_volcano" else "未完成本地 API Key 配置",
            ),
        ):
            payload = api_server.GenerationRequest(
                mode="tiktok_clothing_image",
                request={"purpose": "shop_listing", "style": "studio_detail"},
                reference_images=[api_server.ReferenceImage(kind="url", value="https://cdn.example.com/master.png")],
            )
        self.assertEqual(payload.provider, "hermes_volcano")
        self.assertEqual(api_server._provider_for_job(payload), "hermes_volcano")


        with self.assertRaisesRegex(ValueError, "one product master"):
            api_server.GenerationRequest(mode="tiktok_clothing_image", request={}, reference_images=[])
        with self.assertRaisesRegex(ValueError, "at most nine"):
            api_server.GenerationRequest(
                mode="tiktok_clothing_image",
                request={},
                reference_images=[api_server.ReferenceImage(kind="url", value=f"https://cdn.example.com/{index}.png") for index in range(11)],
            )

    def test_api_job_forwards_master_before_details(self):
        import api_server

        with tempfile.TemporaryDirectory() as output_dir:
            root = Path(output_dir)
            (root / "images").mkdir()
            master = root / "images" / "master.png"
            detail = root / "images" / "detail.png"
            result = root / "tiktok_clothing" / "result.png"
            result.parent.mkdir()
            master.write_bytes(b"master")
            detail.write_bytes(b"detail")
            result.write_bytes(b"result")
            with patch.object(api_server, "resolve_asset", side_effect=lambda value: str(root / value)):
                payload = api_server.GenerationRequest(
                    mode="tiktok_clothing_image",
                    request={"purpose": "video_cover", "style": "studio_detail"},
                    reference_images=[
                        api_server.ReferenceImage(kind="asset", value="images/master.png"),
                        api_server.ReferenceImage(kind="asset", value="images/detail.png"),
                    ],
                )
            job = api_server.GenerationJob(id="tiktok-clothing-job", payload=payload)
            api_server.JOBS[job.id] = job
            with patch.object(api_server, "OUTPUTS_DIR", root), patch.object(api_server, "OUTPUT_ROOTS", (root / "images", root / "tiktok_clothing")), patch.object(api_server, "_hermes_client", return_value=object()), patch("api_server.generate_tiktok_clothing_image") as generate_skill:
                generate_skill.return_value = {"prompt": "prompt", "outputs": [str(result)], "quality": {"passed": True}}
                with patch.object(api_server, "resolve_asset", side_effect=lambda value: str(root / value)):
                    api_server._run_job(job.id)
            kwargs = generate_skill.call_args.kwargs
            self.assertEqual(kwargs["product_images"], [str(master), str(detail)])
            self.assertEqual(kwargs["provider"], "hermes")
            self.assertEqual(job.status, "succeeded")


if __name__ == "__main__":
    unittest.main()
