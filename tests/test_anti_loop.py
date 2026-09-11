from datetime import datetime, timedelta, timezone
import tempfile
import unittest
from pathlib import Path

from harness.anti_loop import AntiLoopGuard, GuardError


class AntiLoopGuardTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.guard = AntiLoopGuard(Path(self.temporary.name))

    def start(self, task_id="task-a", **overrides):
        values = {
            "task_id": task_id,
            "workspace_id": "workspace-a",
            "owner": "aigc-build-codex",
            "objective": "implement the named contract",
            "acceptance": "targeted test passes",
            "inputs": {"request": "same"},
            "budget": {"max_actions": 8, "max_no_progress": 2},
        }
        values.update(overrides)
        return self.guard.start(**values)

    def test_start_is_idempotent_for_same_immutable_input(self):
        first = self.start()
        second = self.start()
        self.assertEqual(second["task_id"], first["task_id"])
        self.assertEqual(len(self.guard.status("workspace-a")), 1)
        with self.assertRaisesRegex(GuardError, "different immutable input"):
            self.start(inputs={"request": "changed"})

    def test_transient_retry_requires_hypothesis_and_is_bounded(self):
        self.start()
        first = self.guard.admit("task-a", "tool", "provider", {"job": "one"}, "remote job state")
        self.guard.finish("task-a", first["sequence"], "transient", False)
        with self.assertRaisesRegex(GuardError, "requires a concrete hypothesis"):
            self.guard.admit("task-a", "tool", "provider", {"job": "one"}, "remote job state")
        retry = self.guard.admit(
            "task-a", "tool", "provider", {"job": "one"}, "remote job state", "provider backoff elapsed"
        )
        self.assertEqual(retry["sequence"], 2)

    def test_unknown_outcome_allows_only_one_diagnostic(self):
        self.start()
        first = self.guard.admit("task-a", "tool", "provider", {"job": "one"}, "remote job state")
        self.guard.finish("task-a", first["sequence"], "unknown", False)
        with self.assertRaisesRegex(GuardError, "unknown outcome allows only one diagnostic"):
            self.guard.admit("task-a", "tool", "provider", {"job": "one"}, "remote job state", "one more probe")
        self.assertEqual(self.guard.status("workspace-a")[0]["blocker"]["reason_code"], "unknown_unresolved")

    def test_resume_resets_no_progress_counter(self):
        self.start()
        first = self.guard.admit("task-a", "tool", "diagnostic-a", {}, "new evidence")
        self.guard.finish("task-a", first["sequence"], "succeeded", False)
        second = self.guard.admit("task-a", "tool", "diagnostic-b", {}, "new evidence")
        self.guard.finish("task-a", second["sequence"], "succeeded", False)
        resumed = self.guard.resume("task-a", "Lead grants a bounded retry with a changed probe")
        self.assertEqual(resumed["counters"]["consecutive_no_progress"], 0)
        third = self.guard.admit("task-a", "tool", "diagnostic-c", {}, "new evidence")
        result = self.guard.finish("task-a", third["sequence"], "succeeded", False)
        self.assertEqual(result["state"], "ready")
        self.assertEqual(result["counters"]["consecutive_no_progress"], 1)

    def test_two_no_progress_attempts_pause_the_run(self):
        self.start()
        first = self.guard.admit("task-a", "tool", "diagnostic-a", {}, "new evidence")
        self.guard.finish("task-a", first["sequence"], "succeeded", False)
        second = self.guard.admit("task-a", "tool", "diagnostic-b", {}, "new evidence")
        result = self.guard.finish("task-a", second["sequence"], "succeeded", False)
        self.assertEqual(result["state"], "paused")
        self.assertEqual(result["blocker"]["reason_code"], "no_progress")
        with self.assertRaisesRegex(GuardError, "paused"):
            self.guard.admit("task-a", "tool", "diagnostic-c", {}, "new evidence")

    def test_progress_resets_no_progress_counter(self):
        self.start()
        first = self.guard.admit("task-a", "tool", "probe", {}, "state")
        self.guard.finish("task-a", first["sequence"], "succeeded", False)
        second = self.guard.admit("task-a", "tool", "test", {}, "artifact")
        result = self.guard.finish("task-a", second["sequence"], "succeeded", True, {"test": "passed"})
        self.assertEqual(result["state"], "ready")
        self.assertEqual(result["counters"]["consecutive_no_progress"], 0)
        self.assertRegex(result["last_progress"]["evidence_hash"], r"^[0-9a-f]{64}$")

    def test_self_dependency_pauses_the_run(self):
        self.start()
        with self.assertRaisesRegex(GuardError, "cannot depend on itself"):
            self.guard.dependencies("task-a", ["task-a"])
        run = self.guard.status("workspace-a")[0]
        self.assertEqual(run["state"], "paused")
        self.assertEqual(run["blocker"]["reason_code"], "dependency_cycle")

    def test_two_task_dependency_cycle_pauses_both_runs(self):
        self.start("task-a")
        self.start("task-b")
        deadline = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        self.guard.dependencies("task-b", ["task-a"], deadline)
        result = self.guard.dependencies("task-a", ["task-b"], deadline)
        self.assertEqual(result["state"], "paused")
        runs = {run["task_id"]: run for run in self.guard.status("workspace-a")}
        self.assertEqual(runs["task-a"]["state"], "paused")
        self.assertEqual(runs["task-b"]["state"], "paused")
        self.assertEqual(runs["task-a"]["blocker"]["reason_code"], "dependency_cycle")
        self.assertEqual(runs["task-a"]["escalation"]["reason_code"], "dependency_cycle")

    def test_herdr_lifecycle_events_are_sanitized_and_bounded(self):
        self.start()
        self.guard.observe_herdr_event(
            "workspace-a",
            {"type": "pane_agent_status_changed", "workspace_id": "workspace-a", "pane_id": "wK:p3", "agent": "codex", "agent_status": "working", "unsafe": "omit"},
        )
        run = self.guard.status("workspace-a")[0]
        self.assertEqual(run["herdr_events"][0]["agent_status"], "working")
        self.assertNotIn("unsafe", run["herdr_events"][0])

    def test_waiting_requires_named_deadline_and_wake(self):
        self.start("task-a")
        self.start("task-b")
        with self.assertRaisesRegex(GuardError, "named dependency deadline"):
            self.guard.dependencies("task-a", ["task-b"])
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        waiting = self.guard.dependencies("task-a", ["task-b"], future)
        self.assertEqual(waiting["state"], "waiting")
        with self.assertRaisesRegex(GuardError, "waiting"):
            self.guard.admit("task-a", "tool", "probe", {}, "state")
        self.guard.complete("task-b", {"ok": True})
        woken = self.guard.wake("task-a")
        self.assertEqual(woken["state"], "ready")

    def test_paused_run_resumes_only_with_lead_hypothesis(self):
        self.start()
        first = self.guard.admit("task-a", "tool", "diagnostic-a", {}, "new evidence")
        self.guard.finish("task-a", first["sequence"], "succeeded", False)
        second = self.guard.admit("task-a", "tool", "diagnostic-b", {}, "new evidence")
        paused = self.guard.finish("task-a", second["sequence"], "succeeded", False)
        self.assertEqual(paused["escalation"]["reason_code"], "no_progress")
        resumed = self.guard.resume("task-a", "Lead grants a bounded retry with a changed probe")
        self.assertEqual(resumed["state"], "ready")
        third = self.guard.admit("task-a", "tool", "diagnostic-c", {}, "new evidence")
        self.assertEqual(third["sequence"], 3)

    def test_complete_is_idempotent_for_terminal_success(self):
        self.start()
        first = self.guard.complete("task-a", {"done": True})
        second = self.guard.complete("task-a", {"done": True})
        self.assertEqual(first["state"], "completed")
        self.assertEqual(second["state"], "completed")

    def test_review_finding_pauses_after_second_reopen(self):
        self.start()
        first = self.guard.admit("task-a", "review", "quality", {"finding_id": "find-1"}, "verification")
        self.guard.finish("task-a", first["sequence"], "succeeded", True, {"round": 1})
        second = self.guard.admit("task-a", "review", "quality", {"finding_id": "find-1", "evidence": "new"}, "verification")
        self.guard.finish("task-a", second["sequence"], "succeeded", True, {"round": 2})
        with self.assertRaisesRegex(GuardError, "review finding exceeded"):
            self.guard.admit("task-a", "review", "quality", {"finding_id": "find-1", "evidence": "again"}, "verification")
        self.assertEqual(self.guard.status("workspace-a")[0]["blocker"]["reason_code"], "review_deadlock")

    def test_elapsed_deadline_pauses_before_admit(self):
        now = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)
        self.guard = AntiLoopGuard(Path(self.temporary.name), clock=lambda: now)
        self.start(budget={"deadline_at": "2026-09-08T11:00:00+00:00"})
        with self.assertRaisesRegex(GuardError, "run deadline has elapsed"):
            self.guard.admit("task-a", "tool", "probe", {}, "state")
        self.assertEqual(self.guard.status("workspace-a")[0]["blocker"]["reason_code"], "deadline_exceeded")

    def test_bootstrap_and_ensure_reuse_existing_run(self):
        first = self.guard.bootstrap("workspace-a")
        second = self.guard.bootstrap("workspace-a")
        self.assertEqual(first["task_id"], "TEAM-ROOT")
        self.assertEqual(second["task_id"], first["task_id"])
        self.assertEqual(len(self.guard.status("workspace-a")), 1)
        reused = self.guard.ensure(
            "TEAM-ROOT",
            "workspace-a",
            "aigc-build-codex",
            "different objective",
            "different acceptance",
            {"request": "changed"},
        )
        self.assertEqual(reused["objective"], first["objective"])
        self.assertEqual(reused["input_fingerprint"], first["input_fingerprint"])
        with self.assertRaisesRegex(GuardError, "different immutable input"):
            self.guard.start(
                "TEAM-ROOT",
                "workspace-a",
                "aigc-build-codex",
                "different objective",
                "different acceptance",
                {"request": "changed"},
            )

    def test_fail_is_idempotent_and_rejects_other_terminal_states(self):
        self.start()
        first = self.guard.fail("task-a", "provider unavailable")
        second = self.guard.fail("task-a", "provider unavailable")
        self.assertEqual(first["state"], "failed")
        self.assertEqual(second["state"], "failed")
        self.start(task_id="task-b")
        self.guard.complete("task-b")
        with self.assertRaisesRegex(GuardError, "task is already terminal"):
            self.guard.fail("task-b", "too late")
        self.start(task_id="task-c")
        self.guard.admit("task-c", "tool", "provider", {"job": "one"}, "remote job state")
        blocked = self.guard.finish("task-c", 1, "permission_or_policy", False)
        self.assertEqual(blocked["state"], "blocked")
        with self.assertRaisesRegex(GuardError, "task is already terminal"):
            self.guard.fail("task-c", "too late")

    def test_sync_mirrors_terminal_coordination_and_skips_paused(self):
        root = self.guard.bootstrap("workspace-a")
        self.assertEqual(root["state"], "ready")
        paused = self.start(task_id="TASK-PAUSED")
        first = self.guard.admit("TASK-PAUSED", "tool", "provider", {"job": "one"}, "remote job state")
        self.guard.finish("TASK-PAUSED", first["sequence"], "transient", False)
        self.guard.admit(
            "TASK-PAUSED",
            "tool",
            "provider",
            {"job": "one"},
            "remote job state",
            "provider backoff elapsed",
        )
        self.guard.finish("TASK-PAUSED", 2, "transient", False)
        self.assertEqual(self.guard.store.load("TASK-PAUSED")["state"], "paused")
        result = self.guard.sync_coordination(
            "workspace-a",
            [
                {"id": "TEAM-ROOT", "owner": "aigc-lead-codex", "status": "planned"},
                {
                    "id": "TASK-DONE",
                    "owner": "aigc-build-codex",
                    "status": "accepted",
                    "acceptanceCriteria": ["tests pass"],
                },
                {"id": "TASK-FAIL", "owner": "aigc-build-codex", "status": "failed"},
                {"id": "TASK-PAUSED", "owner": "aigc-build-codex", "status": "accepted"},
            ],
        )
        self.assertEqual(result["root"]["task_id"], "TEAM-ROOT")
        states = {item["task_id"]: item["state"] for item in result["mirrored"]}
        self.assertEqual(states["TEAM-ROOT"], "ready")
        self.assertEqual(states["TASK-DONE"], "completed")
        self.assertEqual(states["TASK-FAIL"], "failed")
        self.assertEqual(states["TASK-PAUSED"], "paused")
        self.assertEqual(result["skipped"][0]["task_id"], "TASK-PAUSED")
        self.assertEqual(result["skipped"][0]["code"], "paused")
        self.assertEqual(paused["task_id"], "TASK-PAUSED")

if __name__ == "__main__":
    unittest.main()
