import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from providers.video.google_veo_provider import GoogleVeoClient, _resolve_public_https_target
from providers.video.rest_client import TransportResponse
from providers.video.video_generator import generate_video


class GoogleVeoProviderTests(unittest.TestCase):
    def test_native_submit_poll_download_and_image_to_video(self) -> None:
        calls = []

        def transport(method, url, headers, body, timeout):
            calls.append((method, url, headers, body, timeout))
            if method == "POST":
                return TransportResponse(200, {"Content-Type": "application/json"}, b'{"name":"operations/veo-1"}')
            if url.endswith("/media/video.mp4"):
                return TransportResponse(200, {"Content-Type": "video/mp4"}, b"veo-video")
            if len([call for call in calls if call[0] == "GET"]) == 1:
                return TransportResponse(200, {"Content-Type": "application/json"}, b'{"name":"operations/veo-1"}')
            return TransportResponse(
                200,
                {"Content-Type": "application/json"},
                b'{"name":"operations/veo-1","done":true,"response":{"generateVideoResponse":{"generatedSamples":[{"video":{"uri":"https://veo.test/media/video.mp4"}}]}}}',
            )

        client = GoogleVeoClient(
            api_key="test-key",
            model="veo-3.1-fast-generate-preview",
            transport=transport,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "keyframe.png"
            image_path.write_bytes(b"png-bytes")
            output_path = generate_video(
                "animate this shapewear",
                image=str(image_path),
                client=client,
                output_dir=temp_dir,
            )
            self.assertEqual(Path(output_path).read_bytes(), b"veo-video")

        post = calls[0]
        self.assertEqual(post[0], "POST")
        self.assertIn("/models/veo-3.1-fast-generate-preview:predictLongRunning", post[1])
        self.assertEqual(post[2]["x-goog-api-key"], "test-key")
        payload = json.loads(post[3].decode("utf-8"))
        self.assertEqual(payload["instances"][0]["prompt"], "animate this shapewear")
        self.assertEqual(
            payload["instances"][0]["image"]["inlineData"]["data"],
            base64.b64encode(b"png-bytes").decode("ascii"),
        )
        download = next(call for call in calls if call[1].endswith("/media/video.mp4"))
        self.assertNotIn("x-goog-api-key", download[2])

    def test_result_download_does_not_forward_api_key(self) -> None:
        calls = []

        def transport(method, url, headers, body, timeout):
            calls.append((method, url, headers, body, timeout))
            return TransportResponse(200, {"Content-Type": "video/mp4"}, b"veo-video")

        client = GoogleVeoClient(api_key="test-key", transport=transport)
        self.assertEqual(client.download_result("https://cdn.example.test/result.mp4"), b"veo-video")
        self.assertEqual(calls[0][0], "GET")
        self.assertNotIn("x-goog-api-key", calls[0][2])

    def test_model_catalog_uses_google_models_endpoint(self) -> None:
        calls = []

        def transport(method, url, headers, body, timeout):
            calls.append((method, url, headers, body, timeout))
            return TransportResponse(
                200,
                {"Content-Type": "application/json"},
                b'{"models":[{"name":"models/veo-fast"}]}',
            )

        client = GoogleVeoClient(api_key="test-key", transport=transport)

        self.assertEqual(client.list_models(), {"models": [{"name": "models/veo-fast"}]})
        self.assertTrue(calls[0][1].endswith("/v1beta/models"))
        self.assertEqual(calls[0][2]["x-goog-api-key"], "test-key")

    @patch("providers.video.google_veo_provider.socket.getaddrinfo")
    def test_remote_input_image_rejects_private_dns_results(self, getaddrinfo) -> None:
        getaddrinfo.return_value = [
            (2, 1, 6, "", ("127.0.0.1", 443)),
        ]
        with self.assertRaisesRegex(ValueError, "public addresses"):
            _resolve_public_https_target("https://rebind.example.test/image.png")

    @patch("providers.video.google_veo_provider.socket.getaddrinfo")
    def test_remote_input_image_rejects_mixed_public_and_private_dns_results(self, getaddrinfo) -> None:
        getaddrinfo.return_value = [
            (2, 1, 6, "", ("203.0.113.10", 443)),
            (2, 1, 6, "", ("10.0.0.10", 443)),
        ]
        with self.assertRaisesRegex(ValueError, "public addresses"):
            _resolve_public_https_target("https://mixed.example.test/image.png")

    @patch("providers.video.google_veo_provider.socket.getaddrinfo")
    def test_remote_input_image_rejects_cgnat_dns_results(self, getaddrinfo) -> None:
        getaddrinfo.return_value = [(2, 1, 6, "", ("100.64.0.1", 443))]
        with self.assertRaisesRegex(ValueError, "public addresses"):
            _resolve_public_https_target("https://shared.example.test/image.png")

    def test_failed_operation_is_reported(self) -> None:
        def transport(method, url, headers, body, timeout):
            if method == "POST":
                return {"name": "operations/failed"}
            return {"name": "operations/failed", "done": True, "error": {"message": "quota"}}

        client = GoogleVeoClient(api_key="test-key", transport=transport)
        with self.assertRaisesRegex(Exception, "failed.*quota"):
            generate_video("prompt", client=client, output_dir=tempfile.mkdtemp())

    def test_default_transport_rejects_private_probe_target(self) -> None:
        client = GoogleVeoClient(api_key="key", api_url="https://127.0.0.1/v1")
        with self.assertRaises(ValueError):
            client.test_connection()


if __name__ == "__main__":
    unittest.main()
