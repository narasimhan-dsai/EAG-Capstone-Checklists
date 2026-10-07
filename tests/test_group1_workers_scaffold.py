"""Implementation scaffolding. NOT the team's graded tests."""
from __future__ import annotations

from datetime import date

from test_workers_read_scaffold import FakeAS, run, template

from checklist_agent.capabilities import default_registry
from checklist_agent.core.live_graph import TaskSpec
from checklist_agent.workers import RunContext, build_skills

TODAY = date(2026, 10, 7)


def skills_for(tables, errors=None):
    fake = FakeAS(tables, errors)
    return fake, build_skills(RunContext(run_id="r", store=None, llm=None, agentswitch=fake,
                                         clock=lambda: TODAY))


async def call(skills, name):
    return await skills[name](TaskSpec("t", name, {}))


async def test_schedule_gaps_reports_missing_periods_and_only_reads():
    fake, skills = skills_for({"ChecklistTemplate.list": [template("a", frequency="weekly"),
                                                          template("b", frequency="once")],
                               "ChecklistRun.list": [run("r1", "a", "2026-10-06")]})
    out = await call(skills, "find_schedule_gaps")
    assert out["templates_checked"] == 1 and out["by_period"] == {"current": 0, "previous": 1}
    assert all(n.endswith(".list") for n, _ in fake.calls)


def run_row(id, status, **extra):
    return {**run(id, "t", "2026-09-01", status=status), "category": "safety", "completed_items": 1,
            "total_items": 2, "blocker_items_pending": 0, "_reviewer_id_display": "Rao",
            "updated_at": "2026-10-01T09:00:00", **extra}


async def test_blocked_runs_review_queue_and_completion_read_the_same_rows():
    rows = [run_row("1", "draft", blocker_items_pending=2), run_row("2", "submitted"),
            run_row("3", "reviewed", blocker_items_pending=1)]
    _, skills = skills_for({"ChecklistRun.list": rows})
    blocked = await call(skills, "list_blocked_runs")
    queue = await call(skills, "list_review_queue")
    summary = await call(skills, "summarize_completion")
    assert blocked["total"] == 1 and queue["total"] == 1 and queue["by_reviewer"] == {"Rao": 1}
    assert (summary["items_completed"], summary["items_total"], summary["runs_reviewed"]) == (3, 6, 1)


async def test_audit_templates_counts_findings_by_code_with_template_names():
    bad = {**template("a"), "default_assignee_id": None, "category": "astrology",
           "items": [{"name": "x", "branch_rule": "nope"}]}
    _, skills = skills_for({"ChecklistTemplate.list": [bad, {**template("b"), "items": [{"name": "ok"}]}]})
    out = await call(skills, "audit_templates")
    assert out["templates_audited"] == 2 and out["templates_flagged"] == 1
    assert out["by_code"] == {"wrong_category": 1, "missing_assignee": 1, "unresolved_branch_rule": 1}
    assert {f["name"] for f in out["findings"]} == {"T-a"}


async def test_sops_due_review_uses_the_platform_review_frequency_and_says_where_it_came_from():
    sops = [{"id": "1", "name": "A", "status": "published", "published_at": "2026-01-01", "number": "S-1"},
            {"id": "2", "name": "B", "status": "published", "published_at": "2026-09-01", "number": "S-2"}]
    _, skills = skills_for({"SOPDocument.list": sops,
                            "ChecklistPreferences.list": [{"sop_review_frequency_days": 30.0}]})
    out = await call(skills, "list_sops_due_review")
    assert out["review_days"] == 30 and out["review_days_source"] == "ChecklistPreferences"
    assert [s["id"] for s in out["sops"]] == ["1", "2"]


async def test_sops_due_review_falls_back_to_180_when_preferences_cannot_be_read():
    _, skills = skills_for({"SOPDocument.list": []}, errors={"ChecklistPreferences.list": "denied"})
    out = await call(skills, "list_sops_due_review")
    assert out["review_days"] == 180 and out["review_days_source"] == "default"


def test_the_six_new_capabilities_are_read_only_and_offered_without_authority():
    registry = default_registry()
    new = {"find_schedule_gaps", "list_blocked_runs", "list_review_queue", "summarize_completion",
           "audit_templates", "list_sops_due_review"}
    assert new <= set(registry.names())
    assert not any(registry.get(n).side_effect for n in new)
