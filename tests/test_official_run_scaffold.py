"""Implementation scaffolding. NOT the team's graded tests."""
from __future__ import annotations

import json

import pytest

from harness.official import build_results, run_official, to_result_task, validate_results


def row(id, state="pass", claims_passed=3, claims_total=3, **extra):
    return {"id": id, "family": "report", "authored_by": "team", "mode": "dry_run", "state": state,
            "claims_passed": claims_passed, "claims_total": claims_total, **extra}


TASK = {"id": "overdue", "goal": "Which checklists are overdue, and who is blocking them?", "allow": []}


def test_a_passing_task_maps_to_the_required_shape():
    out = to_result_task("suryodaya", TASK, row("overdue"))
    assert out == {"id": "suryodaya:overdue", "title": TASK["goal"], "passed": True, "score": 1.0,
                   "evidence": "3/3 checks passed"}


def test_a_failing_task_carries_a_fractional_score_and_the_failed_claims():
    out = to_result_task("suryodaya", TASK, row("overdue", "fail", 2, 3, failed_claims=["the total matches"]))
    assert out["passed"] is False and out["score"] == pytest.approx(2 / 3)
    assert "2/3" in out["evidence"] and "the total matches" in out["evidence"]


def test_an_infrastructure_error_is_a_failure_that_says_so():
    out = to_result_task("suryodaya", TASK, row("overdue", "infra_error", 0, 0, reason="gateway returned 503"))
    assert out["passed"] is False and out["score"] == 0.0 and out["evidence"].startswith("infrastructure:")


def test_long_text_is_cut_to_what_the_format_allows():
    out = to_result_task("suryodaya", {**TASK, "goal": "g" * 400}, row("x", "fail", 0, 1, failed_claims=["c" * 900]))
    assert len(out["title"]) <= 120 and len(out["evidence"]) <= 500


def test_the_summary_counts_passes_and_infrastructure_errors():
    tasks = [to_result_task("suryodaya", TASK, row("a")), to_result_task("suryodaya", TASK, row("b", "fail", 0, 1)),
             to_result_task("suryodaya", TASK, row("c", "infra_error", 0, 0, reason="x"))]
    assert build_results("suryodaya", tasks)["summary"] == "suryodaya: 1/3 tasks passed, 1 infrastructure error"


def test_the_file_is_valid_even_when_nothing_ran():
    payload = build_results("suryodaya", [])
    validate_results(payload)
    assert len(payload["tasks"]) == 1 and payload["tasks"][0]["passed"] is False


@pytest.mark.parametrize("bad", [
    {"tasks": [], "summary": "x"},
    {"tasks": [{"id": "a", "title": "t", "passed": "yes", "evidence": "e"}], "summary": "x"},
    {"tasks": [{"id": "a", "title": "t", "passed": True, "score": 2, "evidence": "e"}], "summary": "x"},
    {"tasks": [{"id": "a", "title": "t", "passed": True}, {"id": "a", "title": "t", "passed": True}], "summary": "x"},
    {"tasks": [{"id": "a", "title": "t", "passed": True}]},
    {"tasks": [{"id": "a", "title": "t", "passed": True}] * 201, "summary": "x"},
])
def test_the_validator_rejects_what_the_platform_would(bad):
    with pytest.raises(ValueError):
        validate_results(bad)


# --- the run itself, with the task runner replaced
def tasks_file():
    return [{"id": "read", "goal": "Which checklists are overdue?", "allow": [], "mode": "dry_run"},
            {"id": "write", "goal": "Start the monthly safety audit", "allow": ["start_monthly_safety_audit"],
             "mode": "live"},
            {"id": "boom", "goal": "Explode", "allow": [], "mode": "dry_run"}]


async def fake_run_task(task, args, run_dir):
    if task["id"] == "boom":
        raise RuntimeError("the runner crashed")
    return row(task["id"], mode="live" if args.live else "dry_run")


async def test_a_dry_run_skips_tasks_that_write_and_a_crash_costs_only_its_own_task(tmp_path):
    out = tmp_path / "results.json"
    payload = await run_official(tasks_file(), instance="suryodaya", armed=False, budget_seconds=60,
                                 out_path=out, runner_args=type("A", (), {"live": False})(), run_task=fake_run_task)
    by_id = {t["id"]: t for t in payload["tasks"]}
    assert by_id["suryodaya:read"]["passed"] is True
    assert by_id["suryodaya:write"]["passed"] is False and "not armed" in by_id["suryodaya:write"]["evidence"]
    assert by_id["suryodaya:boom"]["passed"] is False and "crashed" in by_id["suryodaya:boom"]["evidence"]
    assert json.loads(out.read_text()) == payload
    validate_results(payload)


async def test_an_armed_run_executes_the_write_tasks(tmp_path):
    seen = []

    async def recording(task, args, run_dir):
        seen.append((task["id"], args.live))
        return row(task["id"])

    await run_official(tasks_file()[:2], instance="suryodaya", armed=True, budget_seconds=60,
                       out_path=tmp_path / "r.json", runner_args=type("A", (), {"live": True})(), run_task=recording)
    assert seen == [("read", True), ("write", True)]


async def test_tasks_left_when_the_time_budget_runs_out_are_reported_not_dropped(tmp_path):
    async def slow(task, args, run_dir):
        import asyncio
        await asyncio.sleep(0.05)
        return row(task["id"])

    payload = await run_official(tasks_file()[:2], instance="suryodaya", armed=True, budget_seconds=0.01,
                                 out_path=tmp_path / "r.json", runner_args=type("A", (), {"live": True})(), run_task=slow)
    assert [t["passed"] for t in payload["tasks"]] == [True, False]
    assert "time budget" in payload["tasks"][1]["evidence"]


def test_claims_are_counted_from_verifier_results():
    from harness.runner import _claims
    assert _claims([{"claim": "a", "ok": True}, {"claim": "b", "ok": False}, {"claim": "c", "infra_error": "x"}]) == (1, 3)
    assert _claims([]) == (0, 0)
