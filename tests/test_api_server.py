"""Mocked tests for AIGC Studio's local web API."""

from __future__ import annotations

import shutil
import struct
import tempfile
import time
import unittest
import zlib
import os
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

import api_server
from providers.image.hermes_client import HermesRequestError
from skills.model_outfit_swap.runtime import FIXED_OUTFIT_PROMPT
from uploader.config import R2Config
from uploader.exceptions import R2ConfigurationError


class ApiServerTests(unittest.TestCase):
    def test_sensitive_provider_error_is_non_retryable_and_user_safe(self) -> None:
        error = HermesRequestError(
            "Hermes request failed (HTTP 400)",
            status=400,
            provider_code="InputImageSensitiveContentDetected",
            provider_message="input image rejected",
        )
        code, message, retryable = api_server._safe_message(error)
        self.assertEqual(code, "provider_input_rejected")
        self.assertFalse(retryable)
        self.assertIn("\u5185\u5bb9\u5b89\u5168", message)

    def test_other_client_4xx_is_non_retryable(self) -> None:
        error = HermesRequestError(
            "Hermes request failed (HTTP 400)",
            status=400,
            provider_code="InvalidParameter",
            provider_message="bad size",
        )
        code, _message, retryable = api_server._safe_message(error)
        self.assertEqual(code, "provider_request_invalid")
        self.assertFalse(retryable)

    def test_gpt_image_edit_access_error_is_actionable(self) -> None:
        error = HermesRequestError(
            "Hermes request failed (HTTP 502)",
            status=502,
            provider_code="upstream_error",
            provider_message="Upstream access forbidden",
        )
        code, message, retryable = api_server._safe_message(error)
        self.assertEqual(code, "provider_capability_unavailable")
        self.assertIn("切换 Hermes 火山", message)
        self.assertFalse(retryable)

    def setUp(self) -> None:
        self.client = TestClient(api_server.app)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.output_root = Path(self.temp_dir.name)
        self.images = self.output_root / "images"
        self.videos = self.output_root / "videos"
        self.shapewear = self.output_root / "shapewear"
        self.imported = self.output_root / "imported"
        for directory in (self.images, self.videos, self.shapewear, self.imported):
            directory.mkdir(parents=True)
        self.output_patch = patch.object(api_server, "OUTPUT_ROOTS", (self.images, self.videos, self.shapewear, self.imported))
        self.outputs_dir_patch = patch.object(api_server, "OUTPUTS_DIR", self.output_root)
        self.r2_store_patch = patch.object(api_server, "R2_UPLOAD_STORE_PATH", self.output_root / ".r2_uploads.json")
        self.hermes_store_patch = patch.object(api_server, "HERMES_SETTINGS_PATH", self.output_root / ".hermes.json")
        self.module_store_patch = patch.object(api_server, "MODULE_SETTINGS_PATH", self.output_root / ".modules.json")
        self.hermes_key_patch = patch.object(api_server, "HERMES_KEY_PATH", self.output_root / ".hermes.key")
        self.batch_store_patch = patch.object(api_server, "BATCH_STORE_PATH", self.output_root / ".batches.json")
        self.mix_store_patch = patch.object(api_server, "MIX_STORE_PATH", self.output_root / ".mixes.json")
        self.output_patch.start()
        self.outputs_dir_patch.start()
        self.r2_store_patch.start()
        self.hermes_store_patch.start()
        self.module_store_patch.start()
        self.hermes_key_patch.start()
        self.batch_store_patch.start()
        self.mix_store_patch.start()
        api_server.JOBS.clear()
        api_server.R2_UPLOADS.clear()
        api_server.BATCHES.clear()
        api_server.MIXES.clear()

    def tearDown(self) -> None:
        self.output_patch.stop()
        self.outputs_dir_patch.stop()
        self.r2_store_patch.stop()
        self.hermes_store_patch.stop()
        self.module_store_patch.stop()
        self.hermes_key_patch.stop()
        self.batch_store_patch.stop()
        self.mix_store_patch.stop()
        self.temp_dir.cleanup()

    @staticmethod
    def r2_config() -> R2Config:
        return R2Config(
            account_id="test-account",
            access_key_id="test-access",
            secret_access_key="test-secret",
            bucket_name="test-bucket",
            public_base_url="https://img.example.com",
        )

    def test_health_hides_configuration(self) -> None:
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_outfit_swap_preview_is_fixed_and_ignores_generation_brief(self) -> None:
        response = self.client.post(
            "/api/prompts/preview",
            json={
                "mode": "model_outfit_swap",
                "request": {
                    "prompt": "Use a different background and change the model identity",
                    "product": "untrusted product brief",
                },
            },
        )
        # Preview is intentionally independent from the editable generation
        # fields; only the reference-image contract is shown.
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["prompt"], FIXED_OUTFIT_PROMPT)
        self.assertNotIn("untrusted product brief", response.text)

    def test_outfit_swap_preview_accepts_empty_request(self) -> None:
        response = self.client.post("/api/prompts/preview", json={"mode": "model_outfit_swap", "request": {}})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["prompt"], FIXED_OUTFIT_PROMPT)

    def test_outfit_swap_preview_uses_selected_provider_prompt_policy(self) -> None:
        response = self.client.post(
            "/api/prompts/preview",
            json={"mode": "model_outfit_swap", "provider": "hermes_volcano", "request": {}},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("SEEDREAM IMAGE EDIT", response.json()["prompt"])
        self.assertNotEqual(response.json()["prompt"], FIXED_OUTFIT_PROMPT)

    def test_shapewear_preview_uses_selected_image_provider_prompt_policy(self) -> None:
        hermes = self.client.post(
            "/api/prompts/preview",
            json={
                "mode": "shapewear_image",
                "provider": "hermes",
                "request": {"product": "black shapewear", "_reference_attached": True},
            },
        )
        volcano = self.client.post(
            "/api/prompts/preview",
            json={
                "mode": "shapewear_image",
                "provider": "hermes_volcano",
                "request": {"product": "black shapewear", "_reference_attached": True},
            },
        )
        self.assertEqual(hermes.status_code, 200)
        self.assertEqual(volcano.status_code, 200)
        hermes_prompt = hermes.json()["prompt"]
        volcano_prompt = volcano.json()["prompt"]
        self.assertIn("GPT-IMAGE-2 SOURCE LOCK", hermes_prompt)
        self.assertNotIn("GPT-IMAGE-2 SOURCE LOCK", volcano_prompt)
        self.assertIn("PRODUCT SOURCE BINDING", volcano_prompt)

    def test_outfit_swap_request_requires_ordered_model_and_outfit_images(self) -> None:
        with self.assertRaisesRegex(ValueError, "model image"):
            api_server.GenerationRequest(mode="model_outfit_swap", provider="hermes", request={}, reference_images=[])
        with self.assertRaisesRegex(ValueError, "distinct"):
            api_server.GenerationRequest(
                mode="model_outfit_swap",
                provider="hermes",
                request={},
                reference_images=[
                    api_server.ReferenceImage(kind="url", value="https://cdn.example.com/model.png"),
                    api_server.ReferenceImage(kind="url", value="https://cdn.example.com/model.png"),
                ],
            )

    def test_outfit_swap_request_drops_legacy_generation_fields(self) -> None:
        request = api_server.GenerationRequest(
            mode="model_outfit_swap",
            provider="hermes",
            request={"product": "legacy", "prompt": "legacy prompt"},
            reference_images=[
                api_server.ReferenceImage(kind="url", value="https://cdn.example.com/model.png"),
                api_server.ReferenceImage(kind="url", value="https://cdn.example.com/outfit.png"),
            ],
        )
        self.assertEqual(request.request, {})

    def test_model_outfit_swap_accepts_both_image_providers(self) -> None:
        for provider in ("hermes", "hermes_volcano", "liblib"):
            with self.subTest(provider=provider):
                payload = api_server.GenerationRequest(
                    mode="model_outfit_swap",
                    provider=provider,
                    reference_images=[
                        api_server.ReferenceImage(kind="url", value="https://cdn.example.com/model.png"),
                        api_server.ReferenceImage(kind="url", value="https://cdn.example.com/outfit.png"),
                    ],
                )
                self.assertEqual(payload.provider, "hermes_volcano" if provider == "liblib" else provider)

    def test_shapewear_product_image_selects_and_defaults_provider(self) -> None:
        payload = api_server.GenerationRequest(mode="shapewear_image", request={})
        self.assertEqual(payload.provider, "hermes")
        self.assertEqual(api_server._provider_for_job(payload), "hermes")
        selected = api_server.GenerationRequest(mode="shapewear_image", provider="hermes_volcano", request={})
        self.assertEqual(selected.provider, "hermes_volcano")
        self.assertEqual(api_server._provider_for_job(selected), "hermes_volcano")
        legacy_alias = api_server.GenerationRequest(mode="shapewear_image", provider="liblib", request={})
        self.assertEqual(legacy_alias.provider, "hermes_volcano")
        with self.assertRaisesRegex(ValueError, "at most 10"):
            api_server.GenerationRequest(
                mode="shapewear_image",
                request={},
                reference_images=[
                    api_server.ReferenceImage(kind="url", value=f"https://cdn.example.com/shapewear-{index}.png")
                    for index in range(11)
                ],
            )
        one_reference = api_server.GenerationRequest(
            mode="shapewear_image",
            request={},
            reference_images=[
                api_server.ReferenceImage(kind="url", value="https://cdn.example.com/shapewear-product.png")
            ],
        )
        self.assertEqual(len(one_reference.reference_images), 1)

    def test_shapewear_image_falls_back_on_quota_error(self) -> None:
        payload = api_server.GenerationRequest(mode="shapewear_image", provider="hermes", request={})
        job = api_server.GenerationJob(id="shapewear-fallback", payload=payload)
        fallback_manifest = {
            "prompt": "prompt",
            "outputs": [str(self.shapewear / "result.png")],
            "quality": {"passed": True},
        }
        with patch.object(
            api_server,
            "generate_shapewear_image",
            side_effect=[RuntimeError("Hermes request failed (HTTP 429): quota exceeded"), fallback_manifest],
        ) as generate_skill, patch.object(
            api_server,
            "_image_client_for_provider",
            side_effect=[object(), object()],
        ), patch.object(api_server, "_provider_available", return_value=(True, None)):
            manifest, provider = api_server._generate_shapewear_image_with_fallback(
                job,
                {},
                image=None,
                references=None,
                output_dir=self.shapewear,
            )
        self.assertEqual(provider, "hermes_volcano")
        self.assertEqual(manifest["provider"], "hermes_volcano")
        self.assertEqual(manifest["provider_fallback"]["from"], "hermes")
        self.assertEqual([call.kwargs["provider"] for call in generate_skill.call_args_list], ["hermes", "hermes_volcano"])

    def test_shapewear_image_does_not_fallback_on_validation_error(self) -> None:
        payload = api_server.GenerationRequest(mode="shapewear_image", provider="hermes", request={})
        job = api_server.GenerationJob(id="shapewear-no-fallback", payload=payload)
        with patch.object(
            api_server,
            "generate_shapewear_image",
            side_effect=api_server.ShapewearRequestError("shapewear product details cannot be recolored"),
        ) as generate_skill, patch.object(api_server, "_provider_available") as provider_available, patch.object(
            api_server, "_image_client_for_provider", return_value=object()
        ):
            with self.assertRaises(api_server.ShapewearRequestError):
                api_server._generate_shapewear_image_with_fallback(
                    job,
                    {},
                    image=None,
                    references=None,
                    output_dir=self.shapewear,
                )
        generate_skill.assert_called_once()
        provider_available.assert_not_called()

    def test_tiktok_image_falls_back_when_gpt_image_edit_is_unavailable(self) -> None:
        payload = api_server.GenerationRequest(
            mode="tiktok_clothing_image",
            provider="hermes",
            request={"purpose": "shop_listing", "style": "studio_detail"},
            reference_images=[
                api_server.ReferenceImage(kind="url", value="https://cdn.example.com/master.png"),
            ],
        )
        job = api_server.GenerationJob(id="tiktok-fallback", payload=payload)
        fallback_manifest = {
            "prompt": "prompt",
            "outputs": [str(self.shapewear / "result.png")],
            "quality": {"passed": True},
        }
        upstream_error = HermesRequestError(
            "Hermes request failed (HTTP 502): upstream_error: Upstream access forbidden",
            status=502,
            provider_code="upstream_error",
            provider_message="Upstream access forbidden",
        )
        with patch.object(
            api_server,
            "generate_tiktok_clothing_image",
            side_effect=[upstream_error, fallback_manifest],
        ) as generate_skill, patch.object(
            api_server,
            "_image_client_for_provider",
            side_effect=[object(), object()],
        ), patch.object(api_server, "_provider_available", return_value=(True, None)):
            manifest, provider = api_server._generate_tiktok_clothing_image_with_fallback(
                job,
                {},
                product_images=["master.png"],
                output_dir=self.shapewear,
            )

        self.assertEqual(provider, "hermes_volcano")
        self.assertEqual(manifest["provider"], "hermes_volcano")
        self.assertEqual(manifest["provider_fallback"]["reason"], "reference_edit_unavailable")
        self.assertEqual(
            [call.kwargs["provider"] for call in generate_skill.call_args_list],
            ["hermes", "hermes_volcano"],
        )

    def test_tiktok_image_does_not_fallback_on_reference_validation_error(self) -> None:
        payload = api_server.GenerationRequest(
            mode="tiktok_clothing_image",
            provider="hermes",
            request={"purpose": "shop_listing", "style": "studio_detail"},
            reference_images=[
                api_server.ReferenceImage(kind="url", value="https://cdn.example.com/master.png"),
            ],
        )
        job = api_server.GenerationJob(id="tiktok-no-fallback", payload=payload)
        error = HermesRequestError(
            "Hermes request failed (HTTP 400): InvalidParameter: bad image",
            status=400,
            provider_code="InvalidParameter",
            provider_message="bad image",
        )
        with patch.object(api_server, "generate_tiktok_clothing_image", side_effect=error) as generate_skill, patch.object(
            api_server, "_provider_available"
        ) as provider_available, patch.object(api_server, "_image_client_for_provider", return_value=object()):
            with self.assertRaises(HermesRequestError):
                api_server._generate_tiktok_clothing_image_with_fallback(
                    job,
                    {},
                    product_images=["master.png"],
                    output_dir=self.shapewear,
                )
        generate_skill.assert_called_once()
        provider_available.assert_not_called()

    def test_hermes_settings_are_write_only_for_api_key(self) -> None:
        response = self.client.put(
            "/api/settings/hermes",
            json={
                "api_url": "https://8.8.8.8/v1",
                "api_key": "super-secret-hermes-key",
                "model": "image-model",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["api_key_configured"])
        self.assertNotIn("api_key", response.json())
        self.assertNotIn("super-secret-hermes-key", response.text)

        loaded = self.client.get("/api/settings/hermes")
        self.assertTrue(loaded.json()["api_key_configured"])
        self.assertNotIn("super-secret-hermes-key", loaded.text)
        self.assertNotIn("super-secret-hermes-key", api_server.MODULE_SETTINGS_PATH.read_text(encoding="utf-8"))
        self.assertEqual(api_server._module_settings("image.hermes", include_key=True)["api_key"], "super-secret-hermes-key")

    def test_module_registry_is_redacted_and_legacy_hermes_shares_record(self) -> None:
        saved = self.client.put(
            "/api/settings/modules/image.hermes",
            json={
                "api_url": "https://8.8.8.8/v1",
                "api_key": "shared-secret",
                "model": "shared-model",
                "enabled": True,
                "options": {"submit_path": "/submit", "status_path": "/status/{task_id}", "result_path": "/result/{task_id}"},
            },
        )
        self.assertEqual(saved.status_code, 200)
        registry = self.client.get("/api/settings/modules")
        self.assertEqual(registry.status_code, 200)
        self.assertEqual({item["id"] for item in registry.json()["modules"]}, set(api_server.MODULE_SPECS))
        self.assertNotIn('"api_key":', registry.text)
        self.assertNotIn("shared-secret", registry.text)
        legacy = self.client.get("/api/settings/hermes")
        self.assertEqual(legacy.json()["model"], "shared-model")
        self.assertEqual(legacy.json()["submit_path"], "/submit")
        self.assertTrue(legacy.json()["api_key_configured"])

    def test_hermes_timeout_is_configurable_and_bounded(self) -> None:
        saved = self.client.put(
            "/api/settings/modules/image.hermes",
            json={
                "api_url": "https://8.8.8.8/v1",
                "api_key": "shared-secret",
                "model": "image-model",
                "options": {"timeout": "240"},
            },
        )
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json()["options"]["timeout"], "240")
        self.assertEqual(
            api_server._module_settings("image.hermes", include_key=True)["options"]["timeout"],
            "240",
        )

        for timeout in ("0", "601", "slow"):
            with self.subTest(timeout=timeout):
                response = self.client.put(
                    "/api/settings/modules/image.hermes",
                    json={
                        "api_url": "https://8.8.8.8/v1",
                        "model": "image-model",
                        "options": {"timeout": timeout},
                    },
                )
                self.assertEqual(response.status_code, 400)

    @patch("api_server.socket.getaddrinfo")
    def test_module_settings_reject_private_and_credential_url_channels(self, getaddrinfo) -> None:
        getaddrinfo.return_value = [(2, 1, 6, "", ("10.0.0.8", 443))]
        dns_private = self.client.put("/api/settings/modules/mix.codex_terra", json={"api_url": "https://planner.example/v1", "api_key": "key", "model": "gpt-5.6-terra"})
        self.assertEqual(dns_private.status_code, 400)
        for url in ("http://127.0.0.1/v1", "https://user:pass@example.test/v1", "https://example.test/v1?api_key=key", "https://example.test/v1#token"):
            with self.subTest(url=url):
                response = self.client.put("/api/settings/modules/image.liblib", json={"api_url": url, "api_key": "key"})
                self.assertEqual(response.status_code, 400)

    @patch("api_server.GoogleVeoClient.test_connection", return_value={"ok": True})
    def test_module_test_does_not_persist_draft_key(self, _probe) -> None:
        response = self.client.post("/api/settings/modules/video.veo/test", json={"api_url": "https://8.8.8.8/v1", "api_key": "draft-secret", "model": "veo"})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("draft-secret", response.text)
        self.assertFalse(api_server.MODULE_SETTINGS_PATH.exists())
        _probe.assert_called_once()

    def test_each_module_test_probes_provider_without_persisting(self) -> None:
        cases = [
            ("image.hermes", "api_server.HermesClient.test_connection"),
            ("image.liblib", "api_server.LiblibClient.test_connection"),
            ("video.veo", "api_server.GoogleVeoClient.test_connection"),
            ("video.seedance", "api_server.SeedanceClient.test_connection"),
            ("mix.codex_terra", "api_server.CodexTerraPlanner.test_connection"),
        ]
        for module_id, probe_path in cases:
            with self.subTest(module_id=module_id), patch(probe_path, return_value={"ok": True}) as probe:
                response = self.client.post(
                    f"/api/settings/modules/{module_id}/test",
                    json={"api_url": "https://8.8.8.8/v1", "api_key": "draft-secret", "model": "test-model"},
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["module_id"], module_id)
                probe.assert_called_once()
        self.assertFalse(api_server.MODULE_SETTINGS_PATH.exists())

    def test_each_module_can_discover_models_without_persisting_draft_key(self) -> None:
        cases = [
            ("image.hermes", "api_server.HermesClient.list_models"),
            ("image.liblib", "api_server.LiblibClient.list_models"),
            ("video.veo", "api_server.GoogleVeoClient.list_models"),
            ("video.seedance", "api_server.SeedanceClient.list_models"),
            ("mix.codex_terra", "api_server.CodexTerraPlanner.list_models"),
        ]
        for module_id, probe_path in cases:
            with self.subTest(module_id=module_id), patch(
                probe_path,
                return_value={"data": [{"id": "model-a", "name": "Model A"}]},
            ) as probe:
                response = self.client.post(
                    f"/api/settings/modules/{module_id}/models",
                    json={
                        "api_url": "https://8.8.8.8/v1",
                        "api_key": "draft-secret",
                        "model": "model-a",
                    },
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["models"], [{"id": "model-a", "name": "Model A"}])
                self.assertEqual(response.json()["selected_model"], "model-a")
                self.assertTrue(response.json()["manual_entry"])
                self.assertNotIn("draft-secret", response.text)
                probe.assert_called_once()
        self.assertFalse(api_server.MODULE_SETTINGS_PATH.exists())

    def test_model_discovery_normalizes_google_and_generic_shapes(self) -> None:
        self.assertEqual(
            api_server._normalize_provider_models(
                {"models": [{"name": "models/veo-fast", "displayName": "Veo Fast"}]}
            ),
            [{"id": "veo-fast", "name": "Veo Fast"}],
        )
        self.assertEqual(
            api_server._normalize_provider_models({"items": ["image-v2", {"modelId": "video-v3"}]}),
            [{"id": "image-v2", "name": "image-v2"}, {"id": "video-v3", "name": "video-v3"}],
        )

    @patch("api_server.LiblibClient.list_models", side_effect=api_server.LiblibClientError("provider leaked secret-value"))
    def test_model_discovery_failure_is_sanitized_and_keeps_manual_fallback(self, _probe) -> None:
        response = self.client.post(
            "/api/settings/modules/image.liblib/models",
            json={
                "api_url": "https://8.8.8.8/v1",
                "api_key": "draft-secret",
                "model": "manual-model",
            },
        )
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["detail"]["code"], "model_discovery_failed")
        self.assertIn("manually", response.json()["detail"]["message"])
        self.assertNotIn("draft-secret", response.text)
        self.assertNotIn("secret-value", response.text)
        self.assertFalse(api_server.MODULE_SETTINGS_PATH.exists())

    def test_module_clear_key_removes_process_value(self) -> None:
        with patch.dict(os.environ, {"CODEX_TERRA_API_KEY": "old-key"}, clear=False):
            saved = self.client.put(
                "/api/settings/modules/mix.codex_terra",
                json={"api_url": "https://8.8.8.8/v1", "api_key": "new-key", "model": "gpt-5.6-terra"},
            )
            self.assertEqual(saved.status_code, 200)
            self.assertEqual(os.environ["CODEX_TERRA_API_KEY"], "new-key")

            cleared = self.client.put(
                "/api/settings/modules/mix.codex_terra",
                json={"api_url": "https://8.8.8.8/v1", "clear_api_key": True, "model": "gpt-5.6-terra"},
            )
            self.assertEqual(cleared.status_code, 200)
            self.assertFalse(cleared.json()["api_key_configured"])
            self.assertNotIn("CODEX_TERRA_API_KEY", os.environ)

    def test_persisted_provider_settings_are_restored_on_startup(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            saved = self.client.put(
                "/api/settings/modules/video.seedance",
                json={"api_url": "https://8.8.8.8/v1", "api_key": "persisted-key", "model": "seedance-custom"},
            )
            self.assertEqual(saved.status_code, 200)
            for name in ("SEEDANCE_API_URL", "SEEDANCE_API_KEY", "SEEDANCE_MODEL", "SEEDANCE_TEXT_MODEL", "SEEDANCE_IMAGE_MODEL"):
                os.environ.pop(name, None)

            api_server._restore_module_environments()

            self.assertEqual(os.environ["SEEDANCE_API_URL"], "https://8.8.8.8/v1")
            self.assertEqual(os.environ["SEEDANCE_API_KEY"], "persisted-key")
            self.assertEqual(os.environ["SEEDANCE_TEXT_MODEL"], "seedance-custom")
            self.assertEqual(os.environ["SEEDANCE_IMAGE_MODEL"], "seedance-custom")

    def test_media_import_copies_supported_files_into_managed_root(self) -> None:
        response = self.client.post(
            "/api/media/import",
            files=[("files", ("photo.png", b"\x89PNG\r\n\x1a\npng-data", "image/png"))],
            data={"relative_paths": ["folder/photo.png"]},
        )
        self.assertEqual(response.status_code, 200)
        assets = response.json()["assets"]
        self.assertEqual(len(assets), 1)
        self.assertTrue(assets[0]["id"].startswith("imported/"))
        self.assertEqual(assets[0]["source"], "local")
        self.assertNotIn(str(self.output_root), response.text)
        self.assertEqual(list(self.imported.rglob("*.txt")), [])

    def test_media_import_rejects_invalid_batch_without_partial_files(self) -> None:
        response = self.client.post(
            "/api/media/import",
            files=[
                ("files", ("photo.png", b"\x89PNG\r\n\x1a\npng-data", "image/png")),
                ("files", ("empty.png", b"", "image/png")),
            ],
            data={"relative_paths": ["folder/photo.png", "folder/empty.png"]},
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(list(self.imported.rglob("*")), [])

    def test_mix_rejects_reversed_trim_range(self) -> None:
        response = self.client.post(
            "/api/mixes",
            json={
                "clips": [
                    {"asset_id": "videos/a.mp4", "start_ms": 5000, "end_ms": 1000},
                    {"asset_id": "videos/b.mp4"},
                ]
            },
        )
        self.assertEqual(response.status_code, 422)

    @patch("api_server._terra_planner", return_value=None)
    def test_mix_plan_uses_local_fallback_and_selected_assets_only(self, _planner) -> None:
        first = self.images / "first.png"
        second = self.images / "second.png"
        first.write_bytes(b"image")
        second.write_bytes(b"image")
        response = self.client.post(
            "/api/mixes/plan",
            json={
                "objective": "突出新品细节",
                "target_duration_ms": 4000,
                "transition_mode": "fade",
                "clips": [
                    {"asset_id": "images/first.png"},
                    {"asset_id": "images/second.png"},
                ],
            },
        )
        self.assertEqual(response.status_code, 200)
        plan = response.json()
        self.assertEqual(plan["planner"], "local")
        self.assertEqual(plan["target_duration_ms"], 4000)
        self.assertEqual([clip["asset_id"] for clip in plan["clips"]], ["images/first.png", "images/second.png"])
        self.assertTrue(all(clip["transition"] == "hard_cut" for clip in plan["clips"]))
        self.assertTrue(plan["warnings"])

    @patch("api_server.MIX_EXECUTOR.submit")
    @patch("api_server._terra_planner", return_value=None)
    def test_mix_auto_plan_persists_validated_plan(self, _planner, submit) -> None:
        first = self.images / "first.png"
        second = self.images / "second.png"
        first.write_bytes(b"image")
        second.write_bytes(b"image")
        response = self.client.post(
            "/api/mixes",
            json={
                "auto_plan": True,
                "objective": "产品展示",
                "target_duration_ms": 6000,
                "clips": [{"asset_id": "images/first.png"}, {"asset_id": "images/second.png"}],
            },
        )
        self.assertEqual(response.status_code, 202)
        mix = api_server.MIXES[response.json()["mix_id"]]
        self.assertEqual(mix["plan"]["planner"], "local")
        self.assertEqual(mix["plan"]["target_duration_ms"], 6000)
        submit.assert_called_once()

    def test_mix_rejects_plan_with_unselected_asset(self) -> None:
        response = self.client.post(
            "/api/mixes",
            json={
                "clips": [{"asset_id": "images/first.png"}, {"asset_id": "images/second.png"}],
                "plan": {
                    "target_duration_ms": 2000,
                    "clips": [{"asset_id": "images/first.png", "duration_ms": 1000}, {"asset_id": "images/other.png", "duration_ms": 1000}],
                },
            },
        )
        self.assertEqual(response.status_code, 422)

    def test_terra_malformed_envelope_timeout_and_invalid_duration_fall_back(self) -> None:
        request = api_server.MixPlanRequest(clips=[api_server.MixClip(asset_id="images/first.png"), api_server.MixClip(asset_id="images/second.png")], target_duration_ms=4000)
        malformed = Mock()
        malformed.plan.return_value = {"clips": []}
        with patch("api_server._terra_planner", return_value=malformed):
            self.assertEqual(api_server._create_mix_plan(request).planner, "local")
        timeout = Mock()
        timeout.plan.side_effect = api_server.TerraPlannerError("timeout upstream-secret")
        with patch("api_server._terra_planner", return_value=timeout):
            self.assertEqual(api_server._create_mix_plan(request).planner, "local")
        invalid_duration = Mock()
        invalid_duration.plan.return_value = {
            "version": "aigc-mix-plan/v1", "planner": "codex_terra", "objective": "test", "target_duration_ms": 4000, "warnings": [],
            "clips": [{"asset_id": "images/first.png", "duration_ms": 70000}, {"asset_id": "images/second.png", "duration_ms": 1000}],
        }
        with patch("api_server._terra_planner", return_value=invalid_duration):
            self.assertEqual(api_server._create_mix_plan(request).planner, "local")

    def test_terra_complete_valid_envelope_is_accepted(self) -> None:
        request = api_server.MixPlanRequest(clips=[api_server.MixClip(asset_id="images/first.png"), api_server.MixClip(asset_id="images/second.png")], target_duration_ms=4000)
        planner = Mock()
        planner.plan.return_value = {
            "version": "aigc-mix-plan/v1", "planner": "codex_terra", "objective": "product edit", "target_duration_ms": 4000, "warnings": [],
            "clips": [{"asset_id": "images/first.png", "duration_ms": 2000, "transition": "hard_cut"}, {"asset_id": "images/second.png", "duration_ms": 2000, "transition": "hard_cut"}],
        }
        with patch("api_server._terra_planner", return_value=planner):
            plan = api_server._create_mix_plan(request)
        self.assertEqual(plan.planner, "codex_terra")
        self.assertEqual(plan.target_duration_ms, 4000)

    def test_mix_failure_redacts_local_ffmpeg_details(self) -> None:
        first = self.images / "first.png"
        second = self.images / "second.png"
        first.write_bytes(b"image")
        second.write_bytes(b"image")
        api_server.MIXES["redacted"] = {"id": "redacted", "status": "queued", "phase": "waiting"}
        request = api_server.MixRequest(clips=[api_server.MixClip(asset_id="images/first.png", duration_ms=500), api_server.MixClip(asset_id="images/second.png", duration_ms=500)])
        with patch("api_server.shutil.which", return_value="ffmpeg"), patch("api_server._run_ffmpeg", side_effect=RuntimeError(r"C:\private\source.mp4 --token secret")):
            api_server._run_mix("redacted", request)
        result = api_server.MIXES["redacted"]
        self.assertEqual(result["status"], "failed")
        self.assertNotIn("private", result["error"])
        self.assertNotIn("secret", result["error"])

    @patch("api_server.BATCH_EXECUTOR.submit")
    @patch("api_server._provider_available", return_value=(True, None))
    def test_batch_idempotency_is_atomic(self, _available, submit) -> None:
        payload = {
            "idempotency_key": "stable-key",
            "options": {"max_concurrency": 4},
            "items": [{"client_id": "row-1", "prompt": "studio image"}],
        }
        first = self.client.post("/api/generation-batches", json=payload)
        second = self.client.post("/api/generation-batches", json=payload)
        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 202)
        self.assertEqual(first.json()["batch_id"], second.json()["batch_id"])
        self.assertEqual(api_server.BATCHES[first.json()["batch_id"]]["max_concurrency"], 4)
        submit.assert_called_once()

    def test_batch_rejects_concurrency_above_limit(self) -> None:
        response = self.client.post(
            "/api/generation-batches",
            json={"options": {"max_concurrency": 5}, "items": [{"client_id": "row-1", "prompt": "studio image"}]},
        )
        self.assertEqual(response.status_code, 422)

    def test_interrupted_batch_is_restored_as_failed(self) -> None:
        api_server.BATCHES["batch-1"] = {
            "id": "batch-1",
            "status": "running",
            "items": [{"client_id": "row-1", "prompt": "image", "status": "running"}],
            "created_at": api_server._now(),
            "updated_at": api_server._now(),
        }
        api_server._save_batches()
        api_server.BATCHES.clear()
        api_server._load_batches()
        self.assertEqual(api_server.BATCHES["batch-1"]["status"], "failed")
        self.assertEqual(api_server.BATCHES["batch-1"]["items"][0]["status"], "failed")

    @patch("api_server._hermes_client", return_value=object())
    @patch("api_server._provider_available", return_value=(True, None))
    def test_batch_keeps_successful_rows_when_another_fails(self, _available, _client) -> None:
        def fake_generation(request, **_kwargs):
            if request["prompt"] == "fail":
                raise RuntimeError("provider failed")
            output = self.images / f"{request['prompt']}.png"
            output.write_bytes(b"image")
            return str(output)

        with patch("api_server.generate_image", side_effect=fake_generation), patch.object(
            api_server.BATCH_EXECUTOR, "submit", side_effect=lambda function, *args: function(*args)
        ):
            response = self.client.post(
                "/api/generation-batches",
                json={
                    "options": {"max_concurrency": 2},
                    "items": [
                        {"client_id": "row-1", "prompt": "success"},
                        {"client_id": "row-2", "prompt": "fail"},
                    ],
                },
            )

        batch = self.client.get(f"/api/generation-batches/{response.json()['batch_id']}").json()
        self.assertEqual(batch["status"], "partial")
        self.assertEqual({item["status"] for item in batch["items"]}, {"succeeded", "failed"})
        self.assertTrue(api_server.BATCH_STORE_PATH.is_file())

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is not installed")
    def test_mix_creates_mp4_without_changing_sources(self) -> None:
        def chunk(kind: bytes, data: bytes) -> bytes:
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

        raw = b"\x00\xff\x00\x00\xff\x00\xff" * 2
        pixel = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
        first = self.images / "first.png"
        second = self.images / "second.png"
        first.write_bytes(pixel)
        second.write_bytes(pixel)

        with patch.object(api_server.MIX_EXECUTOR, "submit", side_effect=lambda function, *args: function(*args)):
            response = self.client.post(
                "/api/mixes",
                json={
                    "aspect_ratio": "square",
                    "clips": [
                        {"asset_id": "images/first.png", "duration_ms": 500},
                        {"asset_id": "images/second.png", "duration_ms": 500},
                    ],
                },
            )

        self.assertEqual(response.status_code, 202)
        mix = self.client.get(f"/api/mixes/{response.json()['mix_id']}").json()
        self.assertEqual(mix["status"], "succeeded")
        video = self.client.get(mix["output"])
        self.assertEqual(video.status_code, 200)
        self.assertEqual(video.content[4:8], b"ftyp")
        self.assertEqual(first.read_bytes(), pixel)
        self.assertEqual(second.read_bytes(), pixel)

    @patch("api_server.R2Config.from_env", side_effect=R2ConfigurationError("missing test-secret"))
    def test_r2_status_reports_unavailable_without_configuration_details(self, _config) -> None:
        response = self.client.get("/api/storage/r2")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"available": False, "message": "Cloudflare R2 尚未完成本地配置"},
        )
        self.assertNotIn("test-secret", response.text)

    @patch("api_server.R2Config.from_env")
    def test_r2_status_exposes_only_public_origin(self, config_from_env) -> None:
        config_from_env.return_value = self.r2_config()

        response = self.client.get("/api/storage/r2")

        self.assertEqual(
            response.json(),
            {"available": True, "public_base_url": "https://img.example.com"},
        )
        self.assertNotIn("test-access", response.text)
        self.assertNotIn("test-secret", response.text)

    def test_seedance_accepts_local_reference_for_provider_upload(self) -> None:
        response = self.client.post(
            "/api/generations",
            json={
                "mode": "video",
                "provider": "seedance",
                "request": {"prompt": "a product film"},
                "reference_image": {"kind": "asset", "value": "images/example.png"},
            },
        )
        # Provider availability is checked after local references are resolved;
        # a valid asset therefore reaches the provider availability boundary.
        self.assertIn(response.status_code, {202, 503})

        private_response = self.client.post(
            "/api/generations",
            json={
                "mode": "video",
                "provider": "seedance",
                "request": {"prompt": "a product film"},
                "reference_image": {"kind": "url", "value": "https://127.0.0.1/product.png"},
            },
        )
        self.assertEqual(private_response.status_code, 422)

    def test_veo_rejects_non_public_remote_reference_images(self) -> None:
        for reference_url in (
            "http://127.0.0.1/product.png",
            "https://10.0.0.10/product.png",
            "https://localhost/product.png",
            "https://metadata.google.internal/product.png",
        ):
            with self.subTest(reference_url=reference_url):
                response = self.client.post(
                    "/api/generations",
                    json={
                        "mode": "video",
                        "provider": "veo",
                        "request": {"prompt": "a product film"},
                        "reference_image": {"kind": "url", "value": reference_url},
                    },
                )
                self.assertEqual(response.status_code, 422)

    def test_asset_endpoint_is_rooted_to_outputs(self) -> None:
        artifact = self.images / "result.png"
        artifact.write_bytes(b"image-bytes")
        response = self.client.get("/api/assets/images/result.png")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"image-bytes")
        traversal = self.client.get("/api/assets/images/../../config.py")
        self.assertEqual(traversal.status_code, 404)

    @patch("api_server.upload_r2_image", return_value="https://img.example.com/images/mock%20result.png")
    @patch("api_server.R2Config.from_env")
    def test_upload_image_to_r2_updates_asset_metadata(self, config_from_env, upload_image) -> None:
        config_from_env.return_value = self.r2_config()
        artifact = self.images / "mock result.png"
        artifact.write_bytes(b"image-bytes")

        response = self.client.post("/api/storage/r2/assets/images/mock%20result.png", json={})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["url"], "https://img.example.com/images/mock%20result.png")
        self.assertEqual(response.json()["object_key"], "images/mock result.png")
        upload_image.assert_called_once_with(artifact, None, overwrite=False)
        listed = self.client.get("/api/assets").json()["assets"][0]
        self.assertEqual(listed["r2_url"], response.json()["url"])
        self.assertEqual(listed["r2_object_key"], "images/mock result.png")

    @patch("api_server.upload_r2_video", return_value="https://img.example.com/videos/mock.mp4")
    @patch("api_server.R2Config.from_env")
    def test_upload_video_to_r2(self, config_from_env, upload_video) -> None:
        config_from_env.return_value = self.r2_config()
        artifact = self.videos / "mock.mp4"
        artifact.write_bytes(b"video-bytes")

        response = self.client.post("/api/storage/r2/assets/videos/mock.mp4", json={})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["url"], "https://img.example.com/videos/mock.mp4")
        upload_video.assert_called_once_with(artifact, None, overwrite=False)

    @patch("api_server.upload_r2_image", side_effect=R2ConfigurationError("missing test-secret"))
    def test_r2_upload_unavailable_does_not_leak_configuration(self, _upload_image) -> None:
        artifact = self.images / "mock.png"
        artifact.write_bytes(b"image-bytes")

        response = self.client.post("/api/storage/r2/assets/images/mock.png", json={})

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"]["code"], "r2_unavailable")
        self.assertNotIn("test-secret", response.text)

    def test_r2_upload_rejects_missing_and_unsupported_assets(self) -> None:
        missing = self.client.post("/api/storage/r2/assets/images/missing.png", json={})
        self.assertEqual(missing.status_code, 404)

        unsupported = self.videos / "legacy.m4v"
        unsupported.write_bytes(b"video-bytes")
        response = self.client.post("/api/storage/r2/assets/videos/legacy.m4v", json={})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"]["code"], "r2_validation_error")

    @patch("api_server.upload_r2_image", return_value="https://img.example.com/images/mock.png")
    @patch("api_server.R2Config.from_env")
    def test_changed_local_asset_invalidates_r2_metadata(self, config_from_env, _upload_image) -> None:
        config_from_env.return_value = self.r2_config()
        artifact = self.images / "mock.png"
        artifact.write_bytes(b"first-image")
        response = self.client.post("/api/storage/r2/assets/images/mock.png", json={})
        self.assertEqual(response.status_code, 200)

        artifact.write_bytes(b"changed-image-content")
        listed = self.client.get("/api/assets").json()["assets"][0]
        self.assertIsNone(listed["r2_url"])
        self.assertIsNone(listed["r2_object_key"])

    @patch("api_server._provider_available", return_value=(True, None))
    @patch("api_server.JOB_EXECUTOR.submit")
    def test_generation_returns_job_id_without_secret(self, submit, _available) -> None:
        response = self.client.post(
            "/api/generations",
            json={"mode": "image", "request": {"prompt": "black shapewear product photo"}},
        )
        self.assertEqual(response.status_code, 202)
        body = response.json()
        self.assertEqual(body["status"], "queued")
        self.assertIn("job_id", body)
        self.assertNotIn("key", str(body).lower())
        submit.assert_called_once()

    @patch("api_server._provider_available", return_value=(True, None))
    @patch("api_server.JOB_EXECUTOR.submit")
    def test_model_outfit_swap_accepts_ordered_reference_images(self, submit, _available) -> None:
        (self.images / "model.png").write_bytes(b"model")
        (self.images / "outfit.png").write_bytes(b"outfit")
        response = self.client.post(
            "/api/generations",
            json={
                "mode": "model_outfit_swap",
                "provider": "hermes",
                "request": {"product": "red jacket", "scene": "studio", "style": "catalog"},
                "reference_images": [
                    {"kind": "asset", "value": "images/model.png"},
                    {"kind": "asset", "value": "images/outfit.png"},
                ],
            },
        )
        self.assertEqual(response.status_code, 202)
        submit.assert_called_once()

    def test_model_outfit_swap_rejects_single_reference_image(self) -> None:
        response = self.client.post(
            "/api/generations",
            json={
                "mode": "model_outfit_swap",
                "provider": "hermes",
                "request": {"product": "red jacket", "scene": "studio", "style": "catalog"},
                "reference_images": [{"kind": "url", "value": "https://cdn.example.com/model.png"}],
            },
        )
        self.assertEqual(response.status_code, 422)

    def test_model_outfit_swap_rejects_more_than_hermes_reference_limit(self) -> None:
        response = self.client.post(
            "/api/generations",
            json={
                "mode": "model_outfit_swap",
                "provider": "hermes",
                "reference_images": [
                    {"kind": "url", "value": f"https://cdn.example.com/image-{index}.png"}
                    for index in range(11)
                ],
            },
        )
        self.assertEqual(response.status_code, 422)

    @patch("api_server._provider_available", return_value=(True, None))
    @patch("api_server.JOB_EXECUTOR.submit")
    def test_clothing_image_to_image_accepts_exactly_one_reference(self, submit, _available) -> None:
        (self.images / "garment.png").write_bytes(b"garment")
        response = self.client.post(
            "/api/generations",
            json={
                "mode": "clothing_image_to_image",
                "provider": "hermes",
                "request": {"objective": "show the fabric weave and seam construction"},
                "reference_images": [{"kind": "asset", "value": "images/garment.png"}],
            },
        )
        self.assertEqual(response.status_code, 202)
        submit.assert_called_once()

    def test_clothing_image_to_image_defaults_to_hermes(self) -> None:
        garment = self.images / "garment.png"
        garment.write_bytes(b"garment")
        payload = api_server.GenerationRequest(
            mode="clothing_image_to_image",
            request={"objective": "show fabric detail"},
            reference_images=[api_server.ReferenceImage(kind="asset", value="images/garment.png")],
        )
        self.assertEqual(payload.provider, "hermes")
        self.assertEqual(api_server._provider_for_job(payload), "hermes")

    @patch("api_server._provider_available", return_value=(True, None))
    @patch("api_server.JOB_EXECUTOR.submit")
    def test_clothing_api_request_without_provider_queues_hermes_job(self, submit, _available) -> None:
        garment = self.images / "garment.png"
        garment.write_bytes(b"garment")
        response = self.client.post(
            "/api/generations",
            json={
                "mode": "clothing_image_to_image",
                "request": {"objective": "show fabric detail"},
                "reference_images": [{"kind": "asset", "value": "images/garment.png"}],
            },
        )
        self.assertEqual(response.status_code, 202)
        job = api_server.JOBS[response.json()["job_id"]]
        self.assertEqual(job.payload.provider, "hermes")
        self.assertEqual(api_server._provider_for_job(job.payload), "hermes")
        submit.assert_called_once()

    def test_clothing_image_to_image_rejects_non_hermes_provider(self) -> None:
        with self.assertRaisesRegex(ValueError, "Hermes"):
            api_server.GenerationRequest(
                mode="clothing_image_to_image",
                provider="liblib",
                request={"objective": "show fabric detail"},
                reference_images=[api_server.ReferenceImage(kind="url", value="https://cdn.example.com/garment.png")],
            )

    def test_clothing_image_to_image_rejects_zero_or_multiple_references(self) -> None:
        for references in ([], [{"kind": "url", "value": "https://cdn.example.com/a.png"}, {"kind": "url", "value": "https://cdn.example.com/b.png"}]):
            with self.subTest(references=references):
                response = self.client.post(
                    "/api/generations",
                    json={"mode": "clothing_image_to_image", "provider": "hermes", "request": {"objective": "show fabric detail"}, "reference_images": references},
                )
                self.assertEqual(response.status_code, 422)

    def test_clothing_image_to_image_rejects_ambiguous_remote_reference(self) -> None:
        for value in ("https://cdn.example.com/garment.png?token=opaque", "https://cdn.example.com/download"):
            with self.subTest(value=value):
                response = self.client.post(
                    "/api/generations",
                    json={
                        "mode": "clothing_image_to_image",
                        "provider": "hermes",
                        "request": {"objective": "show fabric detail"},
                        "reference_images": [{"kind": "url", "value": value}],
                    },
                )
                self.assertEqual(response.status_code, 422)

    @patch("api_server.generate_clothing_image")
    def test_clothing_image_to_image_job_forwards_only_garment_path(self, generate_skill) -> None:
        garment = self.images / "garment.png"
        output = self.images / "clothing-result.png"
        garment.write_bytes(b"garment")
        output.write_bytes(b"result")
        generate_skill.return_value = {
            "prompt": "immutable garment prompt",
            "outputs": [str(output)],
            "quality": {"passed": True},
        }
        payload = api_server.GenerationRequest(
            mode="clothing_image_to_image",
            provider="hermes",
            request={"objective": "show fabric detail"},
            reference_images=[api_server.ReferenceImage(kind="asset", value="images/garment.png")],
        )
        job = api_server.GenerationJob(id="clothing-job", payload=payload)
        api_server.JOBS[job.id] = job
        with patch.object(api_server, "_hermes_client", return_value=object()):
            api_server._run_job(job.id)
        generate_skill.assert_called_once()
        kwargs = generate_skill.call_args.kwargs
        self.assertEqual(kwargs["garment_image"], str(garment.resolve()))
        self.assertNotIn("reference_images", kwargs)
        self.assertEqual(job.status, "succeeded")

    @patch("api_server.generate_model_outfit_image")
    def test_model_outfit_swap_job_forwards_model_and_outfit_paths(self, generate_skill) -> None:
        model = self.images / "model.png"
        outfit = self.images / "outfit.png"
        output = self.images / "result.png"
        model.write_bytes(b"model")
        outfit.write_bytes(b"outfit")
        output.write_bytes(b"result")
        generate_skill.return_value = {
            "prompt": "outfit prompt",
            "outputs": [str(output)],
            "quality": {"passed": True},
        }
        payload = api_server.GenerationRequest(
            mode="model_outfit_swap",
            provider="hermes",
            request={"product": "jacket", "scene": "studio", "style": "catalog"},
            reference_images=[
                api_server.ReferenceImage(kind="asset", value="images/model.png"),
                api_server.ReferenceImage(kind="asset", value="images/outfit.png"),
            ],
        )
        job = api_server.GenerationJob(id="outfit-job", payload=payload)
        api_server.JOBS[job.id] = job
        with patch.object(api_server, "_hermes_client", return_value=object()):
            api_server._run_job(job.id)
        generate_skill.assert_called_once()
        kwargs = generate_skill.call_args.kwargs
        self.assertEqual(generate_skill.call_args.args[0], {})
        self.assertEqual(kwargs["model_image"], str(model.resolve()))
        self.assertEqual(kwargs["outfit_images"], [str(outfit.resolve())])
        self.assertEqual(kwargs["provider"], "hermes")
        self.assertEqual(job.status, "succeeded")

    @patch("api_server.generate_model_outfit_image")
    def test_model_outfit_swap_job_forwards_selected_seedream_provider(self, generate_skill) -> None:
        model = self.images / "model.png"
        outfit = self.images / "outfit.png"
        output = self.images / "result.png"
        model.write_bytes(b"model")
        outfit.write_bytes(b"outfit")
        output.write_bytes(b"result")
        generate_skill.return_value = {"prompt": "seedream prompt", "outputs": [str(output)], "quality": {"passed": True}}
        payload = api_server.GenerationRequest(
            mode="model_outfit_swap",
            provider="hermes_volcano",
            reference_images=[
                api_server.ReferenceImage(kind="asset", value="images/model.png"),
                api_server.ReferenceImage(kind="asset", value="images/outfit.png"),
            ],
        )
        job = api_server.GenerationJob(id="outfit-seedream-job", payload=payload)
        api_server.JOBS[job.id] = job
        with patch.object(api_server, "_image_client_for_provider", return_value=object()):
            api_server._run_job(job.id)
        self.assertEqual(generate_skill.call_args.kwargs["provider"], "hermes_volcano")

    @patch("api_server._provider_available", return_value=(True, None))
    @patch("api_server.generate_image")
    def test_mock_image_job_saves_and_exposes_asset(self, generate_image, _available) -> None:
        output = self.images / "mock.png"

        def fake_generation(*args, **kwargs):
            output.write_bytes(b"mock-image")
            return str(output)

        generate_image.side_effect = fake_generation
        response = self.client.post(
            "/api/generations",
            json={"mode": "image", "request": {"prompt": "studio product photo"}},
        )
        self.assertEqual(response.status_code, 202)
        job_id = response.json()["job_id"]
        for _ in range(50):
            job = self.client.get(f"/api/jobs/{job_id}").json()
            if job["status"] in {"succeeded", "failed"}:
                break
            time.sleep(0.01)
        self.assertEqual(job["status"], "succeeded")
        self.assertEqual(job["outputs"], ["/api/assets/images/mock.png"])
        asset = self.client.get(job["outputs"][0])
        self.assertEqual(asset.status_code, 200)
        self.assertEqual(asset.content, b"mock-image")


if __name__ == "__main__":
    unittest.main()
