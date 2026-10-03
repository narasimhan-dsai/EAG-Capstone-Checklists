"""Implementation scaffolding. NOT the team's graded tests."""
from __future__ import annotations

from datetime import date

from checklist_agent.checklists import Run, RunStatus, Template
from checklist_agent.checklists.records import AGENT_MARKER, run_from_row, template_from_row
from checklist_agent.checklists.scope import audit_scope, month_end, overdue, plan_audit

TODAY = date(2026, 10, 3)


def tpl(id, *, category="safety", frequency="weekly", active=True, assignee="ana"):
    return Template(id=id, name=f"T-{id}", category=category, frequency=frequency,
                    assignee=assignee, active=active)


def run(id, template_id, *, due, status=RunStatus.DRAFT, ours=False, owner="bo"):
    return Run(id=id, template_id=template_id, status=status, owner=owner,
               due_date=due, agent_created=ours)


def test_scope_is_active_safety_monthly_or_more_frequent():
    templates = [tpl("a", frequency="daily"), tpl("b", frequency="weekly"), tpl("c", frequency="monthly"),
                 tpl("d", frequency="yearly"), tpl("e", frequency="once"), tpl("f", frequency="quarterly"),
                 tpl("g", category="quality"), tpl("h", active=False)]
    assert [t.id for t in audit_scope(templates)] == ["a", "b", "c"]


def test_month_end():
    assert month_end(date(2026, 10, 3)) == date(2026, 10, 31)
    assert month_end(date(2026, 2, 10)) == date(2026, 2, 28)


def test_plan_creates_skips_resumes_and_flags_owner():
    templates = [tpl("a"), tpl("b"), tpl("c"), tpl("d", assignee=None)]
    runs = [run("r1", "b", due=date(2026, 10, 1)),                       # someone else's run
            run("r2", "c", due=date(2026, 10, 31), ours=True),           # ours, still draft
            run("r3", "a", due=date(2026, 9, 30))]                       # last month: no effect
    plan = plan_audit(templates, runs, TODAY)
    assert [t.id for t in plan.create] == ["a", "d"]
    assert [(t.id, rid) for t, rid in plan.skip] == [("b", "r1")]
    assert [(t.id, rid) for t, rid in plan.resume] == [("c", "r2")]
    assert plan.needs_owner == ("d",)
    assert plan.month == "2026-10" and plan.in_scope == 4


def test_our_started_run_is_skipped_not_resumed():
    plan = plan_audit([tpl("a")], [run("r1", "a", due=date(2026, 10, 31), ours=True,
                                       status=RunStatus.IN_PROGRESS)], TODAY)
    assert plan.create == () and plan.resume == () and [rid for _, rid in plan.skip] == ["r1"]


def test_run_without_due_date_is_not_this_months_run_and_never_overdue():
    plan = plan_audit([tpl("a")], [run("r1", "a", due=None)], TODAY)
    assert [t.id for t in plan.create] == ["a"]
    assert overdue([run("r1", "a", due=None)], TODAY).total == 0


def test_overdue_uses_dashboard_rule_and_splits_blockers():
    runs = [run("1", "a", due=date(2026, 9, 1), status=RunStatus.DRAFT),
            run("2", "a", due=date(2026, 9, 2), status=RunStatus.IN_PROGRESS),
            run("3", "a", due=date(2026, 9, 3), status=RunStatus.SUBMITTED),
            run("4", "a", due=date(2026, 9, 4), status=RunStatus.REVIEWED),   # done: not overdue
            run("5", "a", due=date(2026, 10, 3), status=RunStatus.DRAFT),     # due today: not overdue
            run("6", "a", due=date(2026, 9, 5), status=RunStatus.DRAFT, owner=None)]
    report = overdue(runs, TODAY)
    assert report.total == 4
    assert report.by_status == {"draft": 2, "in_progress": 1, "submitted": 1}
    assert report.blocked_on == {"owner": 3, "reviewer": 1, "other": 0}
    assert report.unassigned == 1
    assert [r["id"] for r in report.oldest] == ["1", "2", "3", "6"]
    assert report.to_dict()["as_of"] == "2026-10-03"


def test_rows_from_the_platform_become_models():
    t = template_from_row({"id": "t1", "name": "N", "category": "safety", "frequency": "daily",
                           "is_active": 1, "default_assignee_id": None,
                           "items": [{"name": "x", "is_blocker": True}, {"name": "y"}]})
    assert t.active is True and t.assignee is None and [i.blocker for i in t.items] == [True, False]
    r = run_from_row({"id": "r1", "template_id": "t1", "status": "in_progress", "due_date": "2026-10-03",
                      "assigned_to_id": "u", "reviewer_id": "v", "notes": f"{AGENT_MARKER} audit"})
    assert r.status == "in_progress" and r.due_date == date(2026, 10, 3)
    assert r.agent_created is True and r.reviewer == "v"
    assert run_from_row({"id": "r2", "status": "weird", "due_date": "not-a-date"}).due_date is None
