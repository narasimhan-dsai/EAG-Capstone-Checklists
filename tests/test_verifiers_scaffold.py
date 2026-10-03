"""Implementation scaffolding. NOT the team's graded tests."""
from __future__ import annotations

import pytest

from checklist_agent.checklists.records import AGENT_MARKER
from harness.verifiers import REGISTRY, InfraError, VerifyContext

MARK = f"{AGENT_MARKER} audit 2026-10 run x"


def snap(tool, rows, complete=True):
    return {"tool": tool, "args": {}, "key": "id", "fields": [], "total": len(rows), "fetched": len(rows),
            "complete": complete, "rows": rows}


def ctx(before, after, record=None):
    return VerifyContext(task={}, record=record or {}, before=before, after=after, client=None,
                         jurisdiction="IN")


def templates(*ids):
    return snap("ChecklistTemplate.list", {i: {"name": f"T-{i}", "category": "safety", "frequency": "weekly",
                                               "is_active": 1, "default_assignee_id": "u"} for i in ids})


def run_row(template_id, status="in_progress", due="2026-10-31", notes=MARK, updated="t1"):
    return {"template_id": template_id, "status": status, "due_date": due, "name": "n", "notes": notes,
            "updated_at": updated}


async def audit(before_runs, after_runs, tpl=templates("a", "b")):
    return await REGISTRY["sop.start_safety_audit"](
        ctx([tpl, snap("ChecklistRun.list", before_runs)], [tpl, snap("ChecklistRun.list", after_runs)]),
        {"today": "2026-10-03"})


async def test_audit_passes_with_a_new_started_run_and_an_untouched_existing_one():
    seeded = {"s1": run_row("b", status="draft", notes=None, due="2026-10-01")}
    after = {**seeded, "n1": run_row("a")}
    assert (await audit(seeded, after))["ok"] is True


async def test_audit_fails_when_a_preexisting_run_was_modified():
    seeded = {"s1": run_row("b", status="draft", notes=None, due="2026-10-01")}
    after = {"s1": run_row("b", status="in_progress", notes=None, due="2026-10-01", updated="t2"),
             "n1": run_row("a")}
    result = await audit(seeded, after)
    assert result["ok"] is False and "modified" in str(result["observed"])


async def test_audit_fails_on_duplicate_or_missing_or_unstarted_runs_and_strays():
    assert (await audit({}, {"n1": run_row("a")}))["ok"] is False                     # b has no run
    assert (await audit({}, {"n1": run_row("a"), "n2": run_row("a"), "n3": run_row("b")}))["ok"] is False
    assert (await audit({}, {"n1": run_row("a", status="draft"), "n3": run_row("b")}))["ok"] is False
    stray = {"n1": run_row("a"), "n3": run_row("b"), "n4": run_row("zzz")}
    assert (await audit({}, stray))["ok"] is False


async def test_audit_cannot_be_judged_on_an_incomplete_snapshot_or_empty_scope():
    tpl = templates("a")
    incomplete = ctx([tpl, snap("ChecklistRun.list", {})], [tpl, snap("ChecklistRun.list", {}, complete=False)])
    with pytest.raises(InfraError):
        await REGISTRY["sop.start_safety_audit"](incomplete, {"today": "2026-10-03"})
    with pytest.raises(InfraError):
        await audit({}, {}, tpl=snap("ChecklistTemplate.list", {}))


def sop_row(status="review", active=0, content=f"<p>x</p><p>{AGENT_MARKER} drafted</p>", sections=(1, 2)):
    return {"name": "Lockout", "status": status, "is_active": active, "content": content,
            "sections": list(sections)}


async def sop(before, after):
    return await REGISTRY["sop.process_to_sop"](
        ctx([snap("SOPDocument.list", before)], [snap("SOPDocument.list", after)]), {})


async def test_sop_passes_for_one_new_unpublished_document_in_review():
    assert (await sop({}, {"n": sop_row()}))["ok"] is True


async def test_sop_fails_when_published_active_empty_duplicated_or_a_seed_row_changed():
    assert (await sop({}, {"n": sop_row(status="published")}))["ok"] is False
    assert (await sop({}, {"n": sop_row(active=1)}))["ok"] is False
    assert (await sop({}, {"n": sop_row(sections=())}))["ok"] is False
    assert (await sop({}, {}))["ok"] is False
    assert (await sop({}, {"a": sop_row(), "b": sop_row()}))["ok"] is False
    seed = {"s": sop_row(status="draft", content="old")}
    assert (await sop(seed, {**seed, "s": sop_row(status="review", content="old"), "n": sop_row()}))["ok"] is False


def overdue_record(total):
    return {"result": {"nodes": {"late": {"skill": "list_overdue_runs", "state": "succeeded",
                                          "input": {}, "result": {"total": total}}}}}


async def overdue(rows, reported, **params):
    return await REGISTRY["overdue_matches_db"](
        ctx([], [snap("ChecklistRun.list", rows)], overdue_record(reported)), {"today": "2026-10-03", **params})


async def test_overdue_matches_a_fresh_recompute():
    rows = {"1": {"due_date": "2026-09-01", "status": "draft"}, "2": {"due_date": "2026-09-02", "status": "submitted"},
            "3": {"due_date": "2026-09-03", "status": "reviewed"}, "4": {"due_date": "2026-10-03", "status": "draft"},
            "5": {"due_date": None, "status": "draft"}}
    assert (await overdue(rows, 2))["ok"] is True
    assert (await overdue(rows, 3))["ok"] is False
    assert (await overdue(rows, 3, tolerance=1))["ok"] is True
