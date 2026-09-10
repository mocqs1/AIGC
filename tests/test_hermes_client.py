import base64
import json
import logging
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from providers.image.hermes_client import HermesClient, HermesConfigurationError, HermesRequestError
from providers.image.image_generator import generate_image


class HermesClientTests(unittest.TestCase):
    def test_task_stages_use_bounded_timeouts(self):
        calls = []

        def transport(method, url, headers, body, timeout):
            calls.append((method, url, timeout))
            return 200, {"Content-Type": "application/json"}, b'{"status":"completed"}'

        client = HermesClient(
            api_url="https://hermes.example/v1",
            api_key="secret",
            timeout=180,
            query_timeout=7,
            result_timeout=11,
            transport=transport,
        )
        client.query_task("task")
        client.get_result("task")
        self.assertEqual(calls[0][2], 7.0)
        self.assertEqual(calls[1][2], 11.0)

    def test_timeout_is_forwarded_and_request_logs_are_stage_specific(self):
        calls = []

        def transport(method, url, headers, body, timeout):
            calls.append((method, url, timeout))
            return 200, {"Content-Type": "application/json"}, b'{"data": [{"id": "model-a"}]}'

        logger = logging.getLogger("hermes-client-test")
        with self.assertLogs(logger, level="INFO") as captured:
            HermesClient(
                api_url="https://hermes.example/v1",
                api_key="secret",
                timeout=240,
                transport=transport,
                request_logger=logger,
            ).test_connection()

        self.assertEqual(calls[0][2], 240.0)
        output = "\n".join(captured.output)
        self.assertIn("stage=test_connection", output)
        self.assertIn("path=/models", output)
        self.assertIn("elapsed_ms=", output)

    def test_timeout_rejects_non_finite_or_non_positive_values(self):
        for timeout in (0, -1, "not-a-number", float("inf")):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                HermesClient(api_url="https://hermes.example/v1", api_key="secret", timeout=timeout)

    def test_provider_error_preserves_code_and_logs_only_sanitized_detail(self):
        logger = logging.getLogger("hermes-provider-error-test")
        response_body = json.dumps(
            {
                "code": "InputImageSensitiveContentDetected",
                "message": "input data:image/png;base64,AAAA https://cdn.example/image.png?sig=secret",
            }
        ).encode("utf-8")

        def transport(method, url, headers, body, timeout):
            return 400, {"Content-Type": "application/json"}, response_body

        with self.assertLogs(logger, level="WARNING") as captured:
            with self.assertRaises(HermesRequestError) as raised:
                HermesClient(
                    api_url="https://hermes.example/v1",
                    api_key="secret",
                    transport=transport,
                    request_logger=logger,
                ).submit_generation("prompt")

        error = raised.exception
        self.assertEqual(error.status, 400)
        self.assertEqual(error.provider_code, "InputImageSensitiveContentDetected")
        self.assertNotIn("data:image/png;base64,AAAA", str(error))
        self.assertNotIn("https://cdn.example", str(error))
        output = "\n".join(captured.output)
        self.assertIn("status=400", output)
        self.assertIn("code=InputImageSensitiveContentDetected", output)
        self.assertNotIn("data:image/png;base64,AAAA", output)
        self.assertNotIn("https://cdn.example", output)

    def test_gpt_image_2_edit_uses_multipart_and_preserves_source_order(self):
        calls = []

        def transport(method, url, headers, body, timeout):
            calls.append((method, url, headers, body, timeout))
            payload = {"data": [{"b64_json": base64.b64encode(b"generated-image").decode("ascii")}]}
            return 200, {"Content-Type": "application/json"}, json.dumps(payload).encode("utf-8")

        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source.png"
            reference = Path(temporary) / "reference.jpg"
            source.write_bytes(b"source")
            reference.write_bytes(b"reference")
            output = generate_image(
                "product image",
                client=HermesClient(api_url="https://hermes.example/v1", api_key="secret", transport=transport),
                image=str(source),
                references=[str(reference)],
                aspect_ratio="portrait",
                output_dir=temporary,
            )

            self.assertEqual(Path(output).read_bytes(), b"generated-image")
            method, url, headers, body, _timeout = calls[0]
            self.assertEqual(method, "POST")
            self.assertEqual(url, "https://hermes.example/v1/images/edits")
            self.assertIn("multipart/form-data; boundary=", headers["Content-Type"])
            self.assertNotIn("image_url", body.decode("utf-8", "replace"))
            self.assertNotIn("reference_image_urls", body.decode("utf-8", "replace"))
            self.assertIn(b'name="model"\r\n\r\ngpt-image-2', body)
            self.assertIn(b'name="size"\r\n\r\n1024x1536', body)
            self.assertIn(b'name="quality"\r\n\r\nhigh', body)
            self.assertGreaterEqual(body.count(b'name="image[]"; filename='), 2)
            self.assertNotIn(b'name="image"; filename=', body)
            source_offset = body.index(b"source")
            reference_offset = body.index(b"reference")
            self.assertLess(source_offset, reference_offset)
            self.assertEqual(calls[0][2]["Authorization"], "Bearer secret")

    def test_non_gpt_hermes_models_keep_legacy_json_reference_fields(self):
        calls = []

        def transport(method, url, headers, body, timeout):
            calls.append((method, url, headers, body, timeout))
            payload = {"data": [{"b64_json": base64.b64encode(b"generated-image").decode("ascii")}]}
            return 200, {"Content-Type": "application/json"}, json.dumps(payload).encode("utf-8")

        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source.png"
            reference = Path(temporary) / "reference.jpg"
            source.write_bytes(b"source")
            reference.write_bytes(b"reference")
            generate_image(
                "product image",
                client=HermesClient(api_url="https://hermes.example/v1", api_key="secret", model="image-model", transport=transport),
                image=str(source),
                references=[str(reference)],
                output_dir=temporary,
            )

        payload = json.loads(calls[0][3].decode("utf-8"))
        self.assertEqual(payload["image_url"].split(",", 1)[0], "data:image/png;base64")
        self.assertEqual(payload["reference_image_urls"][0].split(",", 1)[0], "data:image/jpeg;base64")
        self.assertEqual(calls[0][1], "https://hermes.example/v1/images/generations")

    def test_gpt_image_2_rejects_generation_path_as_edit_path(self):
        client = HermesClient(
            api_url="https://hermes.example/v1",
            api_key="secret",
            submit_path="/images/generations",
            edit_path="/images/generations",
        )
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source.png"
            source.write_bytes(b"source")
            with self.assertRaises(HermesConfigurationError):
                client.submit_generation("edit", image=str(source))


    def test_gpt_image_2_text_generation_uses_openai_size_quality_and_n(self):
        calls = []

        def transport(method, url, headers, body, timeout):
            calls.append((method, url, headers, body, timeout))
            payload = {"data": [{"b64_json": base64.b64encode(b"generated-image").decode("ascii")}]}
            return 200, {"Content-Type": "application/json"}, json.dumps(payload).encode("utf-8")

        with tempfile.TemporaryDirectory() as temporary:
            output = generate_image(
                "studio product photo",
                client=HermesClient(api_url="https://hermes.example/v1", api_key="secret", transport=transport),
                aspect_ratio="portrait",
                output_dir=temporary,
            )
            self.assertEqual(Path(output).read_bytes(), b"generated-image")
            payload = json.loads(calls[0][3].decode("utf-8"))
            self.assertEqual(calls[0][1], "https://hermes.example/v1/images/generations")
            self.assertEqual(payload["model"], "gpt-image-2")
            self.assertEqual(payload["prompt"], "studio product photo")
            self.assertEqual(payload["size"], "1024x1536")
            self.assertEqual(payload["quality"], "high")
            self.assertEqual(payload["n"], 1)
            self.assertNotIn("aspect_ratio", payload)

    def test_submit_generation_retries_gateway_502_then_succeeds(self):
        calls = []

        def transport(method, url, headers, body, timeout):
            calls.append((method, url, headers, body, timeout))
            if len(calls) == 1:
                return 502, {"Content-Type": "application/json"}, json.dumps({"code": "right_codes_task_failed", "message": "Right Codes image task failed"}).encode("utf-8")
            payload = {"data": [{"b64_json": base64.b64encode(b"generated-image").decode("ascii")}]}
            return 200, {"Content-Type": "application/json"}, json.dumps(payload).encode("utf-8")

        with tempfile.TemporaryDirectory() as temporary, mock.patch("providers.image.hermes_client.time.sleep"):
            output = generate_image(
                "studio product photo",
                client=HermesClient(api_url="https://hermes.example/v1", api_key="secret", transport=transport),
                output_dir=temporary,
            )
            self.assertEqual(Path(output).read_bytes(), b"generated-image")
            self.assertEqual(len(calls), 2)
            self.assertEqual(json.loads(calls[0][3].decode("utf-8"))["size"], "1024x1024")

    def test_submit_edit_does_not_retry_no_compatible_accounts(self):
        calls = []

        def transport(method, url, headers, body, timeout):
            calls.append((method, url, headers, body, timeout))
            return 503, {"Content-Type": "application/json"}, json.dumps({"code": "api_error", "message": "No available compatible accounts"}).encode("utf-8")

        with tempfile.TemporaryDirectory() as temporary, mock.patch("providers.image.hermes_client.time.sleep") as sleep:
            source = Path(temporary) / "source.png"
            source.write_bytes(b"source")
            client = HermesClient(api_url="https://hermes.example/v1", api_key="secret", transport=transport)
            with self.assertRaises(HermesRequestError) as raised:
                client.submit_generation("edit with product", image=str(source))
            self.assertEqual(raised.exception.status, 503)
            self.assertEqual(raised.exception.provider_code, "api_error")
            self.assertEqual(raised.exception.provider_message, "No available compatible accounts")
            self.assertEqual(len(calls), 1)
            self.assertTrue(str(calls[0][1]).endswith("/images/edits"))
            sleep.assert_not_called()


    def test_seedream_uses_ordered_image_array_and_b64_response(self):
        calls = []

        def transport(method, url, headers, body, timeout):
            calls.append((method, url, headers, body, timeout))
            payload = {"data": [{"b64_json": base64.b64encode(b"generated-image").decode("ascii")}]}
            return 200, {"Content-Type": "application/json"}, json.dumps(payload).encode("utf-8")

        with tempfile.TemporaryDirectory() as temporary:
            model = Path(temporary) / "model.png"
            garment = Path(temporary) / "garment.jpg"
            detail = Path(temporary) / "detail.webp"
            model.write_bytes(b"model")
            garment.write_bytes(b"garment")
            detail.write_bytes(b"detail")
            output = generate_image(
                "fixed outfit prompt",
                client=HermesClient(
                    api_url="https://ark.cn-beijing.volces.com/api/v3",
                    api_key="secret",
                    model="doubao-seedream-5-0-pro-260628",
                    transport=transport,
                ),
                image=str(model),
                references=[str(garment), str(detail)],
                aspect_ratio="portrait",
                output_dir=temporary,
            )

            self.assertEqual(Path(output).read_bytes(), b"generated-image")
            payload = json.loads(calls[0][3].decode("utf-8"))
            self.assertEqual(payload["size"], "1152x2048")
            self.assertEqual(payload["response_format"], "b64_json")
            # Seedream's Ark endpoint rejects the OpenAI-style output_format
            # field; b64_json already preserves the generated image bytes.
            self.assertNotIn("output_format", payload)
            self.assertFalse(payload["watermark"])
            self.assertNotIn("aspect_ratio", payload)
            self.assertNotIn("image_url", payload)
            self.assertNotIn("reference_image_urls", payload)
            self.assertEqual(len(payload["image"]), 3)
            # Seedream 5.0 Pro rejects the sequential generation switch. The
            # default response is already a single image for this workflow.
            self.assertNotIn("sequential_image_generation", payload)
            self.assertTrue(payload["image"][0].startswith("data:image/png;base64,"))
            self.assertTrue(payload["image"][1].startswith("data:image/jpeg;base64,"))
            self.assertTrue(payload["image"][2].startswith("data:image/webp;base64,"))
            self.assertEqual(calls[0][2]["Authorization"], "Bearer secret")

    def test_seedream_single_image_uses_string_and_enforces_ten_image_limit(self):
        calls = []

        def transport(method, url, headers, body, timeout):
            calls.append(json.loads(body.decode("utf-8")))
            return 200, {"Content-Type": "application/json"}, b'{"data":[{"b64_json":"aW1hZ2U="}]}'

        client = HermesClient(
            api_url="https://ark.cn-beijing.volces.com/api/v3",
            api_key="secret",
            model="doubao-seedream-5-0-pro-260628",
            transport=transport,
        )
        client.submit_generation("prompt", image="https://cdn.example/model.png")
        self.assertIsInstance(calls[0]["image"], str)
        with self.assertRaisesRegex(Exception, "at most 10"):
            client.submit_generation(
                "prompt",
                image="https://cdn.example/model.png",
                references=[f"https://cdn.example/garment-{index}.png" for index in range(10)],
            )

    def test_external_result_download_does_not_receive_api_key(self):
        calls = []

        def transport(method, url, headers, body, timeout):
            calls.append((method, url, headers))
            return 200, {"Content-Type": "image/png"}, b"image"

        client = HermesClient(api_url="https://hermes.example/v1", api_key="secret", transport=transport)
        self.assertEqual(client.download_result("https://cdn.example/result.png"), b"image")
        self.assertNotIn("Authorization", calls[0][2])

    def test_model_catalog_uses_configured_origin_and_authorization(self):
        calls = []

        def transport(method, url, headers, body, timeout):
            calls.append((method, url, headers, body, timeout))
            return 200, {"Content-Type": "application/json"}, b'{"data":[{"id":"image-v2"}]}'

        client = HermesClient(api_url="https://hermes.example/v1", api_key="secret", transport=transport)

        self.assertEqual(client.list_models(), {"data": [{"id": "image-v2"}]})
        self.assertEqual(calls[0][0:2], ("GET", "https://hermes.example/v1/models"))
        self.assertEqual(calls[0][2]["Authorization"], "Bearer secret")

    def test_default_transport_rejects_private_probe_target(self):
        client = HermesClient(api_url="https://127.0.0.1/v1", api_key="secret")
        with self.assertRaises(ValueError):
            client.test_connection()

    def test_oversized_local_image_is_downscaled_before_seedream_submit(self):
        calls = []

        def transport(method, url, headers, body, timeout):
            calls.append(json.loads(body.decode("utf-8")))
            return 200, {"Content-Type": "application/json"}, b'{"data":[{"b64_json":"aW1hZ2U="}]}'

        prepared = b"\xff\xd8\xff\xdbprepared-jpeg"
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "phone.png"
            source.write_bytes(_oversized_png_header())
            with mock.patch.object(HermesClient, "_transcode_local_image", return_value=(prepared, "image/jpeg")) as transcode:
                HermesClient(
                    api_url="https://ark.cn-beijing.volces.com/api/v3",
                    api_key="secret",
                    model="doubao-seedream-5-0-pro-260628",
                    transport=transport,
                ).submit_generation("prompt", image=str(source))

        transcode.assert_called_once()
        encoded = calls[0]["image"]
        self.assertTrue(encoded.startswith("data:image/jpeg;base64,"))
        self.assertEqual(base64.b64decode(encoded.split(",", 1)[1]), prepared)

    def test_oversized_local_image_without_ffmpeg_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "phone.png"
            source.write_bytes(_oversized_png_header())
            with mock.patch("providers.image.hermes_client.shutil.which", return_value=None):
                with self.assertRaises(HermesRequestError) as raised:
                    HermesClient(
                        api_url="https://ark.cn-beijing.volces.com/api/v3",
                        api_key="secret",
                        model="doubao-seedream-5-0-pro-260628",
                        transport=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("transport must not run")),
                    ).submit_generation("prompt", image=str(source))
        self.assertEqual(raised.exception.provider_code, "input_image_too_large")
        self.assertIsNone(raised.exception.status)


def _oversized_png_header() -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\rIHDR"
        + (6000).to_bytes(4, "big")
        + (4000).to_bytes(4, "big")
        + b"\x08\x02\x00\x00\x00"
    )


if __name__ == "__main__":
    unittest.main()
