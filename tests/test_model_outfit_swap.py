import tempfile
import unittest
from pathlib import Path

from skills.model_outfit_swap.runtime import (
    FIXED_OUTFIT_PROMPT,
    SEEDREAM_OUTFIT_PROMPT,
    GARMENT_FIDELITY_CHECKS,
    ModelOutfitSwapRequestError,
    generate_image,
)


class FakeClient:
    def __init__(self):
        self.submitted = []

    def submit_generation(self, prompt, **options):
        self.submitted.append((prompt, options))
        return {"task_id": "outfit-task"}

    def query_task(self, task_id):
        return {"status": "completed"}

    def get_result(self, task_id):
        return b"fake-image"


class ModelOutfitSwapSkillTests(unittest.TestCase):
    def test_generates_manifest_and_forwards_ordered_references(self):
        client = FakeClient()
        with tempfile.TemporaryDirectory() as output_dir:
            manifest = generate_image(
                {
                    "product": "red tailored jacket",
                    "scene": "bright studio",
                    "style": "clean catalog",
                    "reference_images": ["model.png", "jacket.png", "fabric.jpg"],
                },
                client=client,
                output_dir=output_dir,
            )
            self.assertEqual(manifest["skill"], "model-outfit-swap")
            self.assertEqual(manifest["workflow"], "model_outfit_swap")
            self.assertTrue(Path(manifest["outputs"][0]).is_file())
            self.assertTrue((Path(output_dir) / "prompt.txt").is_file())
            self.assertTrue(manifest["quality"]["passed"])
            self.assertEqual(manifest["garment_fidelity"]["standard"], "exact_visual_match")
            self.assertEqual(manifest["garment_fidelity"]["verification_status"], "pending_manual_review")
            self.assertTrue(manifest["garment_fidelity"]["requires_visual_review"])
            self.assertFalse(manifest["garment_fidelity"]["automatic_guarantee"])
            self.assertEqual(manifest["quality"]["garment_fidelity"], manifest["garment_fidelity"])
        self.assertEqual(client.submitted[0][1]["image"], "model.png")
        self.assertEqual(client.submitted[0][1]["references"], ["jacket.png", "fabric.jpg"])
        self.assertIn("Preserve", client.submitted[0][0])

    def test_requires_two_reference_images(self):
        with self.assertRaisesRegex(ModelOutfitSwapRequestError, "at least two"):
            generate_image({"prompt": "swap outfit", "reference_images": ["model.png"]}, client=FakeClient())

    def test_rejects_more_references_than_hermes_multi_image_limit(self):
        with self.assertRaisesRegex(ModelOutfitSwapRequestError, "at most 10"):
            generate_image(
                {"prompt": "swap outfit"},
                model_image="model.png",
                outfit_images=[f"outfit-{index}.png" for index in range(10)],
                client=FakeClient(),
            )

    def test_rejects_unsafe_request_before_provider_call(self):
        client = FakeClient()
        with self.assertRaises(ModelOutfitSwapRequestError):
            generate_image({"prompt": "nude minor model", "reference_images": ["model.png", "outfit.png"]}, client=client)
        self.assertEqual(client.submitted, [])

    def test_custom_prompt_cannot_remove_immutable_edit_contract(self):
        client = FakeClient()
        with tempfile.TemporaryDirectory() as output_dir:
            manifest = generate_image(
                {"prompt": "Change only the clothing and preserve the catalog scene."},
                model_image="model.png",
                outfit_images=["outfit.png", "outfit-detail.jpg"],
                client=client,
                output_dir=output_dir,
            )
        prompt = manifest["prompt"]
        self.assertEqual(prompt, FIXED_OUTFIT_PROMPT)
        self.assertIn("STRICT EDIT CONTRACT", prompt)
        self.assertIn("first reference image as the fixed adult model", prompt)
        self.assertIn("every later reference only as garment", prompt)
        self.assertIn("Do not change product details", prompt)

    def test_prompt_requires_material_and_manufacturing_evidence_fidelity(self):
        prompt = FIXED_OUTFIT_PROMPT.lower()
        for requirement in (
            "production evidence, not inspiration",
            "weave/knit direction",
            "texture scale",
            "thickness",
            "stretch",
            "compression",
            "sheen",
            "stitch lines and spacing",
            "overlock",
            "bartacks",
            "lining",
            "padding",
            "do not invent",
            "full-garment and close-up scale",
        ):
            with self.subTest(requirement=requirement):
                self.assertIn(requirement, prompt)
        self.assertNotIn("references 2 through 10", prompt)
        self.assertGreaterEqual(len(GARMENT_FIDELITY_CHECKS), 7)

    def test_prompt_is_the_gpt_image_2_source_locked_edit_contract(self):
        self.assertTrue(FIXED_OUTFIT_PROMPT.startswith("GPT-IMAGE-2 IMAGE EDIT"))
        self.assertLess(
            FIXED_OUTFIT_PROMPT.index("SOURCE BINDING"),
            FIXED_OUTFIT_PROMPT.index("EDIT MASK"),
        )
        self.assertLess(
            FIXED_OUTFIT_PROMPT.index("EDIT MASK"),
            FIXED_OUTFIT_PROMPT.index("GARMENT SOURCE OF TRUTH"),
        )
        self.assertIn("zero tolerated deviation", FIXED_OUTFIT_PROMPT.lower())
        self.assertIn("Do not regenerate the full scene", FIXED_OUTFIT_PROMPT)

    def test_generation_brief_fields_never_change_fixed_prompt(self):
        client = FakeClient()
        first = generate_image(
            {"prompt": "catalog product image", "reference_images": ["model.png", "outfit.png"]},
            client=client,
        )
        second = generate_image(
            {
                "product": "a completely different garment",
                "scene": "a different location",
                "style": "an unrelated visual style",
                "reference_images": ["model.png", "outfit.png"],
            },
            client=client,
        )
        self.assertEqual(first["prompt"], FIXED_OUTFIT_PROMPT)
        self.assertEqual(second["prompt"], FIXED_OUTFIT_PROMPT)
        self.assertEqual(client.submitted[0][0], client.submitted[1][0])

    def test_prompt_cannot_request_identity_or_background_edits(self):
        client = FakeClient()
        for prompt in (
            "Change the model identity and background",
            "Reshape the body and alter the pose",
            "Preserve the body. Change the background.",
            "Do not change anything except clothing. Change the model identity",
            "preserve unchanged; transform the body",
            "fixing your body",
            "reduce pain with this garment",
            "Change clothing and background",
            "Change only the clothing and alter identity",
            "Edit garment and remove background",
            "Replace the outfit and change the face",
            "Change clothing, then change identity and background",
        ):
            with self.subTest(prompt=prompt), self.assertRaises(ModelOutfitSwapRequestError):
                generate_image(
                    {"prompt": prompt},
                    model_image="model.png",
                    outfit_images=["outfit.png"],
                    client=client,
                )

    def test_non_image_reference_is_rejected_before_provider_call(self):
        client = FakeClient()
        with self.assertRaisesRegex(ModelOutfitSwapRequestError, "image file"):
            generate_image(
                {"prompt": "catalog image"},
                model_image="model.png",
                outfit_images=["outfit.mp4"],
                client=client,
            )
        self.assertEqual(client.submitted, [])

    def test_windows_local_paths_are_accepted_for_api_resolved_assets(self):
        client = FakeClient()
        with tempfile.TemporaryDirectory() as output_dir:
            manifest = generate_image(
                {"prompt": "catalog image"},
                model_image=r"D:\\aiworker\\AIGC\\outputs\\images\\model.png",
                outfit_images=[r"D:\\aiworker\\AIGC\\outputs\\images\\outfit.png"],
                client=client,
                output_dir=output_dir,
            )
        self.assertEqual(len(manifest["references"]), 2)

    def test_private_remote_reference_is_rejected(self):
        client = FakeClient()
        with self.assertRaisesRegex(ModelOutfitSwapRequestError, "public HTTPS"):
            generate_image(
                {"prompt": "catalog image"},
                model_image="https://127.0.0.1/model.png",
                outfit_images=["https://cdn.example.com/outfit.png"],
                client=client,
            )

    def test_seedream_provider_is_accepted_before_provider_call(self):
        client = FakeClient()
        manifest = generate_image(
            {"prompt": "catalog image", "provider": "hermes_volcano"},
            model_image="model.png",
            outfit_images=["outfit.png"],
            client=client,
        )
        self.assertEqual(manifest["provider"], "hermes_volcano")
        self.assertEqual(client.submitted[0][0], SEEDREAM_OUTFIT_PROMPT)

    def test_unknown_provider_is_rejected_before_provider_call(self):
        client = FakeClient()
        with self.assertRaisesRegex(ModelOutfitSwapRequestError, "Hermes and Hermes Volcano"):
            generate_image(
                {"prompt": "catalog image", "provider": "unknown"},
                model_image="model.png",
                outfit_images=["outfit.png"],
                client=client,
            )
        self.assertEqual(client.submitted, [])


if __name__ == "__main__":
    unittest.main()
