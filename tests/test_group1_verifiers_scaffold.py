"""Implementation scaffolding. NOT the team's graded tests."""
from __future__ import annotations

import pytest
from test_verifiers_scaffold import ctx, snap

from harness.verifiers import REGISTRY, InfraError


def node(skill, result):
    return {"result": {"nodes": {"n": {"skill": skill, "state": "succeeded", "input": {}, "result": result}}}}


async def recompute(measure, rows, tool, record, **params):
    return await REGISTRY["db_recompute"](ctx([], [snap(tool, rows)], record), {"measure": measure, **params})


RUNS = {"1": {"status": "draft", "blocker_items_pending": 2, "completed_items": 1, "total_items": 4},
        "2": {"status": "reviewed", "blocker_items_pending": 1, "completed_items": 3, "total_items": 3},
        "3": {"status": "submitted", "blocker_items_pending": 0, "completed_items": 0, "total_items": 2}}


async def test_completion_items_match_the_database():
    good = node("summarize_completion", {"items_completed": 4, "items_total": 9})
    bad = node("summarize_completion", {"items_completed": 5, "items_total": 9})
    assert (await recompute("completion", RUNS, "ChecklistRun.list", good))["ok"] is True
    assert (await recompute("completion", RUNS, "ChecklistRun.list", bad))["ok"] is False


async def test_blocked_run_count_ignores_reviewed_runs():
    assert (await recompute("blocked_runs", RUNS, "ChecklistRun.list", node("list_blocked_runs", {"total": 1})))["ok"]
    assert not (await recompute("blocked_runs", RUNS, "ChecklistRun.list", node("list_blocked_runs", {"total": 2})))["ok"]


async def test_review_queue_counts_submitted_runs():
    assert (await recompute("review_queue", RUNS, "ChecklistRun.list", node("list_review_queue", {"total": 1})))["ok"]


async def test_sops_due_for_review_recompute_from_published_dates():
    sops = {"a": {"status": "published", "published_at": "2026-01-01T00:00:00"},
            "b": {"status": "published", "published_at": "2026-09-30T00:00:00"},
            "c": {"status": "draft", "published_at": "2025-01-01T00:00:00"}}
    record = node("list_sops_due_review", {"total": 1})
    assert (await recompute("sops_due_review", sops, "SOPDocument.list", record, today="2026-10-07"))["ok"]
    assert (await recompute("sops_due_review", sops, "SOPDocument.list", record, today="2026-10-07",
                            review_days=400))["ok"] is False


async def test_a_missing_node_fails_and_an_unknown_measure_or_snapshot_cannot_be_judged():
    assert (await recompute("blocked_runs", RUNS, "ChecklistRun.list", {"result": {"nodes": {}}}))["ok"] is False
    with pytest.raises(InfraError):
        await recompute("nonsense", RUNS, "ChecklistRun.list", node("x", {}))
    with pytest.raises(InfraError):
        await REGISTRY["db_recompute"](ctx([], [snap("ChecklistRun.list", RUNS, complete=False)],
                                           node("list_blocked_runs", {"total": 1})), {"measure": "blocked_runs"})
