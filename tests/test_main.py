import tempfile
import unittest
from pathlib import Path

from main import generate_image, generate_video


class FakeClient:
    def __init__(self, result):
        self.result = result
        self.submitted = []

    def submit_generation(self, prompt, **options):
        self.submitted.append((prompt, options))
        return {"task_id": "main-task"}

    def query_task(self, task_id):
        return {"status": "completed"}

    def get_result(self, task_id):
        return self.result


class MainEntryPointTests(unittest.TestCase):
    def test_image_entry_optimizes_structured_request_and_saves_result(self):
        client = FakeClient(b"image")
        with tempfile.TemporaryDirectory() as output_dir:
            path = generate_image(
                {
                    "product": "black shapewear",
                    "scene": "premium bedroom",
                    "style": "luxury fashion advertisement",
                    "type": "image",
                },
                client=client,
                output_dir=output_dir,
            )
            saved = Path(path).read_bytes()
        self.assertEqual(saved, b"image")
        self.assertIn("black shapewear", client.submitted[0][0])

    def test_video_entry_supports_direct_prompt_and_reference_image(self):
        client = FakeClient(b"video")
        with tempfile.TemporaryDirectory() as output_dir:
            path = generate_video(
                {"prompt": "animate the product", "image": "keyframe.png"},
                client=client,
                output_dir=output_dir,
            )
            saved = Path(path).read_bytes()
        self.assertEqual(saved, b"video")
        self.assertEqual(client.submitted, [("animate the product", {"image": "keyframe.png"})])

    def test_video_entry_accepts_seedance_provider_override(self):
        client = FakeClient(b"video")
        with tempfile.TemporaryDirectory() as output_dir:
            path = generate_video(
                {"prompt": "animate the product", "provider": "seedance", "type": "video"},
                client=client,
                output_dir=output_dir,
            )
            self.assertTrue(Path(path).is_file())


if __name__ == "__main__":
    unittest.main()
