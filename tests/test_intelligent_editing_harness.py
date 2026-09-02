import json
import tempfile
import unittest
from pathlib import Path

from harness.intelligent_editing.runner import (
    HarnessError,
    IntelligentEditingHarness,
    LocalApiClient,
    _build_parser,
)


ROOT = Path(__file__).resolve().parents[1]


def request(**overrides):
    value = {
        "request_id": "req-harness-001",
        "selected_assets": [
            {"asset_id": "images/first.png", "media_type": "image", "duration_ms": 3000},
            {"asset_id": "videos/second.mp4", "media_type": "video", "duration_ms": 3000},
        ],
        "objective": "product recap",
        "aspect_ratio": "square",
        "target_duration_ms": 6000,
        "image_duration_ms": 3000,
        "transition_mode": "auto",
        "planner": "local",
        "idempotency_key": "same-render",
    }
    value.update(overrides)
    return value


class FakeApi:
    def __init__(self, plan=None, fail_plan=False):
        self.calls = []
        self.plan = plan or {
            "version": "aigc-mix-plan/v1",
            "objective": "product recap",
            "target_duration_ms": 6000,
            "clips": [
                {"asset_id": "images/first.png", "duration_ms": 3000, "transition": "hard_cut"},
                {"asset_id": "videos/second.mp4", "duration_ms": 3000, "transition": "hard_cut"},
            ],
            "planner": "local",
            "transition_mode": "auto",
            "warnings": [],
        }
        self.fail_plan = fail_plan
        self.mix_status = {"status": "succeeded", "phase": "混剪完成", "output": "/api/assets/videos/mix.mp4"}

    def __call__(self, method, path, payload):
        self.calls.append((method, path, payload))
        if path == "/api/assets":
            return {"assets": [
                {"id": "images/first.png", "media_type": "image", "size": 10},
                {"id": "videos/second.mp4", "media_type": "video", "size": 20},
            ]}
        if path == "/api/mixes/plan":
            if self.fail_plan:
                raise TimeoutError("upstream secret and private path")
            return self.plan
        if path == "/api/mixes":
            return {"mix_id": "mix-001", "status": "queued"}
        if path == "/api/mixes/mix-001":
            return self.mix_status
        raise AssertionError(path)


class IntelligentEditingHarnessTests(unittest.TestCase):
    def make_harness(self, fake):
        state_dir = tempfile.TemporaryDirectory()
        self.addCleanup(state_dir.cleanup)
        return IntelligentEditingHarness(state_dir.name, LocalApiClient(transport=fake)), fake, Path(state_dir.name)

    def test_documented_harness_entrypoints_exist(self):
        delivery_root = ROOT / "harness" / "intelligent-editing"
        for name in ("README.md", "__init__.py", "runner.py"):
            self.assertTrue((delivery_root / name).is_file(), name)

    def test_api_client_accepts_only_credential_free_loopback_urls(self):
        for value in ("http://127.0.0.1:8001", "http://127.0.0.2:9000", "https://localhost:8443", "http://[::1]:8001"):
            self.assertEqual(LocalApiClient(value).base_url, value)
        for value in (
            "https://api.example.com",
            "http://192.168.1.2:8001",
            "http://user:password@127.0.0.1:8001",
            "http://localhost:8001/api",
            "http://localhost:8001?token=secret",
        ):
            with self.assertRaises(ValueError):
                LocalApiClient(value)

    def test_preview_is_side_effect_free_and_persists_hash(self):
        harness, fake, state_dir = self.make_harness(FakeApi())
        result = harness.preview(request())
        self.assertEqual(result["version"], "aigc-intelligent-editing-run/v1")
        self.assertEqual(result["stage"], "previewed")
        self.assertEqual(result["status"], "awaiting_confirmation")
        self.assertRegex(result["input_fingerprint"], r"^[0-9a-f]{64}$")
        self.assertRegex(result["plan_hash"], r"^[0-9a-f]{64}$")
        self.assertFalse(any(path == "/api/mixes" for _, path, _ in fake.calls))
        state_text = next(state_dir.glob("*.json")).read_text(encoding="utf-8")
        self.assertNotIn("secret", state_text.lower())
        self.assertNotIn("C:\\", state_text)
        self.assertNotIn("command", state_text.lower())

    def test_local_and_auto_planners_never_request_remote_planning(self):
        for planner in ("local", "auto"):
            with self.subTest(planner=planner):
                harness, fake, _ = self.make_harness(FakeApi())
                result = harness.preview(
                    request(
                        request_id=f"req-{planner}",
                        idempotency_key=f"key-{planner}",
                        planner=planner,
                    )
                )
                self.assertEqual(result["planner"], "local")
                self.assertFalse(
                    any(path == "/api/mixes/plan" for _, path, _ in fake.calls)
                )

    def test_cli_accepts_documented_flags_and_positional_compatibility(self):
        parser = _build_parser()
        flagged = parser.parse_args([
            "--api-base", "http://localhost:8001",
            "--state-root", "run-state",
            "confirm", "--request-id", "req-001", "--plan-hash", "a" * 64,
        ])
        self.assertEqual(flagged.request_id_option, "req-001")
        self.assertEqual(flagged.plan_hash_option, "a" * 64)
        preview = parser.parse_args(["preview", "--request", "request.json"])
        self.assertEqual(preview.request_option, "request.json")
        positional = parser.parse_args(["status", "req-001"])
        self.assertEqual(positional.request_id, "req-001")
        self.assertEqual(parser.parse_args(["plan", "request.json"]).command, "plan")
        self.assertEqual(parser.parse_args(["validate", "request.json"]).command, "validate")
        self.assertEqual(parser.parse_args(["resume", "req-001"]).command, "resume")

    def test_idempotency_key_reuses_existing_run_across_request_ids(self):
        harness, fake, _ = self.make_harness(FakeApi())
        first = harness.preview(request())
        second = harness.preview(request(request_id="req-harness-002"))
        self.assertEqual(second["request_id"], first["request_id"])
        self.assertEqual(second["plan_hash"], first["plan_hash"])
        self.assertEqual(sum(path == "/api/mixes/plan" for _, path, _ in fake.calls), 0)
        with self.assertRaisesRegex(HarnessError, "idempotency_key"):
            harness.preview(
                request(request_id="req-harness-003", objective="different objective")
            )

    def test_confirmation_hash_gates_single_idempotent_render(self):
        harness, fake, _ = self.make_harness(FakeApi())
        preview = harness.preview(request())
        with self.assertRaisesRegex(HarnessError, "哈希"):
            harness.confirm("req-harness-001", "0" * 64)
        with self.assertRaisesRegex(HarnessError, "确认"):
            harness.render("req-harness-001", preview["plan_hash"])
        harness.confirm("req-harness-001", preview["plan_hash"])
        first = harness.render("req-harness-001", preview["plan_hash"])
        second = harness.render("req-harness-001", preview["plan_hash"])
        self.assertEqual(first["mix_id"], "mix-001")
        self.assertEqual(second["mix_id"], "mix-001")
        self.assertEqual(sum(path == "/api/mixes" for _, path, _ in fake.calls), 1)
        self.assertEqual(fake.calls[-1][2]["aspect_ratio"], "square")

    def test_tampered_persisted_plan_cannot_reach_render(self):
        harness, fake, state_dir = self.make_harness(FakeApi())
        preview = harness.preview(request())
        harness.confirm("req-harness-001", preview["plan_hash"])
        state_file = next(state_dir.glob("*.json"))
        state = json.loads(state_file.read_text(encoding="utf-8"))
        state["plan"]["clips"][0]["duration_ms"] = 2500
        state_file.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        with self.assertRaisesRegex(HarnessError, "无法安全恢复"):
            harness.render("req-harness-001", preview["plan_hash"])
        self.assertFalse(any(path == "/api/mixes" for _, path, _ in fake.calls))

    def test_tampered_persisted_evidence_is_rebuilt_from_validated_state(self):
        harness, _, state_dir = self.make_harness(FakeApi())
        harness.preview(request())
        state_file = next(state_dir.glob("*.json"))
        state = json.loads(state_file.read_text(encoding="utf-8"))
        state["evidence"] = {
            "api_key": "secret-value",
            "path": "C:/private/source.mp4",
            "raw_provider_response": {"authorization": "Bearer secret"},
        }
        state["output"] = "/api/assets/videos/result.mp4?token=secret"
        state["error"] = {"code": "provider_error", "message": "C:/private/source.mp4"}
        state_file.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        recovered = harness.status("req-harness-001")
        serialized = json.dumps(recovered, ensure_ascii=False).lower()
        self.assertNotIn("secret", serialized)
        self.assertNotIn("private", serialized)
        self.assertEqual(recovered["evidence"]["stage"], "previewed")
        self.assertIsNone(recovered["output"])
        self.assertIsNone(recovered["error"])

    def test_unsafe_mix_id_is_rejected_before_state_persistence(self):
        class UnsafeMixApi(FakeApi):
            def __call__(self, method, path, payload):
                if path == "/api/mixes":
                    self.calls.append((method, path, payload))
                    return {"mix_id": "../../secret?token=value", "status": "queued"}
                return super().__call__(method, path, payload)

        harness, _, state_dir = self.make_harness(UnsafeMixApi())
        preview = harness.preview(request())
        harness.confirm("req-harness-001", preview["plan_hash"])
        with self.assertRaises(HarnessError):
            harness.render("req-harness-001", preview["plan_hash"])
        state_text = next(state_dir.glob("*.json")).read_text(encoding="utf-8")
        self.assertNotIn("secret", state_text.lower())
        self.assertNotIn("token", state_text.lower())

    def test_terra_failure_falls_back_without_leaking_exception(self):
        harness, fake, _ = self.make_harness(FakeApi(fail_plan=True))
        result = harness.preview(request(planner="codex_terra"))
        self.assertEqual(result["planner"], "local")
        self.assertTrue(any("本地" in warning for warning in result["plan"]["warnings"]))
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("secret", serialized.lower())
        self.assertNotIn("private", serialized.lower())

    def test_request_conflict_and_missing_asset_fail_before_plan(self):
        harness, fake, _ = self.make_harness(FakeApi())
        harness.preview(request())
        with self.assertRaisesRegex(HarnessError, "改变"):
            harness.preview(request(objective="changed objective"))
        class MissingApi(FakeApi):
            def __call__(self, method, path, payload):
                self.calls.append((method, path, payload))
                if path == "/api/assets":
                    return {"assets": []}
                raise AssertionError(path)

        missing = MissingApi()
        harness2, _, _ = self.make_harness(missing)
        with self.assertRaises(HarnessError):
            harness2.preview(request())

    def test_status_persists_safe_output_and_error(self):
        harness, fake, _ = self.make_harness(FakeApi())
        preview = harness.preview(request())
        harness.confirm("req-harness-001", preview["plan_hash"])
        harness.render("req-harness-001", preview["plan_hash"])
        result = harness.status("req-harness-001")
        self.assertEqual(result["stage"], "succeeded")
        self.assertEqual(result["evidence"]["output_asset"], "/api/assets/videos/mix.mp4")
        fake.mix_status = {
            "status": "failed",
            "phase": "C:/secret/source.mp4 --token x",
            "output": "/api/assets/videos/mix.mp4?token=secret",
            "error": "C:/secret/source.mp4 --token x",
        }
        state_file = next(Path(harness.state_dir).glob("*.json"))
        state = json.loads(state_file.read_text(encoding="utf-8"))
        state["stage"] = "running"
        state_file.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        failed = harness.status("req-harness-001")
        self.assertEqual(failed["evidence"]["error"], "混剪任务失败，请检查素材编码和文件完整性")
        self.assertNotIn("secret", json.dumps(failed).lower())
        self.assertNotIn("phase", failed["evidence"])


if __name__ == "__main__":
    unittest.main()
