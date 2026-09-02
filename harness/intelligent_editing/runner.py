"""Resumable, non-destructive orchestrator for the local intelligent mix API."""

from __future__ import annotations

import argparse
import copy
import hashlib
import ipaddress
import json
import os
import re
import sys
import tempfile
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from skills.intelligent_editing.runtime import (
    PLAN_VERSION,
    RUN_VERSION,
    PlanValidationError,
    build_render_payload,
    canonical_json,
    make_local_plan,
    plan_hash,
    planner_payload,
    validate_managed_asset_id,
    validate_plan,
    validate_request,
)


class HarnessError(RuntimeError):
    """Stable, user-safe Harness failure."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


def _safe_request_id(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _safe_mix_id(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value
    ):
        raise HarnessError("invalid_mix_response", "混剪服务返回了无效任务")
    return value


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_loopback_host(hostname: str | None) -> bool:
    if not hostname:
        return False
    if hostname.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _safe_error(error: Exception) -> HarnessError:
    if isinstance(error, HarnessError):
        return error
    if isinstance(error, PlanValidationError):
        return HarnessError(error.code, error.message)
    if isinstance(error, (HTTPError, URLError, TimeoutError, OSError)):
        return HarnessError("api_unavailable", "本地 AIGC API 暂时不可用")
    return HarnessError("harness_error", "智能剪辑任务未完成")


def _safe_asset_record(record: Mapping[str, Any]) -> dict[str, Any]:
    result = {key: record[key] for key in ("id", "media_type", "size") if key in record}
    if not isinstance(result.get("id"), str) or not isinstance(result.get("media_type"), str):
        raise HarnessError("invalid_asset_record", "素材库返回了无效素材")
    return result


class LocalApiClient:
    """Small API client with a test injection point."""

    def __init__(self, base_url: str = "http://127.0.0.1:8001", transport: Callable[[str, str, Any | None], Any] | None = None):
        parts = urlsplit(base_url)
        if (
            parts.scheme not in {"http", "https"}
            or not parts.netloc
            or parts.username
            or parts.password
            or parts.path not in {"", "/"}
            or parts.query
            or parts.fragment
            or not _is_loopback_host(parts.hostname)
        ):
            raise ValueError("base_url must be a credential-free loopback HTTP URL")
        self.base_url = base_url.rstrip("/")
        self.transport = transport

    def request(self, method: str, path: str, payload: Any | None = None) -> Any:
        if not path.startswith("/") or ".." in path.split("/"):
            raise HarnessError("invalid_api_path", "API 路径无效")
        if self.transport is not None:
            return self.transport(method, path, payload)
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(
            self.base_url + path,
            data=body,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            method=method,
        )
        try:
            with urlopen(request, timeout=60) as response:
                return json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
            raise _safe_error(error) from error


class IntelligentEditingHarness:
    """Validate, preview, confirm, render, and resume an intelligent edit."""

    def __init__(self, state_dir: str | Path = ".agents/runtime/harness/intelligent-editing-runs", api: LocalApiClient | None = None):
        self.state_dir = Path(state_dir)
        self.api = api or LocalApiClient()

    def _state_path(self, request_id: str) -> Path:
        return self.state_dir / f"{_safe_request_id(request_id)}.json"

    @staticmethod
    def _rebuild_evidence(
        value: Mapping[str, Any],
        request: Mapping[str, Any],
        plan: Mapping[str, Any],
        fingerprint: str,
        digest: str,
    ) -> dict[str, Any]:
        evidence: dict[str, Any] = {
            "version": PLAN_VERSION,
            "request_id": request["request_id"],
            "input_fingerprint": fingerprint,
            "selected_asset_ids": [asset["asset_id"] for asset in request["selected_assets"]],
            "planner_requested": request["planner"],
            "planner_used": plan["planner"],
            "plan_hash": digest,
            "warnings": list(plan.get("warnings", [])),
            "stage": value["stage"],
        }
        accepted = value.get("accepted_plan_hash")
        if accepted is not None:
            evidence["accepted_plan_hash"] = accepted
        mix_id = value.get("mix_id")
        if mix_id is not None:
            evidence["mix_id"] = mix_id
        output = value.get("output")
        if isinstance(output, str) and output.startswith("/api/assets/"):
            identifier = output.removeprefix("/api/assets/")
            try:
                validate_managed_asset_id(identifier)
            except PlanValidationError:
                pass
            else:
                evidence["output_asset"] = f"/api/assets/{identifier}"
        error = value.get("error")
        if (
            value.get("status") == "failed"
            and isinstance(error, Mapping)
            and error.get("code") == "mix_failed"
        ):
            evidence["error"] = "混剪任务失败，请检查素材编码和文件完整性"
        return evidence

    def _load(self, request_id: str) -> dict[str, Any] | None:
        path = self._state_path(request_id)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError) as error:
            raise HarnessError("corrupt_state", "任务状态文件损坏，无法安全恢复") from error
        if not isinstance(value, dict):
            raise HarnessError("corrupt_state", "任务状态文件无效")
        try:
            if value.get("version") != RUN_VERSION:
                raise ValueError("unsupported state version")
            if value.get("request_id") != request_id:
                raise ValueError("request ID mismatch")
            request = validate_request(value.get("request"))
            if request["request_id"] != request_id:
                raise ValueError("stored request ID mismatch")
            fingerprint = self._request_fingerprint(request)
            if value.get("input_fingerprint") != fingerprint:
                raise ValueError("request fingerprint mismatch")
            if value.get("idempotency_key") != request["idempotency_key"]:
                raise ValueError("idempotency key mismatch")
            plan = validate_plan(value.get("plan"), request)
            digest = plan_hash(plan)
            if value.get("plan_hash") != digest:
                raise ValueError("plan hash mismatch")
            accepted = value.get("accepted_plan_hash")
            if accepted is not None and accepted != digest:
                raise ValueError("accepted plan hash mismatch")
            mix_id = value.get("mix_id")
            if mix_id is not None:
                _safe_mix_id(mix_id)
            if value.get("stage") not in {
                "previewed", "confirmed", "submitted", "queued", "running", "succeeded", "failed"
            }:
                raise ValueError("invalid stage")
            if value.get("status") not in {
                "awaiting_confirmation", "submitted", "queued", "running", "succeeded", "failed"
            }:
                raise ValueError("invalid status")
        except (PlanValidationError, TypeError, ValueError) as error:
            raise HarnessError(
                "corrupt_state",
                "任务状态与已验证的请求或计划不一致，无法安全恢复",
            ) from error
        value["request"] = request
        value["plan"] = plan
        output = value.get("output")
        value["output"] = None
        if isinstance(output, str) and output.startswith("/api/assets/"):
            identifier = output.removeprefix("/api/assets/")
            try:
                validate_managed_asset_id(identifier)
            except PlanValidationError:
                pass
            else:
                value["output"] = f"/api/assets/{identifier}"
        value["error"] = (
            {"code": "mix_failed", "message": "混剪任务失败，请检查素材编码和文件完整性"}
            if value["status"] == "failed"
            else None
        )
        value["evidence"] = self._rebuild_evidence(
            value, request, plan, fingerprint, digest
        )
        return value

    def _save(self, state: dict[str, Any]) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        state.setdefault("created_at", _now())
        state["updated_at"] = _now()
        target = self._state_path(str(state["request_id"]))
        fd, temporary = tempfile.mkstemp(prefix=f".{target.stem}.", suffix=".tmp", dir=self.state_dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(state, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
            os.replace(temporary, target)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    @staticmethod
    def _request_fingerprint(request: Mapping[str, Any]) -> str:
        normalized = validate_request(request)
        immutable_input = {
            key: value
            for key, value in normalized.items()
            if key not in {"request_id", "idempotency_key"}
        }
        return hashlib.sha256(canonical_json(immutable_input).encode("utf-8")).hexdigest()

    def _find_by_idempotency_key(self, key: str) -> dict[str, Any] | None:
        try:
            paths = sorted(self.state_dir.glob("*.json"))
        except OSError as error:
            raise HarnessError("state_unavailable", "任务状态目录无法读取") from error
        for path in paths:
            try:
                candidate = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(candidate, Mapping) and candidate.get("idempotency_key") == key:
                existing_id = candidate.get("request_id")
                if not isinstance(existing_id, str):
                    raise HarnessError("corrupt_state", "幂等任务状态无效")
                return self._load(existing_id)
        return None

    def _assets(self, request: Mapping[str, Any]) -> list[dict[str, Any]]:
        raw = self.api.request("GET", "/api/assets")
        records = raw.get("assets") if isinstance(raw, Mapping) else raw
        if not isinstance(records, list):
            raise HarnessError("invalid_asset_response", "素材库响应无效")
        by_id = {}
        for item in records:
            if isinstance(item, Mapping):
                safe = _safe_asset_record(item)
                by_id[safe["id"]] = safe
        selected = []
        for asset in request["selected_assets"]:
            found = by_id.get(asset["asset_id"])
            if found is None:
                raise HarnessError("invalid_asset", "所选素材不存在或不在托管素材库中")
            if found["media_type"] != asset["media_type"]:
                raise HarnessError("invalid_media_type", "所选素材类型与素材库不一致")
            selected.append(found)
        return selected

    def preview(self, request: Mapping[str, Any]) -> dict[str, Any]:
        normalized = validate_request(request)
        fingerprint = self._request_fingerprint(normalized)
        state = self._load(normalized["request_id"])
        if state is not None:
            if (
                state.get("input_fingerprint") != fingerprint
                or state.get("idempotency_key") != normalized["idempotency_key"]
            ):
                raise HarnessError("request_conflict", "同一 request_id 的输入已经改变，请使用新的 request_id")
            if state.get("plan") and state.get("plan_hash"):
                return copy.deepcopy(state)
        idempotent_state = self._find_by_idempotency_key(normalized["idempotency_key"])
        if idempotent_state is not None:
            if idempotent_state.get("input_fingerprint") != fingerprint:
                raise HarnessError("idempotency_conflict", "同一 idempotency_key 不能用于不同的剪辑输入")
            return copy.deepcopy(idempotent_state)
        self._assets(normalized)
        planner = normalized["planner"]
        if planner in {"local", "auto"}:
            reason = "自动模式使用本地确定性规则" if planner == "auto" else None
            plan = make_local_plan(normalized, reason)
        else:
            payload = planner_payload(normalized)
            try:
                remote_plan = self.api.request("POST", "/api/mixes/plan", payload)
                plan = validate_plan(remote_plan, normalized)
            except Exception:
                plan = make_local_plan(
                    normalized,
                    "Codex Terra 不可用，已改用本地确定性规则",
                )
        digest = plan_hash(plan)
        state = {
            "version": RUN_VERSION,
            "request_id": normalized["request_id"],
            "request": normalized,
            "input_fingerprint": fingerprint,
            "idempotency_key": normalized["idempotency_key"],
            "stage": "previewed",
            "status": "awaiting_confirmation",
            "planner": plan["planner"],
            "plan": plan,
            "plan_hash": digest,
            "accepted_plan_hash": None,
            "mix_id": None,
            "output": None,
            "error": None,
            "evidence": {
                "version": PLAN_VERSION,
                "request_id": normalized["request_id"],
                "input_fingerprint": fingerprint,
                "selected_asset_ids": [asset["asset_id"] for asset in normalized["selected_assets"]],
                "planner_requested": planner,
                "planner_used": plan["planner"],
                "plan_hash": digest,
                "warnings": list(plan.get("warnings", [])),
                "stage": "previewed",
            },
            "created_at": _now(),
            "updated_at": _now(),
        }
        self._save(state)
        return copy.deepcopy(state)

    def confirm(self, request_id: str, expected_hash: str) -> dict[str, Any]:
        state = self._load(request_id)
        if state is None or not state.get("plan_hash"):
            raise HarnessError("preview_required", "请先生成剪辑计划预览")
        if not re.fullmatch(r"[0-9a-f]{64}", expected_hash or "") or expected_hash != state["plan_hash"]:
            raise HarnessError("plan_hash_mismatch", "确认的计划哈希与预览不一致")
        state["accepted_plan_hash"] = expected_hash
        state["stage"] = "confirmed"
        state["status"] = "awaiting_confirmation"
        state["evidence"]["accepted_plan_hash"] = expected_hash
        state["evidence"]["stage"] = "confirmed"
        self._save(state)
        return copy.deepcopy(state)

    def render(self, request_id: str, expected_hash: str) -> dict[str, Any]:
        state = self._load(request_id)
        if state is None:
            raise HarnessError("preview_required", "请先生成剪辑计划预览")
        if state.get("accepted_plan_hash") != expected_hash or expected_hash != state.get("plan_hash"):
            raise HarnessError("confirmation_required", "必须先确认当前计划哈希")
        if state.get("mix_id"):
            return copy.deepcopy(state)
        payload = build_render_payload(state["request"], state["plan"])
        response = self.api.request("POST", "/api/mixes", payload)
        if not isinstance(response, Mapping):
            raise HarnessError("invalid_mix_response", "混剪服务返回了无效任务")
        mix_id = _safe_mix_id(response.get("mix_id"))
        state["mix_id"] = mix_id
        state["stage"] = "submitted"
        state["status"] = "submitted"
        state["evidence"]["mix_id"] = mix_id
        state["evidence"]["stage"] = "submitted"
        self._save(state)
        return copy.deepcopy(state)

    def status(self, request_id: str) -> dict[str, Any]:
        state = self._load(request_id)
        if state is None:
            raise HarnessError("not_found", "任务不存在")
        if not state.get("mix_id") or state.get("stage") in {"succeeded", "failed"}:
            return copy.deepcopy(state)
        response = self.api.request("GET", f"/api/mixes/{state['mix_id']}")
        if not isinstance(response, Mapping):
            raise HarnessError("invalid_mix_response", "混剪状态响应无效")
        status = response.get("status")
        if status in {"queued", "running", "succeeded", "failed"}:
            state["stage"] = status
            state["status"] = status
            state["evidence"]["stage"] = status
        output = response.get("output")
        if isinstance(output, str) and output.startswith("/api/assets/"):
            identifier = output.removeprefix("/api/assets/")
            try:
                validate_managed_asset_id(identifier)
            except PlanValidationError:
                pass
            else:
                safe_output = f"/api/assets/{identifier}"
                state["output"] = safe_output
                state["evidence"]["output_asset"] = safe_output
        if status == "failed":
            state["error"] = {"code": "mix_failed", "message": "混剪任务失败，请检查素材编码和文件完整性"}
            state["evidence"]["error"] = state["error"]["message"]
        self._save(state)
        return copy.deepcopy(state)


def _read_json(path: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise HarnessError("invalid_input", "请求 JSON 无法读取") from error
    if not isinstance(value, dict):
        raise HarnessError("invalid_input", "请求 JSON 必须是对象")
    return value


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AIGC intelligent-editing harness")
    parser.add_argument("--state-dir", "--state-root", dest="state_dir", default=".agents/runtime/harness/intelligent-editing-runs")
    parser.add_argument("--base-url", "--api-base", dest="base_url", default="http://127.0.0.1:8001")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("validate", "plan", "preview"):
        request_command = sub.add_parser(command)
        request_command.add_argument("request_json", nargs="?")
        request_command.add_argument("--request", dest="request_option")
    confirm = sub.add_parser("confirm")
    confirm.add_argument("request_id", nargs="?")
    confirm.add_argument("plan_hash", nargs="?")
    confirm.add_argument("--request-id", dest="request_id_option")
    confirm.add_argument("--plan-hash", dest="plan_hash_option")
    render = sub.add_parser("render")
    render.add_argument("request_id", nargs="?")
    render.add_argument("plan_hash", nargs="?")
    render.add_argument("--request-id", dest="request_id_option")
    render.add_argument("--plan-hash", dest="plan_hash_option")
    for command in ("resume", "status"):
        status_command = sub.add_parser(command)
        status_command.add_argument("request_id", nargs="?")
        status_command.add_argument("--request-id", dest="request_id_option")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        harness = IntelligentEditingHarness(args.state_dir, LocalApiClient(args.base_url))
        if args.command in {"validate", "plan", "preview"}:
            request_path = args.request_option or args.request_json
            if not request_path:
                raise HarnessError("invalid_input", f"{args.command} requires --request <json>")
            request = _read_json(request_path)
            if args.command == "validate":
                result = {"valid": True, "request": validate_request(request)}
            else:
                result = harness.preview(request)
        elif args.command == "confirm":
            request_id = args.request_id_option or args.request_id
            expected_hash = args.plan_hash_option or args.plan_hash
            if not request_id or not expected_hash:
                raise HarnessError("invalid_input", "confirm requires request ID and plan hash")
            result = harness.confirm(request_id, expected_hash)
        elif args.command == "render":
            request_id = args.request_id_option or args.request_id
            expected_hash = args.plan_hash_option or args.plan_hash
            if not request_id or not expected_hash:
                raise HarnessError("invalid_input", "render requires request ID and plan hash")
            result = harness.render(request_id, expected_hash)
        else:
            request_id = args.request_id_option or args.request_id
            if not request_id:
                raise HarnessError("invalid_input", "status requires a request ID")
            result = harness.status(request_id)
    except (HarnessError, PlanValidationError) as error:
        print(
            json.dumps(error.as_dict() if hasattr(error, "as_dict") else {"code": "invalid_request", "message": str(error)}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
