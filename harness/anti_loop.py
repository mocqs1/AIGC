"""Durable, cooperative anti-loop guard for Herdr-managed agent work.

The guard is intentionally separate from Herdr's agent integrations. Herdr
reports pane lifecycle; this module admits named actions, tracks evidence-backed
progress, and pauses runs before repeated work becomes an unbounded loop.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

RUN_VERSION = "aigc-herdr-anti-loop/v1"
DEFAULT_STATE_DIR = ".agents/runtime/herdr-anti-loop"
TERMINAL_STATES = frozenset({"completed", "blocked", "failed"})
ACTIVE_STATES = frozenset({"ready", "active", "waiting", "paused", "verifying"})
SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
MAX_TRANSIENT_RETRIES = 3


class GuardError(RuntimeError):
    """Stable failure returned by the anti-loop control plane."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _now() -> str:
    return _utcnow().isoformat()


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _safe_id(value: str, field: str) -> str:
    if not isinstance(value, str) or not SAFE_ID.fullmatch(value):
        raise GuardError("invalid_identifier", f"{field} must be an opaque safe identifier")
    return value


def _safe_text(value: str, field: str, limit: int = 512) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise GuardError("invalid_input", f"{field} must be a non-empty string no longer than {limit} characters")
    return value.strip()


def _parse_time(value: Any, field: str = "deadline_at") -> datetime | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise GuardError("invalid_budget", f"{field} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise GuardError("invalid_budget", f"{field} must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _retry_delay_seconds(retry_count: int) -> int:
    return min(30, 2 ** max(1, retry_count))


def _budget(value: Mapping[str, Any] | None) -> dict[str, int]:
    raw = dict(value or {})
    raw.pop("deadline_at", None)
    defaults = {
        "max_actions": 24,
        "max_tool_calls": 16,
        "max_model_turns": 8,
        "max_delegations": 3,
        "max_review_rounds": 2,
        "max_no_progress": 2,
    }
    result: dict[str, int] = {}
    for key, default in defaults.items():
        candidate = raw.get(key, default)
        if not isinstance(candidate, int) or isinstance(candidate, bool) or candidate < 0:
            raise GuardError("invalid_budget", f"{key} must be a non-negative integer")
        result[key] = candidate
    unexpected = set(raw) - set(defaults)
    if unexpected:
        raise GuardError("invalid_budget", f"unknown budget field: {sorted(unexpected)[0]}")
    return result


class AgentRunStore:
    """Atomic JSON run store, scoped by the Herdr workspace identifier."""

    def __init__(self, state_dir: str | Path = DEFAULT_STATE_DIR):
        self.state_dir = Path(state_dir)

    def _path(self, task_id: str) -> Path:
        return self.state_dir / f"{_digest(task_id)}.json"

    @staticmethod
    def _validate(run: Any) -> dict[str, Any]:
        if not isinstance(run, dict) or run.get("version") != RUN_VERSION:
            raise GuardError("corrupt_state", "anti-loop state is invalid or incompatible")
        _safe_id(run.get("task_id"), "task_id")
        _safe_id(run.get("workspace_id"), "workspace_id")
        _safe_id(run.get("owner"), "owner")
        if run.get("state") not in ACTIVE_STATES | TERMINAL_STATES:
            raise GuardError("corrupt_state", "anti-loop state has an invalid lifecycle state")
        if not isinstance(run.get("input_fingerprint"), str) or not re.fullmatch(r"[0-9a-f]{64}", run["input_fingerprint"]):
            raise GuardError("corrupt_state", "anti-loop state has an invalid input fingerprint")
        run["budget"] = _budget(run.get("budget"))
        counters = run.get("counters")
        if not isinstance(counters, dict):
            raise GuardError("corrupt_state", "anti-loop state has invalid counters")
        for key in ("actions", "tool_calls", "model_turns", "delegations", "review_rounds", "consecutive_no_progress"):
            if not isinstance(counters.get(key), int) or isinstance(counters[key], bool) or counters[key] < 0:
                raise GuardError("corrupt_state", "anti-loop state has invalid counters")
        if not isinstance(run.get("attempts"), list) or not isinstance(run.get("dependencies"), list):
            raise GuardError("corrupt_state", "anti-loop state has invalid attempts or dependencies")
        if run.get("deadline_at") is not None:
            _parse_time(run.get("deadline_at"))
        return run

    def load(self, task_id: str) -> dict[str, Any] | None:
        task_id = _safe_id(task_id, "task_id")
        try:
            raw = json.loads(self._path(task_id).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError) as error:
            raise GuardError("corrupt_state", "anti-loop state cannot be read safely") from error
        return self._validate(raw)

    def save(self, run: dict[str, Any]) -> None:
        self._validate(run)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        run.setdefault("created_at", _now())
        run["updated_at"] = _now()
        target = self._path(run["task_id"])
        descriptor, temporary = tempfile.mkstemp(prefix=f".{target.stem}.", suffix=".tmp", dir=self.state_dir)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(run, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
            os.replace(temporary, target)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    def runs(self, workspace_id: str | None = None) -> list[dict[str, Any]]:
        if workspace_id is not None:
            workspace_id = _safe_id(workspace_id, "workspace_id")
        try:
            paths = sorted(self.state_dir.glob("*.json"))
        except OSError as error:
            raise GuardError("state_unavailable", "anti-loop state directory cannot be read") from error
        result = []
        for path in paths:
            try:
                value = self._validate(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError, GuardError) as error:
                raise GuardError("corrupt_state", f"anti-loop state file {path.name} cannot be read safely") from error
            if workspace_id is None or value["workspace_id"] == workspace_id:
                result.append(value)
        return result


class AntiLoopGuard:
    """Admission gates and cycle detection for one workspace's agent runs."""

    def __init__(self, state_dir: str | Path = DEFAULT_STATE_DIR, clock: Callable[[], datetime] | None = None):
        self.store = AgentRunStore(state_dir)
        self._clock = clock or _utcnow

    def _stamp(self) -> str:
        value = self._clock()
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()

    def _now_dt(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def start(
        self,
        task_id: str,
        workspace_id: str,
        owner: str,
        objective: str,
        acceptance: str,
        inputs: Mapping[str, Any] | None = None,
        budget: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        task_id = _safe_id(task_id, "task_id")
        workspace_id = _safe_id(workspace_id, "workspace_id")
        owner = _safe_id(owner, "owner")
        objective = _safe_text(objective, "objective")
        acceptance = _safe_text(acceptance, "acceptance")
        immutable = {"workspace_id": workspace_id, "objective": objective, "acceptance": acceptance, "inputs": dict(inputs or {})}
        fingerprint = _digest(immutable)
        existing = self.store.load(task_id)
        if existing is not None:
            if existing["input_fingerprint"] != fingerprint:
                raise GuardError("task_conflict", "task_id already exists with a different immutable input")
            return existing
        raw_budget = dict(budget or {})
        deadline = _parse_time(raw_budget.get("deadline_at"))
        run = {
            "version": RUN_VERSION,
            "task_id": task_id,
            "workspace_id": workspace_id,
            "owner": owner,
            "objective": objective,
            "acceptance": acceptance,
            "input_fingerprint": fingerprint,
            "state": "ready",
            "budget": _budget(raw_budget),
            "deadline_at": deadline.isoformat() if deadline else None,
            "counters": {"actions": 0, "tool_calls": 0, "model_turns": 0, "delegations": 0, "review_rounds": 0, "consecutive_no_progress": 0},
            "dependencies": [],
            "wait": None,
            "attempts": [],
            "last_progress": None,
            "blocker": None,
            "escalation": None,
            "resolution": None,
            "herdr_events": [],
        }
        self.store.save(run)
        return run

    @staticmethod
    def _attempt_key(action_type: str, target: str, normalized_input: Mapping[str, Any]) -> str:
        return _digest({"action_type": action_type, "target": target, "input": dict(normalized_input)})

    def _escalation(self, run: dict[str, Any], reason_code: str, decision_needed: str) -> dict[str, Any]:
        attempts = run.get("attempts") or []
        finished = [attempt for attempt in attempts if attempt.get("status") == "finished"]
        last = finished[-1] if finished else None
        if last is None:
            what_changed = "none"
        elif last.get("progress"):
            what_changed = str(last.get("expected_progress") or "progress")
        else:
            what_changed = f"none since attempt {last['sequence']}"
        return {
            "state": "paused",
            "reason_code": reason_code,
            "last_successful_checkpoint": (run.get("last_progress") or {}).get("evidence_hash"),
            "attempt_summary": {
                "actions": run["counters"]["actions"],
                "consecutive_no_progress": run["counters"]["consecutive_no_progress"],
                "attempt_count": len(attempts),
                "last_action": None if last is None else last.get("action_type"),
                "last_target": None if last is None else last.get("target"),
                "last_key": None if last is None else last.get("key"),
            },
            "what_changed": what_changed,
            "decision_needed": decision_needed,
            "resume_requirements": ["changed input or explicit Lead decision"],
        }

    def _pause(self, run: dict[str, Any], reason_code: str, decision_needed: str) -> None:
        run["state"] = "paused"
        run["wait"] = None
        run["blocker"] = {
            "reason_code": reason_code,
            "decision_needed": decision_needed,
            "at": self._stamp(),
            "last_successful_checkpoint": (run.get("last_progress") or {}).get("evidence_hash"),
        }
        run["escalation"] = self._escalation(run, reason_code, decision_needed)

    def _enforce_deadline(self, run: dict[str, Any]) -> None:
        deadline = _parse_time(run.get("deadline_at"))
        if deadline is not None and self._now_dt() >= deadline:
            self._pause(run, "deadline_exceeded", "Lead must extend the deadline with a new hypothesis")
            self.store.save(run)
            raise GuardError("deadline_exceeded", "run deadline has elapsed; run is paused")

    def _enforce_budget(self, run: dict[str, Any], action_type: str) -> None:
        self._enforce_deadline(run)
        counters = run["counters"]
        budget = run["budget"]
        if counters["actions"] >= budget["max_actions"]:
            self._pause(run, "budget_exhausted", "Lead must grant a bounded budget increase with a new hypothesis")
            self.store.save(run)
            raise GuardError("budget_exhausted", "action budget is exhausted; run is paused")
        counter_key = {"tool": "tool_calls", "model": "model_turns", "delegation": "delegations", "review": "review_rounds"}.get(action_type)
        if counter_key and counters[counter_key] >= budget[f"max_{counter_key}"]:
            self._pause(run, "budget_exhausted", f"{counter_key} budget is exhausted")
            self.store.save(run)
            raise GuardError("budget_exhausted", f"{counter_key} budget is exhausted; run is paused")

    def _reject_waiting(self, run: dict[str, Any]) -> None:
        if run["state"] != "waiting":
            return
        wait = run.get("wait") if isinstance(run.get("wait"), Mapping) else {}
        deadline = _parse_time((wait or {}).get("deadline_at"))
        if deadline is None or self._now_dt() >= deadline:
            self._pause(run, "dependency_deadline", "Lead must break the wait, replace the dependency, or extend its deadline")
            self.store.save(run)
            raise GuardError("dependency_deadline", "waiting deadline elapsed; run is paused")
        raise GuardError("waiting", "task is waiting on a named dependency and cannot admit new work")

    def admit(
        self,
        task_id: str,
        action_type: str,
        target: str,
        normalized_input: Mapping[str, Any] | None,
        expected_progress: str,
        hypothesis: str | None = None,
    ) -> dict[str, Any]:
        run = self._require_active(task_id)
        self._reject_waiting(run)
        action_type = _safe_id(action_type, "action_type")
        target = _safe_text(target, "target")
        expected_progress = _safe_text(expected_progress, "expected_progress")
        if hypothesis is not None:
            hypothesis = _safe_text(hypothesis, "hypothesis")
        payload = dict(normalized_input or {})
        finding_id = payload.get("finding_id")
        if finding_id is not None:
            finding_id = _safe_id(str(finding_id), "finding_id")
        if action_type == "review" and finding_id:
            prior_reviews = [
                attempt
                for attempt in run["attempts"]
                if attempt.get("action_type") == "review"
                and attempt.get("finding_id") == finding_id
                and attempt.get("status") == "finished"
            ]
            if len(prior_reviews) >= 2:
                self._pause(run, "review_deadlock", "Lead must adjudicate the repeated review finding")
                self.store.save(run)
                raise GuardError("review_deadlock", "review finding exceeded its reopen budget; run is paused")
        self._enforce_budget(run, action_type)
        key = self._attempt_key(action_type, target, payload)
        matching = [attempt for attempt in run["attempts"] if attempt["key"] == key]
        if matching:
            latest = matching[-1]
            if latest["status"] == "started":
                raise GuardError("duplicate_inflight", "an identical action is already admitted")
            if latest["outcome"] not in {"transient", "unknown"}:
                raise GuardError("duplicate_action", "identical action already has a non-retryable outcome")
            retry_count = sum(attempt.get("outcome") in {"transient", "unknown"} for attempt in matching)
            if retry_count >= MAX_TRANSIENT_RETRIES:
                self._pause(run, "retry_exhausted", "Provide changed input, a new hypothesis, or an external decision")
                self.store.save(run)
                raise GuardError("retry_exhausted", "identical action exhausted its retry budget; run is paused")
            if not hypothesis:
                raise GuardError("missing_hypothesis", "retry requires a concrete hypothesis")
            if retry_count >= 2:
                finished_at = _parse_time(latest.get("finished_at"), "finished_at")
                if finished_at is not None:
                    due = finished_at + timedelta(seconds=_retry_delay_seconds(retry_count))
                    if self._now_dt() < due:
                        raise GuardError("backoff_pending", f"retry is not due until {due.isoformat()}")
        attempt = {
            "sequence": len(run["attempts"]) + 1,
            "key": key,
            "action_type": action_type,
            "target": target,
            "finding_id": finding_id,
            "input_hash": _digest(payload),
            "expected_progress": expected_progress,
            "hypothesis": hypothesis,
            "status": "started",
            "outcome": None,
            "progress": None,
            "evidence_hash": None,
            "started_at": self._stamp(),
            "finished_at": None,
        }
        run["attempts"].append(attempt)
        run["state"] = "active"
        run["counters"]["actions"] += 1
        counter_key = {"tool": "tool_calls", "model": "model_turns", "delegation": "delegations", "review": "review_rounds"}.get(action_type)
        if counter_key:
            run["counters"][counter_key] += 1
        self.store.save(run)
        return attempt

    def finish(
        self,
        task_id: str,
        sequence: int,
        outcome: str,
        progress: bool,
        evidence: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        run = self._require_active(task_id)
        if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
            raise GuardError("invalid_input", "sequence must be a positive integer")
        if outcome not in {"succeeded", "transient", "input_or_contract", "permission_or_policy", "environment", "unknown"}:
            raise GuardError("invalid_outcome", "outcome is invalid")
        if not isinstance(progress, bool):
            raise GuardError("invalid_input", "progress must be boolean")
        if sequence > len(run["attempts"]):
            raise GuardError("unknown_attempt", "attempt sequence does not exist")
        attempt = run["attempts"][sequence - 1]
        if attempt["status"] != "started":
            raise GuardError("attempt_finished", "attempt is already finished")
        evidence_hash = _digest(dict(evidence or {})) if evidence else None
        attempt.update(
            {
                "status": "finished",
                "outcome": outcome,
                "progress": progress,
                "evidence_hash": evidence_hash,
                "finished_at": self._stamp(),
            }
        )
        if progress:
            run["counters"]["consecutive_no_progress"] = 0
            run["last_progress"] = {"at": self._stamp(), "kind": attempt["expected_progress"], "evidence_hash": evidence_hash}
        else:
            run["counters"]["consecutive_no_progress"] += 1
        if outcome == "permission_or_policy":
            run["state"] = "blocked"
            run["blocker"] = {"reason_code": outcome, "decision_needed": "Required approval or permission is unavailable", "at": self._stamp()}
            run["escalation"] = self._escalation(run, outcome, "Required approval or permission is unavailable")
            run["escalation"]["state"] = "blocked"
        elif outcome in {"input_or_contract", "environment"}:
            self._pause(run, outcome, "Change the input/contract or resolve the named environment prerequisite")
        elif run["counters"]["consecutive_no_progress"] >= run["budget"]["max_no_progress"]:
            self._pause(run, "no_progress", "Provide new evidence, changed input, or a Lead decision")
        elif run["state"] == "active":
            run["state"] = "ready"
        self.store.save(run)
        return run

    def dependencies(self, task_id: str, dependency_ids: Iterable[str], deadline_at: str | None = None) -> dict[str, Any]:
        run = self._require_active(task_id)
        dependencies = sorted({_safe_id(value, "dependency_id") for value in dependency_ids})
        if run["task_id"] in dependencies:
            self._pause(run, "dependency_cycle", f"{run['task_id']} depends on itself")
            self.store.save(run)
            raise GuardError("dependency_cycle", "task cannot depend on itself")
        for dependency_id in dependencies:
            dependency = self.store.load(dependency_id)
            if dependency is None or dependency["workspace_id"] != run["workspace_id"]:
                raise GuardError("missing_dependency", "dependency does not exist in the same workspace")
        if dependencies:
            deadline = _parse_time(deadline_at)
            if deadline is None:
                raise GuardError("missing_deadline", "waiting requires a named dependency deadline")
            run["wait"] = {"deadline_at": deadline.isoformat(), "expected": "terminal"}
            run["state"] = "waiting"
        else:
            run["wait"] = None
            run["state"] = "ready"
        run["dependencies"] = dependencies
        self.store.save(run)
        self.detect_cycles(run["workspace_id"])
        loaded = self.store.load(run["task_id"]) or run
        if loaded["state"] == "waiting" and deadline_at:
            deadline = _parse_time(deadline_at)
            if deadline is not None and self._now_dt() >= deadline:
                self._pause(loaded, "dependency_deadline", "Lead must break the wait, replace the dependency, or extend its deadline")
                self.store.save(loaded)
                raise GuardError("dependency_deadline", "waiting deadline elapsed; run is paused")
        return loaded

    def wake(self, task_id: str) -> dict[str, Any]:
        run = self._require_active(task_id)
        if run["state"] != "waiting":
            return run
        unresolved: list[str] = []
        for dependency_id in run["dependencies"]:
            dependency = self.store.load(dependency_id)
            if dependency is None or dependency["state"] not in TERMINAL_STATES:
                unresolved.append(dependency_id)
        if not unresolved:
            run["state"] = "ready"
            run["wait"] = None
            self.store.save(run)
            return run
        wait = run.get("wait") if isinstance(run.get("wait"), Mapping) else {}
        deadline = _parse_time((wait or {}).get("deadline_at"))
        if deadline is None or self._now_dt() >= deadline:
            self._pause(run, "dependency_deadline", "Lead must break the wait, replace the dependency, or extend its deadline")
            self.store.save(run)
            raise GuardError("dependency_deadline", "waiting deadline elapsed; run is paused")
        raise GuardError("waiting", "named dependencies are not terminal")

    def resume(self, task_id: str, hypothesis: str, budget: Mapping[str, Any] | None = None) -> dict[str, Any]:
        run = self.store.load(_safe_id(task_id, "task_id"))
        if run is None:
            raise GuardError("unknown_task", "task does not exist")
        if run["state"] in TERMINAL_STATES:
            raise GuardError("terminal_task", "task is already terminal")
        if run["state"] != "paused":
            raise GuardError("not_paused", "only a paused task can be resumed by Lead")
        hypothesis = _safe_text(hypothesis, "hypothesis")
        raw_budget = dict(budget or {})
        if "deadline_at" in raw_budget:
            deadline = _parse_time(raw_budget.get("deadline_at"))
            run["deadline_at"] = deadline.isoformat() if deadline else None
        if raw_budget:
            merged = dict(run["budget"])
            merged.update({key: value for key, value in raw_budget.items() if key != "deadline_at"})
            run["budget"] = _budget(merged)
        run["state"] = "ready"
        run["blocker"] = None
        run["wait"] = None
        run["resume"] = {"at": self._stamp(), "hypothesis": hypothesis}
        self.store.save(run)
        return run

    def observe_herdr_event(self, workspace_id: str, event: Mapping[str, Any]) -> list[dict[str, Any]]:
        workspace_id = _safe_id(workspace_id, "workspace_id")
        if not isinstance(event, Mapping):
            raise GuardError("invalid_event", "Herdr event must be an object")
        safe_event = {key: event[key] for key in ("type", "pane_id", "agent", "agent_status", "workspace_id") if isinstance(event.get(key), str)}
        if not safe_event:
            return []
        changed = []
        for run in self.store.runs(workspace_id):
            history = run.setdefault("herdr_events", [])
            history.append({"at": self._stamp(), **safe_event})
            del history[:-20]
            self.store.save(run)
            changed.append(run)
        return changed

    def detect_cycles(self, workspace_id: str) -> list[list[str]]:
        runs = {run["task_id"]: run for run in self.store.runs(workspace_id) if run["state"] in ACTIVE_STATES}
        graph = {task_id: [dep for dep in run["dependencies"] if dep in runs] for task_id, run in runs.items()}
        index = 0
        indices: dict[str, int] = {}
        lowlinks: dict[str, int] = {}
        stack: list[str] = []
        on_stack: set[str] = set()
        components: list[list[str]] = []

        def visit(node: str) -> None:
            nonlocal index
            indices[node] = index
            lowlinks[node] = index
            index += 1
            stack.append(node)
            on_stack.add(node)
            for neighbor in graph[node]:
                if neighbor not in indices:
                    visit(neighbor)
                    lowlinks[node] = min(lowlinks[node], lowlinks[neighbor])
                elif neighbor in on_stack:
                    lowlinks[node] = min(lowlinks[node], indices[neighbor])
            if lowlinks[node] == indices[node]:
                component = []
                while True:
                    member = stack.pop()
                    on_stack.remove(member)
                    component.append(member)
                    if member == node:
                        break
                if len(component) > 1 or node in graph[node]:
                    components.append(sorted(component))

        for task_id in graph:
            if task_id not in indices:
                visit(task_id)
        for component in components:
            cycle_path = " -> ".join([*component, component[0]])
            for task_id in component:
                run = runs[task_id]
                self._pause(run, "dependency_cycle", f"Lead must break dependency cycle: {cycle_path}")
                self.store.save(run)
        return components

    def complete(self, task_id: str, evidence: Mapping[str, Any] | None = None) -> dict[str, Any]:
        run = self.store.load(_safe_id(task_id, "task_id"))
        if run is None:
            raise GuardError("unknown_task", "task does not exist")
        if run["state"] == "completed":
            return run
        if run["state"] in TERMINAL_STATES:
            raise GuardError("terminal_task", "task is already terminal")
        if run["state"] == "paused":
            raise GuardError("paused", "task is paused and requires a Lead decision")
        if run["state"] == "waiting":
            raise GuardError("waiting", "task is waiting on a named dependency and cannot complete")
        run["state"] = "completed"
        run["wait"] = None
        run["resolution"] = {"at": self._stamp(), "evidence_hash": _digest(dict(evidence or {})) if evidence else None}
        self.store.save(run)
        return run

    def status(self, workspace_id: str | None = None) -> list[dict[str, Any]]:
        return self.store.runs(workspace_id)

    def _require_active(self, task_id: str) -> dict[str, Any]:
        run = self.store.load(task_id)
        if run is None:
            raise GuardError("unknown_task", "task does not exist")
        if run["state"] in TERMINAL_STATES:
            raise GuardError("terminal_task", "task is already terminal")
        if run["state"] == "paused":
            raise GuardError("paused", "task is paused and requires a Lead decision")
        return run


def _json_arg(value: str) -> Mapping[str, Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise GuardError("invalid_json", "JSON argument is invalid") from error
    if not isinstance(parsed, Mapping):
        raise GuardError("invalid_json", "JSON argument must be an object")
    return parsed


def _output(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AIGC Herdr anti-loop guard")
    parser.add_argument("--state-dir", default=os.environ.get("AIGC_ANTI_LOOP_STATE_DIR", DEFAULT_STATE_DIR))
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("start")
    start.add_argument("--task-id", required=True)
    start.add_argument("--workspace-id", required=True)
    start.add_argument("--owner", required=True)
    start.add_argument("--objective", required=True)
    start.add_argument("--acceptance", required=True)
    start.add_argument("--inputs", default="{}")
    start.add_argument("--budget", default="{}")
    admit = sub.add_parser("admit")
    admit.add_argument("--task-id", required=True)
    admit.add_argument("--action-type", required=True)
    admit.add_argument("--target", required=True)
    admit.add_argument("--input", default="{}")
    admit.add_argument("--expected-progress", required=True)
    admit.add_argument("--hypothesis")
    finish = sub.add_parser("finish")
    finish.add_argument("--task-id", required=True)
    finish.add_argument("--sequence", type=int, required=True)
    finish.add_argument("--outcome", required=True)
    finish.add_argument("--progress", choices=("yes", "no"), required=True)
    finish.add_argument("--evidence", default="{}")
    dependencies = sub.add_parser("dependencies")
    dependencies.add_argument("--task-id", required=True)
    dependencies.add_argument("--depends-on", action="append", default=[])
    dependencies.add_argument("--deadline")
    wake = sub.add_parser("wake")
    wake.add_argument("--task-id", required=True)
    resume = sub.add_parser("resume")
    resume.add_argument("--task-id", required=True)
    resume.add_argument("--hypothesis", required=True)
    resume.add_argument("--budget", default="{}")
    complete = sub.add_parser("complete")
    complete.add_argument("--task-id", required=True)
    complete.add_argument("--evidence", default="{}")
    status = sub.add_parser("status")
    status.add_argument("--workspace-id")
    event = sub.add_parser("event")
    event.add_argument("--workspace-id", required=True)
    event.add_argument("--payload", required=True)
    sweep = sub.add_parser("sweep")
    sweep.add_argument("--workspace-id", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    guard = AntiLoopGuard(args.state_dir)
    try:
        if args.command == "start":
            result = guard.start(args.task_id, args.workspace_id, args.owner, args.objective, args.acceptance, _json_arg(args.inputs), _json_arg(args.budget))
        elif args.command == "admit":
            result = guard.admit(args.task_id, args.action_type, args.target, _json_arg(args.input), args.expected_progress, args.hypothesis)
        elif args.command == "finish":
            result = guard.finish(args.task_id, args.sequence, args.outcome, args.progress == "yes", _json_arg(args.evidence))
        elif args.command == "dependencies":
            result = guard.dependencies(args.task_id, args.depends_on, args.deadline)
        elif args.command == "wake":
            result = guard.wake(args.task_id)
        elif args.command == "resume":
            result = guard.resume(args.task_id, args.hypothesis, _json_arg(args.budget))
        elif args.command == "complete":
            result = guard.complete(args.task_id, _json_arg(args.evidence))
        elif args.command == "status":
            result = guard.status(args.workspace_id)
        elif args.command == "event":
            result = guard.observe_herdr_event(args.workspace_id, _json_arg(args.payload))
        else:
            result = {"cycles": guard.detect_cycles(args.workspace_id)}
    except GuardError as error:
        _output({"ok": False, "error": error.as_dict()})
        return 1
    _output({"ok": True, "result": result})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
