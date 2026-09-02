import tempfile
import unittest
from pathlib import Path

from providers.image.image_generator import ImageGenerationError, generate_image


class FakeImageClient:
    def __init__(self, statuses, result=b"image-bytes"):
        self.statuses = list(statuses)
        self.result = result
        self.submitted = []
        self.queried = []
        self.results = []

    def submit_generation(self, prompt, **options):
        self.submitted.append((prompt, options))
        return {"task_id": "task-123"}

    def query_task(self, task_id):
        self.queried.append(task_id)
        return {"status": self.statuses.pop(0)}

    def get_result(self, task_id):
        self.results.append(task_id)
        return self.result


class ImageGeneratorTests(unittest.TestCase):
    def test_resume_task_skips_submit_and_notifies_new_submission_only(self) -> None:
        client = FakeImageClient(["completed"])
        submitted = []
        with tempfile.TemporaryDirectory() as output_dir:
            path = generate_image(
                "prompt",
                client=client,
                output_dir=output_dir,
                resume_task_id="persisted-task",
                on_task_submitted=submitted.append,
                max_wait_seconds=1,
            )
            self.assertTrue(Path(path).is_file())
        self.assertEqual(client.submitted, [])
        self.assertEqual(client.queried, ["persisted-task"])
        self.assertEqual(submitted, [])

    def test_new_task_callback_receives_provider_id(self) -> None:
        client = FakeImageClient(["completed"])
        submitted = []
        with tempfile.TemporaryDirectory() as output_dir:
            generate_image("prompt", client=client, output_dir=output_dir, on_task_submitted=submitted.append)
        self.assertEqual(submitted, ["task-123"])

    def test_direct_image_result_takes_precedence_over_generic_response_id(self) -> None:
        class SyncImageClient(FakeImageClient):
            def submit_generation(self, prompt, **options):
                return {"id": "request-id", "data": [{"b64_json": "aW1hZ2UtYnl0ZXM="}]}

        client = SyncImageClient([])
        with tempfile.TemporaryDirectory() as output_dir:
            path = generate_image("prompt", client=client, output_dir=output_dir)
            self.assertEqual(Path(path).read_bytes(), b"image-bytes")
        self.assertEqual(client.queried, [])
        self.assertEqual(client.results, [])

    def test_empty_direct_data_still_allows_async_task_polling(self) -> None:
        class AsyncImageClient(FakeImageClient):
            def submit_generation(self, prompt, **options):
                return {"id": "task-123", "data": []}

        client = AsyncImageClient(["completed"])
        with tempfile.TemporaryDirectory() as output_dir:
            path = generate_image("prompt", client=client, output_dir=output_dir)
            self.assertEqual(Path(path).read_bytes(), b"image-bytes")
        self.assertEqual(client.queried, ["task-123"])
        self.assertEqual(client.results, ["task-123"])

    def test_submit_poll_result_and_save_bytes(self) -> None:
        client = FakeImageClient(["pending", "completed"])
        with tempfile.TemporaryDirectory() as output_dir:
            path = generate_image("a product prompt", client=client, output_dir=output_dir)
            saved = Path(path)
            self.assertTrue(saved.is_file())
            self.assertEqual(saved.read_bytes(), b"image-bytes")
            self.assertEqual(client.submitted, [("a product prompt", {})])
            self.assertEqual(client.queried, ["task-123", "task-123"])
            self.assertEqual(client.results, ["task-123"])

    def test_result_url_uses_client_downloader(self) -> None:
        class UrlClient(FakeImageClient):
            def __init__(self):
                super().__init__(["success"], result={"result_url": "https://example.test/render.jpg"})
                self.downloaded = []

            def download_result(self, url):
                self.downloaded.append(url)
                return b"url-image"

        client = UrlClient()
        with tempfile.TemporaryDirectory() as output_dir:
            path = generate_image("prompt", client=client, output_dir=output_dir)
            self.assertEqual(Path(path).suffix, ".jpg")
            self.assertEqual(Path(path).read_bytes(), b"url-image")
        self.assertEqual(client.downloaded, ["https://example.test/render.jpg"])

    def test_failed_task_raises_without_fetching_result(self) -> None:
        client = FakeImageClient(["failed"])
        with tempfile.TemporaryDirectory() as output_dir:
            with self.assertRaisesRegex(ImageGenerationError, "failed"):
                generate_image("prompt", client=client, output_dir=output_dir)
        self.assertEqual(client.results, [])


if __name__ == "__main__":
    unittest.main()
