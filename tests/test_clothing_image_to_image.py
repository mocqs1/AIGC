import base64
import json
import tempfile
import unittest
from pathlib import Path

from skills.clothing_image_to_image.runtime import (
    CLOTHING_PROVIDER,
    IMAGE_INPUT_BINDING,
    IMMUTABLE_GARMENT_CONTRACT,
    ClothingImageRequestError,
    build_prompt,
    generate_image,
)
from providers.image.hermes_client import HermesClient


class FakeClient:
    def __init__(self):
        self.submitted = []

    def submit_generation(self, prompt, **options):
        self.submitted.append((prompt, options))
        return {"task_id": "clothing-task"}

    def query_task(self, task_id):
        return {"status": "completed"}

    def get_result(self, task_id):
        return b"fake-image"


class ClothingImageToImageTests(unittest.TestCase):
    def test_generates_from_exactly_one_garment_reference(self):
        client = FakeClient()
        with tempfile.TemporaryDirectory() as output_dir:
            manifest = generate_image(
                {"objective": "Show the knit texture and reinforced seam in a clean catalog frame."},
                garment_image="garment.png",
                client=client,
                output_dir=output_dir,
            )
            self.assertEqual(manifest["skill"], "clothing-image-to-image")
            self.assertEqual(manifest["workflow"], "clothing_image_to_image")
            self.assertEqual(manifest["garment_reference"], "garment.png")
            self.assertTrue(Path(manifest["outputs"][0]).is_file())
            self.assertTrue((Path(output_dir) / "prompt.txt").is_file())
        self.assertEqual(client.submitted[0][1]["image"], "garment.png")
        self.assertNotIn("references", client.submitted[0][1])
        self.assertIn(IMMUTABLE_GARMENT_CONTRACT, client.submitted[0][0])
        self.assertIn(IMAGE_INPUT_BINDING, client.submitted[0][0])
        self.assertEqual(manifest["provider"], CLOTHING_PROVIDER)

    def test_hermes_receives_the_exact_garment_bytes_as_primary_image(self):
        calls = []

        def transport(method, url, headers, body, timeout):
            calls.append(json.loads(body.decode("utf-8")))
            result = {"data": [{"b64_json": base64.b64encode(b"generated-image").decode("ascii")}]}
            return 200, {"Content-Type": "application/json"}, json.dumps(result).encode("utf-8")

        source_bytes = b"the-selected-garment-bytes"
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "garment.png"
            source.write_bytes(source_bytes)
            manifest = generate_image(
                {"objective": "show the fabric weave and seam construction"},
                garment_image=str(source),
                client=HermesClient(
                    api_url="https://ark.cn-beijing.volces.com/api/v3",
                    api_key="test-key",
                    model="doubao-seedream-5-0-pro-260628",
                    transport=transport,
                ),
                output_dir=temporary,
            )

        payload = calls[0]
        self.assertEqual(payload["image"], "data:image/png;base64," + base64.b64encode(source_bytes).decode("ascii"))
        self.assertNotIn("reference_image_urls", payload)
        self.assertEqual(manifest["provider"], "hermes")

    def test_non_hermes_provider_is_rejected_before_generation(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "garment.png"
            source.write_bytes(b"garment")
            with self.assertRaisesRegex(ClothingImageRequestError, "Hermes"):
                generate_image(
                    {"objective": "show fabric detail"},
                    garment_image=str(source),
                    provider="liblib",
                    client=FakeClient(),
                    output_dir=temporary,
                )

    def test_reference_images_must_contain_one_image(self):
        for references in ([], ["a.png", "b.png"], ["a.mp4"]):
            with self.subTest(references=references), self.assertRaises(ClothingImageRequestError):
                generate_image({"prompt": "show fabric detail"}, reference_images=references, client=FakeClient())

    def test_reference_aliases_and_duplicate_source_are_rejected(self):
        with self.assertRaisesRegex(ClothingImageRequestError, "same single image"):
            generate_image({"prompt": "show fabric detail", "reference_images": ["a.png"]}, garment_image="b.png", client=FakeClient())
        with self.assertRaises(ClothingImageRequestError):
            generate_image({"prompt": "show fabric detail", "reference_images": ["a.png", "a.png"]}, client=FakeClient())
        with self.assertRaisesRegex(ClothingImageRequestError, "same single image"):
            generate_image({"prompt": "show fabric detail"}, garment_image="a.png", image="b.png", client=FakeClient())
        with self.assertRaisesRegex(ClothingImageRequestError, "same single image"):
            generate_image({"prompt": "show fabric detail", "garment_image": "a.png"}, garment_image="b.png", client=FakeClient())
        with self.assertRaisesRegex(ClothingImageRequestError, "exactly one"):
            generate_image({"prompt": "show fabric detail"}, garment_image="a.png", references=["b.png"], client=FakeClient())

    def test_unsafe_or_recoloring_objectives_are_rejected_before_provider(self):
        client = FakeClient()
        for prompt in (
            "Recolor the garment blue",
            "Change the fabric and add a new logo",
            "nude model product image",
            "doctor-approved slimming result",
        ):
            with self.subTest(prompt=prompt), self.assertRaises(ClothingImageRequestError):
                generate_image({"prompt": prompt}, garment_image="garment.png", client=client)
        self.assertEqual(client.submitted, [])

    def test_prompt_preview_is_deterministic_and_contains_detail_direction(self):
        request = {"scene": "neutral studio", "camera": "macro close-up", "lighting": "raking light"}
        first = build_prompt(request)
        self.assertEqual(first, build_prompt(request))
        self.assertIn("fabric texture", first)
        self.assertIn("single source of truth", first)


if __name__ == "__main__":
    unittest.main()
