import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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
        self.assertIn("clean neutral studio", client.submitted[0][0])
        self.assertNotIn("premium bedroom", client.submitted[0][0])
        self.assertIn("INTIMATE-APPAREL SAFETY LOCK", client.submitted[0][0])


    def test_image_entry_uses_configured_module_client_when_client_is_none(self) -> None:
        sentinel = object()
        with tempfile.TemporaryDirectory() as output_dir:
            destination = Path(output_dir) / "out.png"
            with patch("main._configured_image_client", return_value=sentinel) as configured:
                with patch("main._generate_image", return_value=str(destination)) as generate:
                    with patch("main.load_workflow", return_value={"type": "image", "provider": "hermes"}):
                        with patch("api_server._default_image_provider", return_value="image.backup"):
                            generate_image({"prompt": "studio product photo", "type": "image"}, output_dir=output_dir)
        configured.assert_called_once_with("image.backup")
        self.assertIs(generate.call_args.kwargs["client"], sentinel)

    def test_image_entry_forwards_custom_provider_to_module_client(self) -> None:
        sentinel = object()
        with tempfile.TemporaryDirectory() as output_dir:
            destination = Path(output_dir) / "out.png"
            with patch("main._configured_image_client", return_value=sentinel) as configured:
                with patch("main._generate_image", return_value=str(destination)) as generate:
                    generate_image(
                        {"prompt": "studio product photo", "type": "image", "provider": "image.backup"},
                        output_dir=output_dir,
                    )
        configured.assert_called_once_with("image.backup")
        self.assertIs(generate.call_args.kwargs["client"], sentinel)

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

    def test_video_entry_forwards_resume_task_id(self):
        client = FakeClient(b"video")
        submitted = []
        with tempfile.TemporaryDirectory() as output_dir:
            path = generate_video(
                {"prompt": "animate the product", "type": "video"},
                client=client,
                output_dir=output_dir,
                resume_task_id="persisted-video",
                on_task_submitted=submitted.append,
            )
            self.assertTrue(Path(path).is_file())
        self.assertEqual(client.submitted, [])
        self.assertEqual(submitted, [])


if __name__ == "__main__":
    unittest.main()
