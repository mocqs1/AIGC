import json
import tempfile
import unittest
from pathlib import Path

from skills.model_outfit_swap.runtime import FIXED_OUTFIT_PROMPT, SEEDREAM_OUTFIT_PROMPT, OutfitSwapRequestError, generate_image


ROOT = Path(__file__).resolve().parents[1]


class FakeClient:
    def __init__(self, result=b"outfit-image"):
        self.result = result
        self.submitted = []
        self.queried = []
        self.results = []

    def submit_generation(self, prompt, **options):
        self.submitted.append((prompt, options))
        return {"task_id": "outfit-task"}

    def query_task(self, task_id):
        self.queried.append(task_id)
        return {"status": "completed"}

    def get_result(self, task_id):
        self.results.append(task_id)
        return self.result


class ModelOutfitSwapSkillTests(unittest.TestCase):
    def test_registry_workflow_and_entrypoint_are_present(self):
        registry = json.loads((ROOT / "skills" / "registry.json").read_text(encoding="utf-8"))
        entry = next(item for item in registry["skills"] if item["name"] == "model-outfit-swap")
        self.assertEqual(entry["entrypoints"]["image"], "skills.model_outfit_swap.runtime.generate_image")
        self.assertEqual(entry["provider"], "hermes")
        self.assertFalse(entry["provider_locked"])
        self.assertEqual(entry["providers"], ["hermes", "hermes_volcano"])
        self.assertIn("面料还原换装", entry["triggers"])
        self.assertIn("garment detail matching", entry["triggers"])
        workflow = json.loads(
            (ROOT / "skills" / "model-outfit-swap" / "workflows" / "model_outfit_swap.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(workflow["type"], "image")
        self.assertEqual(workflow["mode"], "image_to_image")
        self.assertEqual(workflow["prompt_policy"], "fixed_immutable_outfit_contract")
        self.assertEqual(workflow["model_family"], "gpt-image-2")
        self.assertEqual(workflow["model_families"]["hermes"], "gpt-image-2")
        self.assertEqual(workflow["model_families"]["hermes_volcano"], "doubao-seedream-5-0-pro-260628")
        self.assertEqual(workflow["provider_prompt_policies"]["hermes"], "gpt_image_2_clothing_only_source_locked_v1")
        self.assertEqual(workflow["provider_prompt_policies"]["hermes_volcano"], "seedream_clothing_only_source_locked_v1")
        self.assertFalse(workflow["accepts_custom_prompt"])
        self.assertEqual(workflow["input"]["minimum_references"], 2)
        constraints = workflow["provider_constraints"]
        self.assertEqual(constraints["garment_fidelity_standard"], "exact_visual_match")
        self.assertEqual(constraints["fidelity_verification_status"], "pending_manual_review")
        self.assertFalse(constraints["automatic_fidelity_guarantee"])
        quality_text = " ".join(workflow["quality_checks"]).lower()
        for detail in ("textile", "thickness", "stitch", "hardware", "ambiguous"):
            self.assertIn(detail, quality_text)

    def test_runtime_preserves_reference_order_and_writes_manifest(self):
        client = FakeClient()
        with tempfile.TemporaryDirectory() as output_dir:
            manifest = generate_image(
                {
                    "product": "a red wool coat",
                    "scene": "a bright studio",
                    "style": "minimal editorial",
                },
                model_image="model.jpg",
                outfit_images=["coat.jpg"],
                client=client,
                output_dir=output_dir,
            )
            output = Path(manifest["outputs"][0])
            self.assertTrue(output.is_file())
            self.assertEqual(output.read_bytes(), b"outfit-image")
            self.assertEqual(manifest["skill"], "model-outfit-swap")
            self.assertEqual(manifest["workflow"], "model_outfit_swap")
            self.assertEqual(manifest["references"], ["model.jpg", "coat.jpg"])
            self.assertEqual(manifest["aspect_ratio"], "portrait")
            self.assertEqual(manifest["quality"]["aspect_ratio"], "portrait")
            self.assertEqual(manifest["garment_fidelity"]["standard"], "exact_visual_match")
            self.assertEqual(manifest["garment_fidelity"]["verification_status"], "pending_manual_review")
            self.assertFalse(manifest["garment_fidelity"]["automatic_guarantee"])
            self.assertGreaterEqual(len(manifest["quality"]["manual_review"]), 9)
            self.assertTrue((Path(output_dir) / "prompt.txt").is_file())
            prompt, options = client.submitted[0]
            self.assertEqual(prompt, FIXED_OUTFIT_PROMPT)
            self.assertNotIn("red wool coat", prompt)
            self.assertIn("clothing-only", prompt.lower())
            self.assertEqual(options["image"], "model.jpg")
            self.assertEqual(options["references"], ["coat.jpg"])
            self.assertEqual(options["aspect_ratio"], "portrait")

    def test_rejects_more_references_than_hermes_multi_image_limit(self):
        with self.assertRaisesRegex(OutfitSwapRequestError, "at most 10"):
            generate_image(
                {"prompt": "fashion image"},
                model_image="model.jpg",
                outfit_images=[f"coat-{index}.jpg" for index in range(10)],
                client=FakeClient(),
            )

    def test_request_shape_and_safety_are_validated_before_provider_call(self):
        client = FakeClient()
        with self.assertRaisesRegex(OutfitSwapRequestError, "at least two"):
            generate_image({"prompt": "fashion image"}, model_image="model.jpg", outfit_images=[], client=client)
        with self.assertRaisesRegex(OutfitSwapRequestError, "at least two"):
            generate_image({"prompt": "fashion image", "reference_images": []}, client=client)
        with self.assertRaisesRegex(OutfitSwapRequestError, "model_image"):
            generate_image({"prompt": "fashion image", "outfit_images": ["coat.jpg"]}, client=client)
        with self.assertRaisesRegex(OutfitSwapRequestError, "unsupported"):
            generate_image(
                {"prompt": "make a nude minor image"},
                model_image="model.jpg",
                outfit_images=["coat.jpg"],
                client=client,
            )
        with self.assertRaisesRegex(OutfitSwapRequestError, "unsupported"):
            generate_image(
                {"prompt": "commercial fashion", "audience": {"segment": "teen models"}},
                model_image="model.jpg",
                outfit_images=["coat.jpg"],
                client=client,
            )
        self.assertEqual(client.submitted, [])

    def test_mapping_references_are_accepted(self):
        client = FakeClient()
        with tempfile.TemporaryDirectory() as output_dir:
            manifest = generate_image(
                {"prompt": "Professional adult fashion try-on", "model_asset": "model.png", "reference_images": [{"value": "jacket.png"}]},
                client=client,
                output_dir=output_dir,
            )
        self.assertEqual(manifest["references"], ["model.png", "jacket.png"])

    def test_explicit_model_with_full_reference_list_strips_duplicate_model(self):
        client = FakeClient()
        with tempfile.TemporaryDirectory() as output_dir:
            manifest = generate_image(
                {"prompt": "Professional adult fashion try-on"},
                model_image="model.png",
                reference_images=["model.png", "jacket.png"],
                client=client,
                output_dir=output_dir,
            )
        self.assertEqual(manifest["references"], ["model.png", "jacket.png"])
        self.assertEqual(client.submitted[0][1]["references"], ["jacket.png"])

    def test_references_alias_is_forwarded(self):
        client = FakeClient()
        with tempfile.TemporaryDirectory() as output_dir:
            manifest = generate_image(
                {"prompt": "Professional adult fashion try-on"},
                model_asset="model.png",
                references=["jacket.png"],
                client=client,
                output_dir=output_dir,
            )
        self.assertEqual(manifest["references"], ["model.png", "jacket.png"])

    def test_seedream_provider_uses_its_own_prompt_contract(self):
        client = FakeClient()
        with tempfile.TemporaryDirectory() as output_dir:
            manifest = generate_image(
                {"prompt": "catalog image"},
                model_image="model.png",
                outfit_images=["jacket.png"],
                provider="hermes_volcano",
                client=client,
                output_dir=output_dir,
            )
        self.assertEqual(manifest["provider"], "hermes_volcano")
        self.assertEqual(manifest["prompt"], SEEDREAM_OUTFIT_PROMPT)
        self.assertIn("SEEDREAM IMAGE EDIT", manifest["prompt"])
        self.assertNotEqual(manifest["prompt"], FIXED_OUTFIT_PROMPT)


if __name__ == "__main__":
    unittest.main()
