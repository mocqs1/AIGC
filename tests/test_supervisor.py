import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness.supervisor import HerdrSupervisor, SingleInstance


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "AGENTS.md").write_text("# test\n", encoding="utf-8")
        self.state = self.root / "state"
        self.anti_loop = self.root / ".agents/runtime/herdr-anti-loop"
        self.anti_loop.mkdir(parents=True)
        self.supervisor = HerdrSupervisor(self.root, self.state, quiet_seconds=30)

    def write_run(self, **overrides):
        run = {
            "version": "aigc-herdr-anti-loop/v1",
            "task_id": "task-a",
            "workspace_id": "workspace-a",
            "owner": "aigc-build-codex",
            "state": "active",
            "last_progress_at": "2099-01-01T00:00:00+00:00",
            "counters": {"event_count": 0, "token_estimate": 0},
            "rotation": {"event_count": 0, "token_estimate": 0},
        }
        run.update(overrides)
        (self.anti_loop / "run.json").write_text(json.dumps(run), encoding="utf-8")

    @patch("harness.supervisor.subprocess.run")
    def test_reconcile_does_not_prompt_completed_or_idle_without_active_run(self, run):
        run.return_value.returncode = 0
        run.return_value.stdout = json.dumps({"result": {"snapshot": {"agents": []}}})
        result = self.supervisor.reconcile()
        self.assertEqual(result["actions"], [])
        self.assertEqual(result["findings"], [])

    @patch("harness.supervisor.subprocess.run")
    def test_idle_active_run_is_prompted_once_with_durable_action(self, run):
        self.write_run()
        snapshot = {"result": {"snapshot": {"agents": [{"name": "aigc-build-codex", "cwd": str(self.root), "agent_status": "idle", "pane_id": "w1:p2", "revision": 4}]}}}
        run.side_effect = [type("Result", (), {"returncode": 0, "stdout": json.dumps(snapshot)})(), type("Result", (), {"returncode": 0, "stdout": "{}"})()]
        result = self.supervisor.reconcile()
        self.assertEqual(len(result["actions"]), 1)
        self.assertEqual(result["actions"][0]["result"], "submitted")
        self.assertIn("Supervisor recovery nonce", run.call_args_list[1].args[0][-1])
    @patch("harness.supervisor.subprocess.run")
    def test_unknown_labeled_pane_is_restarted_without_close(self, run):
        snapshot = {"result": {"snapshot": {"agents": [{"label": "AIGC Build Codex", "agent_status": "unknown", "cwd": str(self.root), "pane_id": "w1:p2"}]}}}
        start_result = type("Result", (), {"returncode": 0, "stdout": "{}"})()
        run.return_value = type("Result", (), {"returncode": 0, "stdout": json.dumps(snapshot)})()
        run.side_effect = [run.return_value, start_result]
        result = self.supervisor.reconcile()
        self.assertEqual(result["actions"][0]["kind"], "role_rebind")
        self.assertEqual(result["actions"][0]["result"], "started")
        self.assertEqual(run.call_args_list[1].args[0][:7], ["herdr", "agent", "start", "aigc-build-codex", "--kind", "codex", "--pane"])
        self.assertFalse(any(call.args[0][2] in {"close", "stop"} for call in run.call_args_list))

    @patch("harness.supervisor.subprocess.run")
    def test_working_named_agent_is_left_running(self, run):
        self.write_run()
        snapshot = {"result": {"snapshot": {"agents": [{"name": "aigc-build-codex", "agent_status": "working", "cwd": str(self.root), "pane_id": "w1:p2", "revision": 9}]}}}
        run.return_value = type("Result", (), {"returncode": 0, "stdout": json.dumps(snapshot)})()

        result = self.supervisor.reconcile()

        self.assertEqual(result["actions"], [])
        self.assertEqual(result["findings"], [])
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0][:3], ["herdr", "api", "snapshot"])


    @patch("harness.supervisor.subprocess.run")
    def test_rotation_writes_checkpoint_without_in_session_compact(self, run):
        self.write_run(
            counters={"event_count": 901, "token_estimate": 0},
            rotation={"event_count": 0, "token_estimate": 0, "base_event_count": 0, "base_token_estimate": 0},
        )
        snapshot = {"result": {"snapshot": {"agents": [{"name": "aigc-build-codex", "cwd": str(self.root), "agent_status": "working", "pane_id": "w1:p2", "revision": 12}]}}}
        run.return_value = type("Result", (), {"returncode": 0, "stdout": json.dumps(snapshot)})()

        report = self.supervisor.reconcile()

        self.assertEqual(report["actions"][0]["kind"], "context_handoff_ready")
        self.assertEqual(report["actions"][0]["result"], "ready")
        self.assertEqual(run.call_count, 1)
        checkpoint = next((self.state / "context-checkpoints").glob("*.json"))
        saved = json.loads(checkpoint.read_text(encoding="utf-8"))
        self.assertEqual(saved["version"], "aigc-context-checkpoint/v1")
        self.assertEqual(saved["task_id"], "task-a")

    @patch("harness.supervisor.subprocess.run")
    def test_ready_handoff_starts_fresh_agent_once(self, run):
        self.write_run(
            counters={"event_count": 901, "token_estimate": 0},
            rotation={"generation": 1, "event_count": 901, "token_estimate": 0, "base_event_count": 901, "base_token_estimate": 0, "state": "handoff_ready", "checkpoint": "checkpoint.json", "revision_before": 12},
        )
        snapshot = {"result": {"snapshot": {"agents": [{"name": "aigc-build-codex", "cwd": str(self.root), "agent_status": "working", "pane_id": "w1:p2", "revision": 12}]}}}
        run.return_value = type("Result", (), {"returncode": 0, "stdout": json.dumps(snapshot)})()

        report = self.supervisor.reconcile()

        self.assertEqual(report["actions"][0]["kind"], "context_handoff_start")
        self.assertEqual(report["actions"][0]["result"], "started")
        self.assertEqual(run.call_args_list[1].args[0][:7], ["herdr", "agent", "start", "aigc-build-codex", "--kind", "codex", "--pane"])
        self.assertEqual(json.loads(next(self.anti_loop.glob("*.json")).read_text(encoding="utf-8"))["rotation"]["state"], "handoff_started")

    @patch("harness.supervisor.subprocess.run")
    def test_completed_rotation_resets_budget_baseline(self, run):
        self.write_run(
            counters={"event_count": 901, "token_estimate": 0},
            rotation={"generation": 1, "event_count": 900, "token_estimate": 0, "base_event_count": 0, "base_token_estimate": 0, "state": "submitted", "revision_before": 12, "checkpoint": "checkpoint.json"},
        )
        snapshot = {"result": {"snapshot": {"agents": [{"name": "aigc-build-codex", "cwd": str(self.root), "agent_status": "working", "pane_id": "w1:p2", "revision": 13}]}}}
        run.return_value = type("Result", (), {"returncode": 0, "stdout": json.dumps(snapshot)})()

        report = self.supervisor.reconcile()

        self.assertEqual(report["actions"], [])
        self.assertEqual(report["findings"][0]["code"], "handoff_completed")
        saved = json.loads(next(self.anti_loop.glob("*.json")).read_text(encoding="utf-8"))
        self.assertEqual(saved["rotation"]["state"], "completed")
        self.assertEqual(saved["rotation"]["base_event_count"], 901)

    def test_single_instance_rejects_live_duplicate(self):
        first = SingleInstance(self.state / "lock")
        second = SingleInstance(self.state / "lock")
        self.assertTrue(first.acquire())
        self.assertFalse(second.acquire())
        first.release()


if __name__ == "__main__":
    unittest.main()
