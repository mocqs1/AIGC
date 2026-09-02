import logging
import unittest
import json
import tempfile
from pathlib import Path

from providers.video.rest_client import TransportResponse
from providers.video.seedance_provider import SeedanceClient
from providers.video.veo_provider import VeoClient


class RestProviderTests(unittest.TestCase):
    def _transport(self, calls):
        def transport(method, url, headers, body, timeout):
            calls.append((method, url, headers, body, timeout))
            if method == "POST":
                return TransportResponse(200, {"Content-Type": "application/json"}, b'{"task_id":"abc"}')
            if url.endswith("/result"):
                return TransportResponse(200, {}, b"video")
            return TransportResponse(200, {"Content-Type": "application/json"}, b'{"status":"completed"}')
        return transport

    def test_veo_submit_poll_result_uses_configured_paths(self):
        calls = []
        client = VeoClient(
            api_key="test-veo-key",
            api_url="https://veo.example",
            submit_path="/generate",
            status_path="/tasks/{task_id}",
            result_path="/tasks/{task_id}/result",
            transport=self._transport(calls),
        )
        self.assertEqual(client.submit_generation("prompt", image="keyframe"), {"task_id": "abc"})
        self.assertEqual(client.query_task("abc"), {"status": "completed"})
        self.assertEqual(client.get_result("abc"), b"video")
        self.assertEqual([item[1] for item in calls], [
            "https://veo.example/generate",
            "https://veo.example/tasks/abc",
            "https://veo.example/tasks/abc/result",
        ])
        self.assertTrue(calls[0][2]["Authorization"].endswith("test-veo-key"))

    def test_seedance_uses_same_contract_without_real_network(self):
        calls = []
        client = SeedanceClient(
            api_key="test-seedance-key",
            api_url="https://seedance.example",
            transport=self._transport(calls),
        )
        self.assertEqual(client.query_task("task/with spaces"), {"status": "completed"})
        self.assertIn("task%2Fwith%20spaces", calls[0][1])

    def test_seedance_model_catalog_uses_configured_gateway(self):
        calls = []
        client = SeedanceClient(
            api_key="test-seedance-key",
            api_url="https://seedance.example/v1",
            transport=self._transport(calls),
        )

        self.assertEqual(client.list_models(), {"status": "completed"})
        self.assertEqual(calls[0][0:2], ("GET", "https://seedance.example/v1/models"))
        self.assertEqual(calls[0][2]["Authorization"], "Bearer test-seedance-key")


    def test_ark_seedance_uses_contents_task_contract(self):
        calls = []

        def transport(method, url, headers, body, timeout):
            calls.append((method, url, headers, body, timeout))
            if method == "POST":
                return TransportResponse(200, {"Content-Type": "application/json"}, b'{"id":"cgt-ark-1"}')
            return TransportResponse(200, {"Content-Type": "application/json"}, b'{"status":"succeeded","content":{"video_url":"https://cdn.example/ark.mp4"}}')

        client = SeedanceClient(
            api_key="ark-key",
            api_url="https://ark.cn-beijing.volces.com/api/v3",
            text_model="doubao-seedance-2-0-260128",
            transport=transport,
        )

        self.assertTrue(client.uses_ark)
        self.assertEqual(client.test_connection()["status"], "succeeded")
        submission = client.submit_generation("animate the product", image="https://example.com/frame.png")
        self.assertEqual(submission["id"], "cgt-ark-1")
        self.assertEqual(client.query_task("cgt-ark-1")["status"], "succeeded")
        self.assertEqual(calls[0][0:2], ("GET", "https://ark.cn-beijing.volces.com/api/v3/models"))
        self.assertEqual(calls[1][1], "https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks")
        payload = json.loads(calls[1][3].decode("utf-8"))
        self.assertEqual(payload["model"], "doubao-seedance-2-0-260128")
        self.assertEqual(payload["content"][0], {"type": "text", "text": "animate the product"})
        self.assertEqual(payload["content"][1], {"type": "image_url", "image_url": {"url": "https://example.com/frame.png"}})
        self.assertEqual(calls[2][1], "https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks/cgt-ark-1")

    def test_ark_seedance_local_image_uses_data_url_without_evolink_upload(self):
        calls = []

        def transport(method, url, headers, body, timeout):
            calls.append((method, url, headers, body, timeout))
            return TransportResponse(200, {"Content-Type": "application/json"}, b'{"id":"cgt-ark-2"}')

        with tempfile.TemporaryDirectory() as output_dir:
            image_path = Path(output_dir) / "frame.png"
            image_path.write_bytes(b"png-bytes")
            client = SeedanceClient(
                api_key="ark-key",
                api_url="https://ark.cn-beijing.volces.com/api/v3",
                text_model="doubao-seedance-2-0-260128",
                transport=transport,
            )
            client.submit_generation("animate", image=str(image_path))

        self.assertEqual(len(calls), 1)
        payload = json.loads(calls[0][3].decode("utf-8"))
        self.assertTrue(payload["content"][1]["image_url"]["url"].startswith("data:image/png;base64,"))
    def test_seedance_uploads_local_image_before_video_submission(self):
        calls = []

        def transport(method, url, headers, body, timeout):
            calls.append((method, url, headers, body, timeout))
            if url == "https://files.example/api/v1/files/upload/base64":
                return TransportResponse(
                    200,
                    {"Content-Type": "application/json"},
                    b'{"success":true,"data":{"file_url":"https://files.example/images/frame.png"}}',
                )
            return TransportResponse(200, {"Content-Type": "application/json"}, b'{"id":"task-1"}')

        with tempfile.TemporaryDirectory() as output_dir:
            image_path = Path(output_dir) / "frame.png"
            image_path.write_bytes(b"png-bytes")
            client = SeedanceClient(
                api_key="test-seedance-key",
                api_url="https://api.evolink.ai",
                upload_api_url="https://files.example",
                transport=transport,
            )

            submission = client.submit_generation("animate the product", image=str(image_path))

        self.assertEqual(submission["id"], "task-1")
        self.assertEqual(calls[0][0:2], ("POST", "https://files.example/api/v1/files/upload/base64"))
        self.assertEqual(calls[0][2]["Authorization"], "Bearer test-seedance-key")
        upload_payload = json.loads(calls[0][3].decode("utf-8"))
        self.assertTrue(upload_payload["base64_data"].startswith("data:image/png;base64,"))
        self.assertEqual(upload_payload["file_name"], "frame.png")
        self.assertEqual(calls[1][1], "https://api.evolink.ai/v1/videos/generations")
        video_payload = json.loads(calls[1][3].decode("utf-8"))
        self.assertEqual(video_payload["model"], "seedance-2.5-image-to-video")
        self.assertEqual(video_payload["image_urls"], ["https://files.example/images/frame.png"])

    def test_seedance_rejects_invalid_local_image_before_upload(self):
        client = SeedanceClient(
            api_key="test-seedance-key",
            api_url="https://api.evolink.ai",
            transport=lambda *args: self.fail("transport must not be called"),
        )
        with self.assertRaisesRegex(ValueError, "must be an existing file"):
            client.submit_generation("animate the product", image="missing.png")


    def test_seedance_uses_evolink_endpoints_models_and_task_results(self):
        calls = []

        def transport(method, url, headers, body, timeout):
            calls.append((method, url, headers, body, timeout))
            if method == "POST":
                return TransportResponse(
                    200,
                    {"Content-Type": "application/json"},
                    b'{"id":"task-unified-123","status":"pending"}',
                )
            if url == "https://cdn.example/video.mp4":
                return TransportResponse(200, {"Content-Type": "video/mp4"}, b"video")
            return TransportResponse(
                200,
                {"Content-Type": "application/json"},
                b'{"id":"task-unified-123","status":"completed",'
                b'"results":["https://cdn.example/video.mp4"]}',
            )

        client = SeedanceClient(
            api_key="test-seedance-key",
            api_url="https://api.evolink.ai",
            transport=transport,
        )

        submission = client.submit_generation("animate the product", image="https://example.com/frame.png")
        completed_task = client.get_result(submission["id"])

        self.assertEqual(submission["id"], "task-unified-123")
        self.assertEqual(completed_task["results"], ["https://cdn.example/video.mp4"])
        self.assertEqual(calls[0][1], "https://api.evolink.ai/v1/videos/generations")
        self.assertEqual(calls[1][1], "https://api.evolink.ai/v1/tasks/task-unified-123")
        self.assertEqual(calls[0][2]["Authorization"], "Bearer test-seedance-key")
        payload = json.loads(calls[0][3].decode("utf-8"))
        self.assertEqual(payload["model"], "seedance-2.5-image-to-video")
        self.assertEqual(payload["image_urls"], ["https://example.com/frame.png"])
        self.assertNotIn("image", payload)

    def test_seedance_rejects_non_public_uploaded_url(self):
        client = SeedanceClient(
            api_key="test-seedance-key",
            api_url="https://api.evolink.ai",
            upload_api_url="https://files.example",
            transport=lambda method, url, headers, body, timeout: TransportResponse(
                200,
                {"Content-Type": "application/json"},
                b'{"success":true,"data":{"file_url":"http://127.0.0.1/frame.png"}}',
            ),
        )
        with tempfile.TemporaryDirectory() as output_dir:
            image_path = Path(output_dir) / "frame.png"
            image_path.write_bytes(b"png-bytes")
            with self.assertRaisesRegex(ValueError, "public HTTPS URL"):
                client.submit_generation("animate the product", image=str(image_path))

    def test_request_error_preserves_http_status_and_detail(self):
        client = SeedanceClient(
            api_key="ark-key",
            api_url="https://ark.cn-beijing.volces.com/api/v3",
            text_model="doubao-seedance-2-0-260128",
            transport=lambda *args: TransportResponse(
                400,
                {"Content-Type": "application/json"},
                b'{"error":{"code":"InvalidParameter","message":"bad model"}}',
            ),
        )
        from providers.video.rest_client import VideoProviderRequestError
        with self.assertRaises(VideoProviderRequestError) as raised:
            client.submit_generation("animate")
        self.assertEqual(raised.exception.status, 400)
        self.assertIn("bad model", raised.exception.response_detail)

    def test_ark_rejects_non_seedance_model_before_request(self):
        client = SeedanceClient(
            api_key="ark-key",
            api_url="https://ark.cn-beijing.volces.com/api/v3",
            text_model="doubao-seed-2-0-mini-260428",
            transport=lambda *args: self.fail("request must not be sent with an invalid model"),
        )
        with self.assertRaisesRegex(ValueError, "requires a doubao-seedance model ID"):
            client.submit_generation("animate")

    def test_default_rest_provider_transport_rejects_private_probe_target(self):
        client = VeoClient(api_key="key", api_url="https://127.0.0.1/v1")
        with self.assertRaises(ValueError):
            client.test_connection()

        client = SeedanceClient(api_key="key", api_url="https://127.0.0.1/v1")
        with self.assertRaises(ValueError):
            client.test_connection()


if __name__ == "__main__":
    unittest.main()
