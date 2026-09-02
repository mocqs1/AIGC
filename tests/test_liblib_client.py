import json
import unittest

from providers.liblib.client import LiblibClient, LiblibRequestError, TransportResponse


class RecordingTransport:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append((method, url, headers, body, timeout))
        if self.error:
            raise self.error
        return self.response


class LiblibClientTests(unittest.TestCase):
    def test_submit_sends_auth_and_json_payload(self) -> None:
        transport = RecordingTransport(
            TransportResponse(200, {"Content-Type": "application/json"}, b'{"task_id":"t-1"}')
        )
        client = LiblibClient(
            api_key="test-key",
            api_url="https://example.test/api/",
            timeout=4,
            transport=transport,
        )

        response = client.submit_generation("a product", width=512, height=512)
        self.assertEqual(response, {"task_id": "t-1"})
        method, url, headers, body, timeout = transport.calls[0]
        self.assertEqual(method, "POST")
        self.assertEqual(url, "https://example.test/api/generate")
        self.assertEqual(headers["Authorization"], "Bearer test-key")
        self.assertIn("video/*", headers["Accept"])
        self.assertEqual(
            json.loads(body.decode("utf-8")),
            {"prompt": "a product", "width": 512, "height": 512},
        )
        self.assertEqual(timeout, 4.0)

    def test_configured_model_is_sent_and_catalog_uses_models_endpoint(self) -> None:
        transport = RecordingTransport(
            TransportResponse(200, {"Content-Type": "application/json"}, b'{"data":[{"id":"lib-model"}]}')
        )
        client = LiblibClient(
            api_key="test-key",
            api_url="https://example.test/api",
            model="lib-model",
            transport=transport,
        )

        client.submit_generation("a product")
        catalog = client.list_models()

        self.assertEqual(json.loads(transport.calls[0][3].decode("utf-8"))["model"], "lib-model")
        self.assertEqual(transport.calls[1][0:2], ("GET", "https://example.test/api/models"))
        self.assertEqual(transport.calls[1][2]["Authorization"], "Bearer test-key")
        self.assertEqual(catalog, {"data": [{"id": "lib-model"}]})

    def test_query_and_result_use_task_endpoints(self) -> None:
        transport = RecordingTransport(TransportResponse(200, {}, b'{"status":"completed"}'))
        client = LiblibClient(api_key="key", api_url="https://example.test/api", transport=transport)

        client.query_task("task 1")
        client.get_result("task 1")
        self.assertEqual(
            transport.calls[0][0:2],
            ("GET", "https://example.test/api/generate/task%201"),
        )
        self.assertEqual(
            transport.calls[1][0:2],
            ("GET", "https://example.test/api/generate/task%201/result"),
        )

    def test_timeout_is_wrapped_as_request_error(self) -> None:
        transport = RecordingTransport(error=TimeoutError("slow"))
        client = LiblibClient(api_key="key", transport=transport)

        with self.assertRaisesRegex(LiblibRequestError, "timed out"):
            client.query_task("task-1")

    def test_http_error_is_wrapped_with_status(self) -> None:
        transport = RecordingTransport(TransportResponse(503, {}, b'{"error":"busy"}'))
        client = LiblibClient(api_key="key", transport=transport)

        with self.assertRaisesRegex(LiblibRequestError, "HTTP 503.*busy"):
            client.query_task("task-1")

    def test_download_result_does_not_forward_api_authorization(self) -> None:
        transport = RecordingTransport(
            TransportResponse(200, {"Content-Type": "video/mp4"}, b"video-bytes")
        )
        client = LiblibClient(api_key="secret", transport=transport)

        self.assertEqual(client.download_result("https://cdn.example.test/result.mp4"), b"video-bytes")
        headers = transport.calls[0][2]
        self.assertNotIn("Authorization", headers)

    def test_default_transport_rejects_private_probe_target(self) -> None:
        client = LiblibClient(api_key="key", api_url="https://127.0.0.1/api")
        with self.assertRaises(ValueError):
            client.test_connection()


if __name__ == "__main__":
    unittest.main()
