"""Batch adapter for validated shapewear intelligent-editing variants."""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from typing import Any

from harness.intelligent_editing.runner import HarnessError, IntelligentEditingHarness
from skills.shapewear_video_generator.batch import BATCH_VERSION, validate_batch


class ShapewearBatchHarness:
    """Delegate each batch variant to the existing plan-hash-gated Harness."""

    def __init__(self, editing_harness: IntelligentEditingHarness):
        if not isinstance(editing_harness, IntelligentEditingHarness):
            raise TypeError("editing_harness must be an IntelligentEditingHarness")
        self.editing_harness = editing_harness

    @staticmethod
    def _variants(batch: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Mapping[str, Any]]]:
        normalized = validate_batch(batch)
        return normalized, {variant["variant_id"]: variant for variant in normalized["variants"]}

    @staticmethod
    def _summary(batch: Mapping[str, Any], runs: list[Mapping[str, Any]]) -> dict[str, Any]:
        statuses: dict[str, int] = {}
        variants = []
        for run in runs:
            status = str(run.get("status", "unknown"))
            statuses[status] = statuses.get(status, 0) + 1
            variants.append(
                {
                    "variant_id": run["request_id"],
                    "stage": run["stage"],
                    "status": status,
                    "planner": run.get("planner"),
                    "plan_hash": run.get("plan_hash"),
                    "plan": copy.deepcopy(run.get("plan")),
                    "accepted_plan_hash": run.get("accepted_plan_hash"),
                    "mix_id": run.get("mix_id"),
                    "output": run.get("output"),
                    "error": copy.deepcopy(run.get("error")),
                }
            )
        return {
            "version": BATCH_VERSION,
            "batch_id": batch["batch_id"],
            "variant_count": batch["variant_count"],
            "statuses": statuses,
            "variants": variants,
        }

    def preview(self, batch: Mapping[str, Any]) -> dict[str, Any]:
        normalized, variants = self._variants(batch)
        runs = [
            self.editing_harness.preview(variant["editing_request"])
            for variant in variants.values()
        ]
        return self._summary(normalized, runs)

    def confirm(self, batch: Mapping[str, Any], plan_hashes: Mapping[str, Any]) -> dict[str, Any]:
        normalized, variants = self._variants(batch)
        if (
            not isinstance(plan_hashes, Mapping)
            or set(plan_hashes) != set(variants)
            or any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value) for value in plan_hashes.values())
        ):
            raise HarnessError("invalid_batch_confirmation", "必须为批次中的每个变体提供计划哈希")
        runs = [
            self.editing_harness.confirm(variant_id, plan_hashes[variant_id])
            for variant_id in variants
        ]
        return self._summary(normalized, runs)

    def render(self, batch: Mapping[str, Any], plan_hashes: Mapping[str, Any]) -> dict[str, Any]:
        normalized, variants = self._variants(batch)
        if (
            not isinstance(plan_hashes, Mapping)
            or set(plan_hashes) != set(variants)
            or any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value) for value in plan_hashes.values())
        ):
            raise HarnessError("invalid_batch_confirmation", "必须为批次中的每个变体提供计划哈希")
        runs = [
            self.editing_harness.render(variant_id, plan_hashes[variant_id])
            for variant_id in variants
        ]
        return self._summary(normalized, runs)

    def status(self, batch: Mapping[str, Any]) -> dict[str, Any]:
        normalized, variants = self._variants(batch)
        runs = [self.editing_harness.status(variant_id) for variant_id in variants]
        return self._summary(normalized, runs)


__all__ = ["ShapewearBatchHarness"]
