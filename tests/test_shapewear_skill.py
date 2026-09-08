import json
import importlib.util
import importlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "skills" / "shapewear-video-generator"
QUALITY_MODULE_PATH = SKILL_ROOT / "quality_check.py"
QUALITY_SPEC = importlib.util.spec_from_file_location("shapewear_quality_check", QUALITY_MODULE_PATH)
assert QUALITY_SPEC is not None and QUALITY_SPEC.loader is not None
QUALITY_MODULE = importlib.util.module_from_spec(QUALITY_SPEC)
QUALITY_SPEC.loader.exec_module(QUALITY_MODULE)


class ShapewearSkillTests(unittest.TestCase):
    class FakeClient:
        def __init__(self, result):
            self.result = result
            self.submitted = []

        def submit_generation(self, prompt, **options):
            self.submitted.append((prompt, options))
            return {"task_id": "skill-task"}

        def query_task(self, task_id):
            return {"status": "completed"}

        def get_result(self, task_id):
            return self.result

    def test_registry_and_skill_metadata_are_present(self):
        registry = json.loads((ROOT / "skills" / "registry.json").read_text(encoding="utf-8"))
        entry = next(item for item in registry["skills"] if item["name"] == "shapewear-video-generator")
        self.assertEqual(entry["name"], "shapewear-video-generator")
        self.assertIn("shapewear", entry["triggers"])
        self.assertIn("shapewear product image", entry["triggers"])
        self.assertEqual(entry["image_provider"], "hermes")
        self.assertFalse(entry["image_provider_locked"])
        self.assertEqual(entry["image_providers"], ["hermes", "hermes_volcano"])
        self.assertIn("skills.shapewear_video_generator.runtime.generate_image", entry["entrypoints"]["image"])
        self.assertIn("lingerie advertisement", entry["qualification_rules"])
        for entrypoint in entry["entrypoints"].values():
            module_name, function_name = entrypoint.rsplit(".", 1)
            self.assertTrue(callable(getattr(importlib.import_module(module_name), function_name)))
        skill_text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        for trigger in ("塑身衣", "shapewear", "bodysuit", "waist trainer", "fashion commercial"):
            self.assertIn(trigger, skill_text)

    def test_workflows_are_valid_and_reference_existing_resources(self):
        for name, expected_type in (("shapewear_image.json", "image"), ("shapewear_video.json", "video")):
            workflow = json.loads((SKILL_ROOT / "workflows" / name).read_text(encoding="utf-8"))
            self.assertEqual(workflow["type"], expected_type)
            if expected_type == "image":
                self.assertEqual(workflow["provider"], "hermes")
                self.assertFalse(workflow["provider_locked"])
                self.assertEqual(workflow["providers"], ["hermes", "hermes_volcano"])
                self.assertEqual(workflow["provider_constraints"]["garment_fidelity_standard"], "exact_visual_match")
                self.assertEqual(workflow["provider_prompt_policies"]["hermes"], "gpt_image_2_source_locked_product_fidelity_v1")
                self.assertEqual(workflow["provider_constraints"]["model_families"]["hermes"], "gpt-image-2")
            for resource in workflow["resources"]:
                self.assertTrue((SKILL_ROOT / resource).is_file(), resource)

    def test_quality_check_distinguishes_valid_and_missing_artifacts(self):
        with tempfile.TemporaryDirectory() as output_dir:
            artifact = Path(output_dir) / "image_001.png"
            artifact.write_bytes(b"mock-image")
            result = QUALITY_MODULE.check_artifact(artifact, "image")
            self.assertTrue(result["passed"])
            self.assertTrue(result["manual_review"])

            missing = QUALITY_MODULE.check_artifact(Path(output_dir) / "video_001.mp4", "video")
            self.assertFalse(missing["passed"])

    def test_runtime_returns_skill_manifest_and_writes_prompt(self):
        from skills.shapewear_video_generator.runtime import generate_image

        client = self.FakeClient(b"skill-image")
        with tempfile.TemporaryDirectory() as output_dir:
            manifest = generate_image(
                {
                    "product": "black high-waist shapewear",
                    "color": "black",
                    "material": "seamless compression fabric",
                    "scene": "premium bedroom",
                    "style": "luxury fashion",
                },
                client=client,
                output_dir=output_dir,
            )
            self.assertEqual(manifest["skill"], "shapewear-video-generator")
            self.assertEqual(manifest["workflow"], "shapewear_image")
            self.assertTrue(Path(manifest["outputs"][0]).is_file())
            self.assertTrue((Path(output_dir) / "prompt.txt").is_file())
            self.assertTrue(manifest["quality"]["passed"])
            self.assertEqual(manifest["provider"], "hermes")
            self.assertEqual(manifest["garment_fidelity"]["standard"], "exact_visual_match")
            self.assertEqual(manifest["garment_fidelity"]["verification_status"], "pending_manual_review")

    def test_image_prompt_keeps_fidelity_contract_when_user_supplies_prompt(self):
        from skills.shapewear_video_generator.runtime import _build_prompt

        prompt = _build_prompt(
            {
                "prompt": "Use a warm studio and a three-quarter catalog angle.",
                "product": "black high-waist shapewear bodysuit",
                "material": "recycled nylon elastane warp-knit compression fabric",
            },
            "image",
        )
        lowered = prompt.lower()
        for term in ("immutable shapewear product contract", "weave or knit direction", "compression", "gusset/crotch", "flatlock", "laser-cut hem", "do not recolor", "do not guess"):
            self.assertIn(term, lowered)
        self.assertIn("user presentation brief", lowered)
        self.assertIn("warm studio", lowered)

    def test_image_style_id_switches_prompt_and_keeps_scene(self) -> None:
        from skills.shapewear_video_generator.runtime import _build_prompt

        ugc = _build_prompt(
            {
                "product": "black high-waist shapewear",
                "color": "black",
                "style_id": "tiktok_ugc",
                "scene": "bright apartment dressing area, full-length mirror",
                "style": "TikTok UGC try-on",
            },
            "image",
            provider="hermes",
        )
        detail = _build_prompt(
            {
                "product": "black high-waist shapewear",
                "color": "black",
                "style_id": "product_detail",
                "scene": "clean neutral studio, product fully visible and centered",
                "style": "commercial product still",
            },
            "image",
            provider="hermes",
        )
        self.assertIn("TikTok UGC try-on still", ugc)
        self.assertIn("adult woman fully wearing", ugc)
        self.assertIn("bright apartment dressing area", ugc)
        self.assertIn("do not convert UGC into a product-only packshot", ugc)
        self.assertNotIn("product-only or a fully covered headless mannequin", ugc)
        self.assertIn("product detail photograph", detail)
        self.assertIn("clean neutral studio", detail)
        self.assertNotIn("TikTok UGC try-on still", detail)

        campaign = _build_prompt(
            {
                "product": "black high-waist shapewear",
                "color": "black",
                "style_id": "fashion_campaign",
                "scene": "luxury fashion studio, full-body magazine lighting",
                "style": "high-end fashion campaign",
            },
            "image",
            provider="hermes",
        )
        self.assertIn("high-end fashion campaign still", campaign)
        self.assertIn("adult woman fully wearing", campaign)
        self.assertIn("luxury fashion studio", campaign)
        self.assertIn("do not convert it into a packshot", campaign)
        self.assertNotIn("TikTok UGC try-on still", campaign)
        self.assertNotIn("product-only or a fully covered headless mannequin", campaign)

    def test_video_style_id_maps_fashion_campaign_block(self) -> None:
        from skills.shapewear_video_generator.runtime import _build_prompt

        prompt = _build_prompt(
            {
                "product": "black high-waist shapewear",
                "color": "black",
                "style_id": "fashion_campaign",
                "scene": "luxury fashion studio",
                "style": "high-end fashion campaign",
                "material": "seamless compression fabric",
            },
            "video",
        )
        self.assertIn("high-end fashion campaign commercial", prompt)
        self.assertIn("adult woman fully wearing", prompt)
        self.assertNotIn("TikTok UGC shapewear try-on", prompt)
        self.assertIn("8-second 9:16 1080x1920 clip", prompt)
        self.assertNotIn("{duration_seconds}", prompt)
        self.assertNotIn("{aspect_ratio}", prompt)
        self.assertNotIn("{resolution}", prompt)

    def test_video_prompt_uses_requested_clip_duration_and_landscape_frame(self) -> None:
        from skills.shapewear_video_generator.runtime import _build_prompt

        prompt = _build_prompt(
            {
                "product": "black high-waist shapewear",
                "color": "black",
                "style_id": "fashion_campaign",
                "clip_id": "landscape_showcase",
                "duration_seconds": 8,
                "aspect_ratio": "16:9",
                "resolution": "1920x1080",
                "scene": "wide luxury studio",
                "style": "landscape fashion showcase",
                "material": "seamless compression fabric",
            },
            "video",
        )
        self.assertIn("8-second 16:9 1920x1080 clip", prompt)
        self.assertNotIn("1080x1920", prompt)

    def test_video_product_detail_uses_fabric_macro_block(self) -> None:
        from skills.shapewear_video_generator.runtime import _build_prompt

        prompt = _build_prompt(
            {
                "product": "black high-waist shapewear",
                "color": "black",
                "style_id": "product_detail",
                "clip_id": "fabric_macro",
                "scene": "clean neutral studio, fabric and seam close-up",
                "style": "fabric construction clip",
                "material": "seamless compression fabric",
            },
            "video",
        )
        self.assertIn("shapewear fabric and construction clip", prompt)
        self.assertIn("5-second 9:16 1080x1920 clip", prompt)
        self.assertNotIn("luxury shapewear fashion commercial clip", prompt)

    def test_hermes_gpt_image_prompt_is_source_first(self):
        from skills.shapewear_video_generator.runtime import (
            GPT_IMAGE_2_IMAGE_FIDELITY_CONTRACT,
            GPT_IMAGE_2_REFERENCE_BINDING,
            _build_prompt,
        )

        prompt = _build_prompt(
            {
                "prompt": "Use a dramatic editorial set and a fashion model.",
                "product": "selected main shapewear product",
                "_reference_attached": True,
            },
            "image",
            provider="hermes",
        )
        self.assertTrue(prompt.startswith(GPT_IMAGE_2_REFERENCE_BINDING))
        self.assertIn(GPT_IMAGE_2_IMAGE_FIDELITY_CONTRACT, prompt)
        self.assertLess(prompt.index("GPT-IMAGE-2 SOURCE LOCK"), prompt.index("USER PRESENTATION BRIEF"))
        self.assertLess(prompt.index("USER PRESENTATION BRIEF"), prompt.index("IMMUTABLE SHAPEWEAR PRODUCT CONTRACT"))
        self.assertIn("near-100% exact visual-match target", prompt.lower())

    def test_seedream_prompt_does_not_use_gpt_image_adapter(self):
        from skills.shapewear_video_generator.runtime import _build_prompt

        prompt = _build_prompt(
            {"product": "black shapewear", "_reference_attached": True},
            "image",
            provider="hermes_volcano",
        )
        self.assertNotIn("GPT-IMAGE-2 SOURCE LOCK", prompt)
        self.assertIn("IMMUTABLE SHAPEWEAR PRODUCT CONTRACT", prompt)
        self.assertIn("PRODUCT SOURCE BINDING", prompt)

    def test_image_accepts_second_image_provider_before_provider_call(self):
        from skills.shapewear_video_generator.runtime import ShapewearRequestError, generate_image

        with tempfile.TemporaryDirectory() as output_dir:
            client = self.FakeClient(b"second-provider-image")
            manifest = generate_image(
                {"product": "black shapewear", "provider": "hermes_volcano"},
                client=client,
                output_dir=output_dir,
            )
            self.assertEqual(manifest["provider"], "hermes_volcano")
            self.assertEqual(len(client.submitted), 1)

            legacy_client = self.FakeClient(b"legacy-provider-image")
            legacy_manifest = generate_image(
                {"product": "black shapewear", "provider": "liblib"},
                client=legacy_client,
                output_dir=output_dir,
            )
            self.assertEqual(legacy_manifest["provider"], "hermes_volcano")

    def test_image_rejects_garment_mutation_brief_before_provider_call(self):
        from skills.shapewear_video_generator.runtime import ShapewearRequestError, generate_image

        client = self.FakeClient(b"should-not-be-created")
        with self.assertRaisesRegex(ShapewearRequestError, "cannot be recolored"):
            generate_image({"prompt": "recolor the shapewear garment red and add lace"}, client=client)
        self.assertEqual(client.submitted, [])

    def test_image_forwards_ordered_references_and_marks_binding(self):
        from skills.shapewear_video_generator.runtime import generate_image

        client = self.FakeClient(b"skill-image")
        with tempfile.TemporaryDirectory() as output_dir:
            manifest = generate_image(
                {"product": "high-waist shapewear", "prompt": "neutral catalog view"},
                image="product-front.png",
                references=["product-back.png", "fabric-macro.png"],
                client=client,
                output_dir=output_dir,
            )
        self.assertEqual(
            client.submitted[0][1],
            {"image": "product-front.png", "references": ["product-back.png", "fabric-macro.png"]},
        )
        self.assertTrue(manifest["reference_bound"])
        self.assertEqual(manifest["reference_count"], 3)
        self.assertEqual(
            manifest["reference_roles"],
            ["product_master", "same_product_detail_or_alternate_view", "same_product_detail_or_alternate_view"],
        )
        self.assertIn("attached reference must remain the same product", manifest["prompt"].lower())

    def test_runtime_rejects_prohibited_request_before_provider_call(self):
        from skills.shapewear_video_generator.runtime import ShapewearRequestError, generate_video

        client = self.FakeClient(b"should-not-be-created")
        with self.assertRaises(ShapewearRequestError):
            generate_video("make a nude minor lingerie commercial", client=client)
        self.assertEqual(client.submitted, [])

    def test_runtime_video_manifest_forwards_keyframe(self):
        from skills.shapewear_video_generator.runtime import generate_video

        client = self.FakeClient(b"skill-video")
        with tempfile.TemporaryDirectory() as output_dir:
            manifest = generate_video(
                {
                    "product": "beige bodysuit",
                    "scene": "premium bedroom",
                    "style": "minimal editorial",
                    "type": "video",
                },
                image="keyframe.png",
                client=client,
                output_dir=output_dir,
            )
            self.assertEqual(manifest["workflow"], "shapewear_video")
            self.assertTrue(manifest["quality"]["passed"])
        self.assertEqual(client.submitted[0][1], {"image": "keyframe.png"})

    def test_runtime_video_resume_skips_keyframe_generation(self):
        from skills.shapewear_video_generator.runtime import generate_video

        client = self.FakeClient(b"skill-video")
        with tempfile.TemporaryDirectory() as output_dir:
            with patch("skills.shapewear_video_generator.runtime.generate_image") as generate_image:
                manifest = generate_video(
                    {
                        "product": "beige bodysuit",
                        "scene": "premium bedroom",
                        "style": "minimal editorial",
                        "type": "video",
                    },
                    client=client,
                    output_dir=output_dir,
                    resume_task_id="persisted-video",
                )
        generate_image.assert_not_called()
        self.assertEqual(client.submitted, [])
        self.assertEqual(manifest["workflow"], "shapewear_video")

    def test_runtime_video_resume_does_not_forward_resume_to_keyframe(self):
        from skills.shapewear_video_generator.runtime import generate_video

        generate_video_engine = patch("skills.shapewear_video_generator.runtime.engine_generate_video")
        with generate_video_engine as engine:
            engine.return_value = str(Path("generated-video.mp4"))
            with tempfile.TemporaryDirectory() as output_dir:
                keyframe = str(Path(output_dir) / "generated-keyframe.png")
                with patch(
                    "skills.shapewear_video_generator.runtime.generate_image",
                    return_value={"outputs": [keyframe]},
                ) as generate_image:
                    generate_video(
                        {
                            "product": "black bodysuit",
                            "scene": "premium bedroom",
                            "style": "luxury fashion",
                            "type": "video",
                            "provider": "seedance",
                        },
                        image="existing-keyframe.png",
                        output_dir=output_dir,
                        resume_task_id="persisted-video",
                        on_task_submitted=lambda _task_id: None,
                    )
            generate_image.assert_not_called()
            self.assertEqual(engine.call_args.kwargs["resume_task_id"], "persisted-video")
            self.assertEqual(engine.call_args.kwargs["image"], "existing-keyframe.png")

    @patch("skills.shapewear_video_generator.runtime.engine_generate_video")
    def test_seedance_without_keyframe_generates_with_hermes_then_forwards_keyframe(self, generate_video_engine):
        from skills.shapewear_video_generator.runtime import generate_video

        client = self.FakeClient(b"skill-video")
        generate_video_engine.return_value = str(Path("generated-video.mp4"))
        with tempfile.TemporaryDirectory() as output_dir:
            keyframe = str(Path(output_dir) / "generated-keyframe.png")
            with patch(
                "skills.shapewear_video_generator.runtime.generate_image",
                return_value={"outputs": [keyframe]},
            ) as generate_image:
                manifest = generate_video(
                    {
                        "product": "black bodysuit",
                        "scene": "premium bedroom",
                        "style": "luxury fashion",
                        "type": "video",
                        "provider": "seedance",
                    },
                    client=client,
                    output_dir=output_dir,
                )

        generate_image.assert_called_once()
        image_request = generate_image.call_args.args[0]
        self.assertEqual(image_request["provider"], "hermes")
        self.assertEqual(generate_video_engine.call_args.kwargs["image"], keyframe)
        self.assertEqual(generate_video_engine.call_args.kwargs["provider"], "seedance")
        self.assertEqual(manifest["workflow"], "shapewear_video")

    def test_runtime_video_forwards_provider_override(self):
        from skills.shapewear_video_generator.runtime import generate_video

        client = self.FakeClient(b"skill-video")
        with tempfile.TemporaryDirectory() as output_dir:
            manifest = generate_video(
                {
                    "product": "black bodysuit",
                    "scene": "premium bedroom",
                    "style": "luxury fashion",
                    "type": "video",
                    "provider": "seedance",
                },
                image="keyframe.png",
                client=client,
                output_dir=output_dir,
            )
        self.assertEqual(manifest["workflow"], "shapewear_video")

    def test_runtime_rejects_unsafe_nested_fields(self):
        from skills.shapewear_video_generator.runtime import ShapewearRequestError, generate_image

        client = self.FakeClient(b"should-not-be-created")
        with self.assertRaises(ShapewearRequestError):
            generate_image({"product": "shapewear", "audience": {"segment": "teen girls"}}, client=client)
        with self.assertRaises(ShapewearRequestError):
            generate_image({"product": "shapewear", "claim": "doctor-approved pain relief"}, client=client)
        with self.assertRaises(ShapewearRequestError):
            generate_image({"product": "shapewear", "claim": "official SKIMS campaign"}, client=client)
        with self.assertRaises(ShapewearRequestError):
            generate_image({"product": "shapewear", "claim": "officially affiliated with SKIMS"}, client=client)
        self.assertEqual(client.submitted, [])


if __name__ == "__main__":
    unittest.main()
