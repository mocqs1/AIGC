import tempfile
import unittest
from pathlib import Path

from providers.video.video_generator import VideoGenerationError, generate_video


class FakeVideoClient:
    def __init__(self, statuses, result=b"video-bytes"):
        self.statuses = list(statuses)
        self.result = result
        self.submitted = []
        self.queried = []
        self.results = []

    def submit_generation(self, prompt, **options):
        self.submitted.append((prompt, options))
        return {"task_id": "video-task-123"}

    def query_task(self, task_id):
        self.queried.append(task_id)
        return {"status": self.statuses.pop(0)}

    def get_result(self, task_id):
        self.results.append(task_id)
        return self.result


class VideoGeneratorTests(unittest.TestCase):
    def test_text_to_video_submits_polls_and_saves_bytes(self) -> None:
        client = FakeVideoClient(["pending", "completed"])
        with tempfile.TemporaryDirectory() as output_dir:
            path = generate_video("a product video prompt", client=client, output_dir=output_dir)
            saved = Path(path)
            self.assertTrue(saved.is_file())
            self.assertEqual(saved.read_bytes(), b"video-bytes")
            self.assertEqual(client.submitted, [("a product video prompt", {})])
            self.assertEqual(client.queried, ["video-task-123", "video-task-123"])
            self.assertEqual(client.results, ["video-task-123"])

    def test_image_to_video_passes_reference_image(self) -> None:
        client = FakeVideoClient(["success"])
        with tempfile.TemporaryDirectory() as output_dir:
            generate_video(
                "animate the product",
                image="https://example.test/product.png",
                client=client,
                output_dir=output_dir,
            )
        self.assertEqual(
            client.submitted,
            [("animate the product", {"image": "https://example.test/product.png"})],
        )

    def test_result_url_uses_client_downloader_and_video_extension(self) -> None:
        class UrlClient(FakeVideoClient):
            def __init__(self):
                super().__init__(
                    ["success"], result={"video_url": "https://example.test/render.webm"}
                )
                self.downloaded = []

            def download_result(self, url):
                self.downloaded.append(url)
                return b"url-video"

        client = UrlClient()
        with tempfile.TemporaryDirectory() as output_dir:
            path = generate_video("prompt", client=client, output_dir=output_dir)
            self.assertEqual(Path(path).suffix, ".webm")
            self.assertEqual(Path(path).read_bytes(), b"url-video")
        self.assertEqual(client.downloaded, ["https://example.test/render.webm"])

    def test_evolink_task_results_list_is_downloaded(self) -> None:
        class EvoLinkClient(FakeVideoClient):
            def __init__(self):
                super().__init__(["completed"])
                self.downloaded = []

            def get_result(self, task_id):
                self.results.append(task_id)
                return {"results": ["https://example.test/render.mp4"]}

            def download_result(self, url):
                self.downloaded.append(url)
                return b"evolink-video"

        client = EvoLinkClient()
        with tempfile.TemporaryDirectory() as output_dir:
            path = generate_video("prompt", client=client, output_dir=output_dir)
            self.assertEqual(Path(path).suffix, ".mp4")
            self.assertEqual(Path(path).read_bytes(), b"evolink-video")
        self.assertEqual(client.downloaded, ["https://example.test/render.mp4"])

    def test_failed_task_raises_without_fetching_result(self) -> None:
        client = FakeVideoClient(["failed"])
        with tempfile.TemporaryDirectory() as output_dir:
            with self.assertRaisesRegex(VideoGenerationError, "failed"):
                generate_video("prompt", client=client, output_dir=output_dir)
        self.assertEqual(client.results, [])


if __name__ == "__main__":
    unittest.main()
