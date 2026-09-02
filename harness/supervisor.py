"""Project-scoped supervisor for Herdr agent lifecycle recovery.

Herdr owns panes and agent detection. This process owns the durable mapping
between active anti-loop runs and those panes, and reconciles missed lifecycle
signals without living inside an agent pane.
"""
import argparse
import hashlib
import json
import os
import subprocess
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
ROLE_BINDINGS = {
    "aigc-lead-codex": ("AIGC Lead Codex", "codex"),
    "aigc-build-codex": ("AIGC Build Codex", "codex"),
    "aigc-product-omp": ("AIGC Product OMP", "omp"),
    "aigc-quality-omp": ("AIGC Quality OMP", "omp"),
}

ACTIVE_RUN_STATES = {"ready", "active", "verifying"}
RECOVERABLE_AGENT_STATES = {"idle", "done"}
DEFAULT_INTERVAL = 5.0
DEFAULT_QUIET_SECONDS = 300.0
DEFAULT_ROTATE_EVENT_COUNT = 900
DEFAULT_ROTATE_TOKEN_ESTIMATE = 110000
CONTEXT_CHECKPOINT_VERSION = "aigc-context-checkpoint/v1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _task_digest(task_id: str) -> str:
    return hashlib.sha256(_canonical(task_id).encode("utf-8")).hexdigest()


def _is_project_path(value: Any, project_root: Path) -> bool:
    try:
        return Path(str(value)).resolve() == project_root
    except (OSError, RuntimeError):
        return False


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return default


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _parse_time(value: Any) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _safe_project_root(project_root: str | Path) -> Path:
    root = Path(project_root).resolve()
    if not root.is_dir() or not (root / "AGENTS.md").is_file():
        raise ValueError("project root must contain AGENTS.md")
    return root


class SingleInstance:
    """Small cross-process lock; stale owners are recoverable by PID check."""

    def __init__(self, path: Path):
        self.path = path
        self.handle = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.handle = self.path.open("x", encoding="ascii")
            self.handle.write(str(os.getpid()))
            self.handle.flush()
            return True
        except FileExistsError:
            try:
                pid = int(self.path.read_text(encoding="ascii").strip())
                os.kill(pid, 0)
            except (FileNotFoundError, ValueError, OSError):
                try:
                    self.path.unlink()
                except OSError:
                    return False
                return self.acquire()
            return False

    def release(self) -> None:
        if self.handle is not None:
            self.handle.close()
            self.handle = None
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass


class HerdrSupervisor:
    def __init__(
        self,
        project_root: str | Path,
        state_dir: str | Path | None = None,
        interval: float = DEFAULT_INTERVAL,
        quiet_seconds: float = DEFAULT_QUIET_SECONDS,
        rotate_event_count: int = DEFAULT_ROTATE_EVENT_COUNT,
        rotate_token_estimate: int = DEFAULT_ROTATE_TOKEN_ESTIMATE,
        herdr_command: str = "herdr",
    ):
        self.project_root = _safe_project_root(project_root)
        self.state_dir = Path(state_dir or self.project_root / ".agents/runtime/herdr-supervisor")
        self.interval = max(1.0, interval)
        self.quiet_seconds = max(30.0, quiet_seconds)
        self.rotate_event_count = max(1, rotate_event_count)
        self.rotate_token_estimate = max(1, rotate_token_estimate)
        self.herdr_command = herdr_command
        self.status_path = self.state_dir / "status.json"
        self.actions_path = self.state_dir / "actions.jsonl"
        self.lock = SingleInstance(self.state_dir / "supervisor.lock")
        self.checkpoint_dir = self.state_dir / "context-checkpoints"

    def _run_files(self) -> list[Path]:
        root = self.project_root / ".agents/runtime/herdr-anti-loop"
        try:
            return sorted(root.glob("*.json"))
        except OSError:
            return []

    def _persist_run(self, run: Mapping[str, Any]) -> None:
        path = self.project_root / ".agents/runtime/herdr-anti-loop" / f"{_task_digest(str(run['task_id']))}.json"
        _write_json(path, dict(run))

    def load_runs(self) -> list[dict[str, Any]]:
        runs = []
        for path in self._run_files():
            value = _read_json(path, None)
            if isinstance(value, dict) and value.get("state") in ACTIVE_RUN_STATES:
                runs.append(value)
        return runs

    def snapshot(self) -> dict[str, Any]:
        completed = subprocess.run(
            [self.herdr_command, "api", "snapshot"],
            cwd=self.project_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError("Herdr snapshot failed")
        payload = json.loads(completed.stdout)
        snapshot = payload.get("result", {}).get("snapshot", payload.get("snapshot"))
        if not isinstance(snapshot, dict):
            raise RuntimeError("Herdr snapshot response is invalid")
        return snapshot
    def _session_usage(self, agent: Mapping[str, Any]) -> tuple[int, int] | None:
        """Read only token/event metadata from the Codex JSONL ledger."""
        identity = agent.get("agent_session")
        session_id = identity.get("value") if isinstance(identity, Mapping) else None
        if not isinstance(session_id, str) or not session_id:
            return None
        roots = [Path.home() / ".codex" / "sessions"]
        candidates: list[Path] = []
        for root in roots:
            if root.is_dir():
                candidates.extend(root.glob(f"**/*{session_id}*.jsonl"))
        if not candidates:
            return None
        events = 0
        tokens = 0
        try:
            with max(candidates, key=lambda path: path.stat().st_mtime).open("r", encoding="utf-8") as handle:
                for line in handle:
                    events += 1
                    try:
                        payload = json.loads(line).get("payload", {})
                        usage = payload.get("info", {}).get("total_token_usage", {})
                        tokens = max(tokens, int(usage.get("total_tokens", 0) or 0))
                    except (ValueError, TypeError, AttributeError):
                        continue
        except (OSError, ValueError):
            return None
        return events, tokens

    def _project_agents(self, snapshot: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
        agents = snapshot.get("agents", [])
        result = {}
        for agent in agents if isinstance(agents, list) else []:
            if not isinstance(agent, dict) or not _is_project_path(agent.get("cwd"), self.project_root):
                continue
            name = agent.get("name")
            if isinstance(name, str) and name:
                result[name] = agent
        return result

    def _rebind_unnamed_roles(self, snapshot: Mapping[str, Any], named: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Restore a configured role on its labeled pane without killing it."""
        agents = snapshot.get("agents", [])
        if not isinstance(agents, list):
            return [], []
        used_panes = {str(value.get("pane_id")) for value in named.values() if value.get("pane_id")}
        actions: list[dict[str, Any]] = []
        findings: list[dict[str, Any]] = []
        for role, (label, runtime) in ROLE_BINDINGS.items():
            if role in named:
                continue
            candidate = next(
                (
                    value for value in agents
                    if isinstance(value, dict)
                    and str(value.get("pane_id", "")) not in used_panes
                    and _is_project_path(value.get("cwd"), self.project_root)
                    and str(value.get("label", value.get("terminal_title_stripped", ""))).strip() == label
                    and (not value.get("agent") or value.get("agent") == runtime)
                ),
                None,
            )
            if candidate is None:
                continue
            pane_id = str(candidate.get("pane_id", ""))
            if not pane_id:
                continue
            if candidate.get("agent_status") == "unknown":
                started = subprocess.run(
                    [self.herdr_command, "agent", "start", role, "--kind", runtime, "--pane", pane_id, "--timeout", "60000"],
                    cwd=self.project_root,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=90,
                    check=False,
                )
                action = {"at": _now(), "kind": "role_rebind", "role": role, "pane_id": pane_id, "result": "started" if started.returncode == 0 else "start_failed"}
                actions.append(action)
                self._record_action(action)
                if started.returncode == 0:
                    used_panes.add(pane_id)
                    continue
            renamed = subprocess.run(
                [self.herdr_command, "agent", "rename", pane_id, role],
                cwd=self.project_root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
            )
            action = {"at": _now(), "kind": "role_rebind", "role": role, "pane_id": pane_id, "result": "renamed" if renamed.returncode == 0 else "rename_failed"}
            actions.append(action)
            self._record_action(action)
            if renamed.returncode != 0:
                findings.append({"code": "role_rebind_failed", "role": role, "pane_id": pane_id})
            else:
                used_panes.add(pane_id)
        return actions, findings

    def _record_action(self, action: Mapping[str, Any]) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        with self.actions_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(_canonical(dict(action)) + "\n")

    def _checkpoint_path(self, task_id: str, generation: int) -> Path:
        return self.checkpoint_dir / f"{_task_digest(task_id)}-{generation}.json"

    def _write_context_checkpoint(self, run: Mapping[str, Any], generation: int) -> Path:
        """Persist bounded, secret-free recovery metadata before compaction."""
        task_id = str(run["task_id"])
        checkpoint = {
            "version": CONTEXT_CHECKPOINT_VERSION,
            "task_id": task_id,
            "generation": generation,
            "owner": str(run.get("owner", "")),
            "state": str(run.get("state", "")),
            "input_fingerprint": str(run.get("input_fingerprint", "")),
            "counters": dict(run.get("counters") or {}),
            "last_progress": run.get("last_progress"),
            "dependencies": list(run.get("dependencies") or []),
            "created_at": _now(),
        }
        path = self._checkpoint_path(task_id, generation)
        _write_json(path, checkpoint)
        return path

    def _rotate_context_once(self, run: dict[str, Any], agent: Mapping[str, Any], event_count: int, token_estimate: int) -> dict[str, Any]:
        """Create a handoff checkpoint; never inject a fragile in-session command."""
        rotation = dict(run.get("rotation") or {})
        generation = int(rotation.get("generation", 0) or 0) + 1
        checkpoint_path = self._write_context_checkpoint(run, generation)
        action = {
            "at": _now(),
            "kind": "context_handoff_ready",
            "task_id": str(run["task_id"]),
            "owner": run.get("owner"),
            "agent": agent.get("name"),
            "pane_id": agent.get("pane_id"),
            "revision_before": agent.get("revision"),
            "generation": generation,
            "event_count": event_count,
            "token_estimate": token_estimate,
            "checkpoint": checkpoint_path.name,
            "result": "ready",
        }
        rotation.update({"generation": generation, "base_event_count": event_count, "base_token_estimate": token_estimate, "event_count": event_count, "token_estimate": token_estimate, "state": "handoff_ready", "checkpoint": checkpoint_path.name, "last_attempt_at": action["at"], "revision_before": agent.get("revision")})
        run["rotation"] = rotation
        self._persist_run(run)
        self._record_action(action)
        return action
    def _start_handoff_agent(self, run: dict[str, Any], agent: Mapping[str, Any]) -> dict[str, Any]:
        """Start a fresh configured agent only after durable handoff exists."""
        rotation = dict(run.get("rotation") or {})
        pane_id = str(agent.get("pane_id", ""))
        task_id = str(run["task_id"])
        checkpoint = str(rotation.get("checkpoint", ""))
        owner = str(run.get("owner", ""))
        runtime = ROLE_BINDINGS.get(owner, ("", "codex"))[1]
        action = {
            "at": _now(),
            "kind": "context_handoff_start",
            "task_id": task_id,
            "owner": owner,
            "pane_id": pane_id,
            "checkpoint": checkpoint,
        }
        if not pane_id:
            action.update({"result": "failed", "error": "agent pane is unavailable"})
        else:
            prompt = f"Resume task {task_id} from durable checkpoint {checkpoint}. Do not repeat completed actions."
            started = subprocess.run(
                [
                    self.herdr_command,
                    "agent",
                    "start",
                    owner,
                    "--kind",
                    runtime,
                    "--pane",
                    pane_id,
                    "--timeout",
                    "60000",
                    "--",
                    prompt,
                ],
                cwd=self.project_root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=90,
                check=False,
            )
            action["result"] = "started" if started.returncode == 0 else "failed"
        if action["result"] == "started":
            rotation["state"] = "handoff_started"
            rotation["last_started_at"] = action["at"]
            run["rotation"] = rotation
            self._persist_run(run)
        self._record_action(action)
        return action

    def _prompt_once(self, run: dict[str, Any], agent: Mapping[str, Any], reason: str) -> dict[str, Any]:
        task_id = str(run["task_id"])
        generation = int(run.get("supervisor_generation", 0)) + 1
        nonce = f"{task_id}:{generation}:{uuid.uuid4().hex[:12]}"
        action = {"at": _now(), "kind": "resume_prompt", "task_id": task_id, "owner": run.get("owner"), "agent": agent.get("name"), "nonce": nonce, "reason": reason}
        prompt = (
            f"Supervisor recovery nonce {nonce}. Resume task {task_id} from its latest durable checkpoint. "
            "Perform one atomic acceptance-relevant action, report verifiable progress, and stop if blocked."
        )
        result = subprocess.run(
            [self.herdr_command, "agent", "prompt", str(agent["name"]), prompt],
            cwd=self.project_root, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30, check=False,
        )
        action["result"] = "submitted" if result.returncode == 0 else "failed"
        self._record_action(action)
        run["supervisor_generation"] = generation
        run["last_supervisor_action_at"] = action["at"]
        run["last_supervisor_action"] = action["kind"]
        run["last_supervisor_result"] = action["result"]
        self._persist_run(run)
        return action

    def reconcile(self) -> dict[str, Any]:
        generated_at = _now()
        report: dict[str, Any] = {
            "version": 1,
            "generated_at": generated_at,
            "project_root": str(self.project_root).replace("\\", "/"),
            "mode": "reconcile",
            "actions": [],
            "findings": [],
        }
        try:
            snapshot = self.snapshot()
        except (OSError, RuntimeError, subprocess.SubprocessError, json.JSONDecodeError) as error:
            report["mode"] = "degraded"
            report["findings"].append({"code": "herdr_unavailable", "detail": str(error)[:200]})
            _write_json(self.status_path, report)
            return report

        agents = self._project_agents(snapshot)
        rebind_actions, rebind_findings = self._rebind_unnamed_roles(snapshot, agents)
        report["actions"].extend(rebind_actions)
        report["findings"].extend(rebind_findings)
        if rebind_actions:
            # The same snapshot is intentionally not reused for prompting. A
            # rebind must be observed by a later cycle before recovery.
            agents = self._project_agents(snapshot)
        report["agents"] = [
            {"name": name, "pane_id": value.get("pane_id"), "status": value.get("agent_status"), "revision": value.get("revision")}
            for name, value in sorted(agents.items())
        ]
        now = time.time()
        for run in self.load_runs():
            owner = run.get("owner")
            agent = agents.get(owner) if isinstance(owner, str) else None
            if agent is None:
                report["findings"].append({"task_id": run.get("task_id"), "code": "agent_missing"})
                continue
            agent_status = agent.get("agent_status")
            last_action_at = _parse_time(run.get("last_supervisor_action_at"))
            if agent_status in RECOVERABLE_AGENT_STATES and (last_action_at is None or now - last_action_at >= self.quiet_seconds):
                report["actions"].append(self._prompt_once(run, agent, f"agent_{agent_status}"))
            usage = self._session_usage(agent)
            if usage is not None:
                counters = dict(run.get("counters") or {})
                counters["event_count"], counters["token_estimate"] = usage
                run["counters"] = counters
                self._persist_run(run)
            else:
                counters = run.get("counters", {})
            rotation = dict(run.get("rotation") or {})
            event_count = max(int(counters.get("event_count", 0) or 0), int(rotation.get("event_count", 0) or 0))
            token_estimate = max(int(counters.get("token_estimate", 0) or 0), int(rotation.get("token_estimate", 0) or 0))
            base_events = int(rotation.get("base_event_count", 0) or 0)
            base_tokens = int(rotation.get("base_token_estimate", 0) or 0)
            due = event_count - base_events >= self.rotate_event_count or token_estimate - base_tokens >= self.rotate_token_estimate
            if rotation.get("state") in {"submitted", "handoff_started"}:
                before_revision = rotation.get("revision_before")
                current_revision = agent.get("revision")
                if before_revision is not None and current_revision is not None and current_revision != before_revision:
                    rotation.update({"state": "completed", "completed_at": _now(), "revision_after": current_revision, "base_event_count": event_count, "base_token_estimate": token_estimate})
                    run["rotation"] = rotation
                    self._persist_run(run)
                    report["findings"].append({"task_id": run.get("task_id"), "code": "handoff_completed", "generation": rotation.get("generation"), "checkpoint": rotation.get("checkpoint")})
                else:
                    report["findings"].append({"task_id": run.get("task_id"), "code": "handoff_pending", "checkpoint": rotation.get("checkpoint")})
            elif rotation.get("state") == "handoff_ready":
                report["actions"].append(self._start_handoff_agent(run, agent))
            elif due and rotation.get("state") not in {"completed"}:
                report["actions"].append(self._rotate_context_once(run, agent, event_count, token_estimate))

            progress_at = _parse_time(run.get("last_progress_at"))
            if progress_at is not None and now - progress_at >= self.quiet_seconds and agent_status == "working":
                report["findings"].append({"task_id": run.get("task_id"), "code": "progress_quiet", "seconds": int(now - progress_at)})
        _write_json(self.status_path, report)
        return report

    def run_forever(self) -> int:
        if not self.lock.acquire():
            return 0
        try:
            while True:
                self.reconcile()
                time.sleep(self.interval)
        except KeyboardInterrupt:
            return 0
        finally:
            self.lock.release()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AIGC Herdr project supervisor")
    parser.add_argument("--project-root", default=os.environ.get("AIGC_PROJECT_ROOT", "."))
    parser.add_argument("--state-dir", default=os.environ.get("AIGC_SUPERVISOR_STATE_DIR"))
    parser.add_argument("--interval", type=float, default=float(os.environ.get("AIGC_SUPERVISOR_INTERVAL", DEFAULT_INTERVAL)))
    parser.add_argument("--quiet-seconds", type=float, default=DEFAULT_QUIET_SECONDS)
    parser.add_argument("--herdr-command", default="herdr")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("start")
    sub.add_parser("run")
    sub.add_parser("status")
    sub.add_parser("reconcile")
    sub.add_parser("stop")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    supervisor = HerdrSupervisor(args.project_root, args.state_dir, args.interval, args.quiet_seconds, herdr_command=args.herdr_command)
    if args.command == "status":
        print(json.dumps(supervisor.status(), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.command == "reconcile":
        print(json.dumps(supervisor.reconcile(), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.command in {"start", "run"}:
        return supervisor.run_forever()
    if args.command == "stop":
        print(json.dumps({"ok": False, "error": "stop requires the supervisor process owner; terminate it through the service manager"}))
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
