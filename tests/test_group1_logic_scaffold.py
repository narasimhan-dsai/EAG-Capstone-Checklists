"""Implementation scaffolding. NOT the team's graded tests."""
from __future__ import annotations

from datetime import date

from checklist_agent.checklists import Item, Run, RunStatus, Template, audit_template
from checklist_agent.checklists.records import run_from_row, sop_from_row, template_from_row
from checklist_agent.checklists.reports import blocked_runs, completion_summary, review_queue, sops_due_review
from checklist_agent.checklists.schedule import period_bounds, schedule_gaps

TODAY = date(2026, 10, 7)  # a Wednesday


def tpl(id, frequency="weekly", active=True, name=None):
    return Template(id=id, name=name or f"T-{id}", category="quality", frequency=frequency,
                    assignee="u", active=active, items=(Item("0", "x"),))


def run(id, template_id, due, **kw):
    return Run(id=id, template_id=template_id, status=kw.pop("status", RunStatus.DRAFT), due_date=due, **kw)


# --- schedule gaps
def test_period_bounds_per_frequency():
    assert period_bounds("daily", TODAY) == (TODAY, TODAY)
    assert period_bounds("weekly", TODAY) == (date(2026, 10, 5), date(2026, 10, 11))
    assert period_bounds("monthly", TODAY) == (date(2026, 10, 1), date(2026, 10, 31))
    assert period_bounds("quarterly", TODAY) == (date(2026, 10, 1), date(2026, 12, 31))
    assert period_bounds("yearly", TODAY) == (date(2026, 1, 1), date(2026, 12, 31))
    assert period_bounds("once", TODAY) is None and period_bounds(None, TODAY) is None


def test_gaps_are_reported_for_the_current_and_previous_period_only_where_no_run_is_due():
    templates = [tpl("a", "weekly"), tpl("b", "daily"), tpl("c", "once"), tpl("d", "weekly", active=False)]
    runs = [run("r1", "a", date(2026, 10, 6)),          # a: current week covered
            run("r2", "b", date(2026, 10, 6))]          # b: yesterday covered, today not
    report = schedule_gaps(templates, runs, TODAY)
    found = {(g["template_id"], g["period"]) for g in report.to_dict()["gaps"]}
    assert found == {("a", "previous"), ("b", "current")}
    assert report.to_dict()["templates_checked"] == 2        # inactive and once are not checked


def test_a_run_without_a_due_date_never_covers_a_period():
    report = schedule_gaps([tpl("a", "monthly")], [run("r", "a", None)], TODAY)
    assert report.to_dict()["total"] == 2


# --- blocked runs, review queue, completion, SOPs
def test_blocked_runs_are_open_runs_with_pending_blocker_items():
    runs = [run("1", "a", date(2026, 9, 1), blockers_pending=2),
            run("2", "a", date(2026, 9, 2), blockers_pending=0),
            run("3", "a", date(2026, 9, 3), blockers_pending=1, status=RunStatus.REVIEWED),
            run("4", "a", date(2026, 9, 4), blockers_pending=3, status=RunStatus.IN_PROGRESS)]
    out = blocked_runs(runs, TODAY)
    assert out["total"] == 2 and [r["id"] for r in out["runs"]] == ["4", "1"]
    assert out["by_status"] == {"draft": 1, "in_progress": 1}


def test_review_queue_groups_submitted_runs_by_reviewer():
    runs = [run("1", "a", date(2026, 9, 1), status=RunStatus.SUBMITTED, reviewer_name="Rao",
                updated_at="2026-09-20"),
            run("2", "a", date(2026, 9, 2), status=RunStatus.SUBMITTED, reviewer_name="Rao",
                updated_at="2026-10-01"),
            run("3", "a", date(2026, 9, 3), status=RunStatus.SUBMITTED, updated_at="2026-10-06"),
            run("4", "a", date(2026, 9, 4), status=RunStatus.DRAFT)]
    out = review_queue(runs, TODAY)
    assert out["total"] == 3
    assert out["by_reviewer"] == {"Rao": 2, "(no reviewer)": 1}
    assert out["oldest"][0]["id"] == "1" and out["oldest"][0]["days_waiting"] == 17


def test_completion_summary_matches_the_dashboard_formula():
    runs = [run("1", "a", None, category="safety", completed_items=3, total_items=4, status=RunStatus.REVIEWED),
            run("2", "a", None, category="safety", completed_items=0, total_items=4),
            run("3", "a", None, category="quality", completed_items=2, total_items=2, status=RunStatus.REVIEWED)]
    out = completion_summary(runs)
    assert (out["items_completed"], out["items_total"], out["items_remaining"]) == (5, 10, 5)
    assert out["completion_pct"] == 50.0
    assert out["by_category"]["safety"] == {"runs": 2, "items_completed": 3, "items_total": 8, "completion_pct": 37.5}
    assert out["runs_reviewed"] == 2 and out["runs_open"] == 1


def test_sops_due_for_review_use_the_review_frequency():
    sops = [sop_from_row({"id": "1", "name": "A", "status": "published", "published_at": "2026-01-01"}),
            sop_from_row({"id": "2", "name": "B", "status": "published", "published_at": "2026-09-01"}),
            sop_from_row({"id": "3", "name": "C", "status": "draft", "published_at": "2025-01-01"}),
            sop_from_row({"id": "4", "name": "D", "status": "published", "published_at": None,
                          "updated_at": "2025-06-01T10:00:00"})]
    out = sops_due_review(sops, 180, TODAY)
    assert [s["id"] for s in out["sops"]] == ["4", "1"] and out["total"] == 2
    assert out["review_days"] == 180 and out["sops"][1]["days_since"] == 279


# --- template audit: a branch rule that points at no item
def test_a_branch_rule_that_names_no_item_is_flagged():
    row = {"id": "t", "name": "T", "category": "safety", "frequency": "weekly", "is_active": 1,
           "default_assignee_id": "u",
           "items": [{"name": "Guard check", "branch_rule": "Lathe Dog 1391"}, {"name": "Lathe Dog 7"},
                     {"name": "Oil", "branch_rule": "Lathe Dog 7"}]}
    codes = [(f.code) for f in audit_template(template_from_row(row))]
    assert codes.count("unresolved_branch_rule") == 1          # "Lathe Dog 1391" is not an item here


def test_run_rows_carry_the_computed_fields_the_reports_need():
    r = run_from_row({"id": "r", "status": "draft", "blocker_items_pending": 2, "required_items_pending": 3,
                      "completed_items": 1.0, "total_items": 4.0, "category": "safety",
                      "_assigned_to_id_display": "ana@x.in", "_reviewer_id_display": "bo@x.in",
                      "updated_at": "2026-10-01T09:00:00"})
    assert (r.blockers_pending, r.required_pending, r.total_items, r.category) == (2, 3, 4.0, "safety")
    assert (r.owner_name, r.reviewer_name, r.updated_at) == ("ana@x.in", "bo@x.in", "2026-10-01T09:00:00")


def test_period_boundaries_for_quarters_months_and_the_year_rollover():
    assert period_bounds("quarterly", date(2026, 2, 10)) == (date(2026, 1, 1), date(2026, 3, 31))
    assert period_bounds("quarterly", date(2026, 5, 31)) == (date(2026, 4, 1), date(2026, 6, 30))
    assert period_bounds("quarterly", date(2026, 9, 30)) == (date(2026, 7, 1), date(2026, 9, 30))
    assert period_bounds("monthly", date(2026, 12, 15)) == (date(2026, 12, 1), date(2026, 12, 31))
    assert period_bounds("monthly", date(2028, 2, 29)) == (date(2028, 2, 1), date(2028, 2, 29))
    assert period_bounds("weekly", date(2026, 1, 1)) == (date(2025, 12, 29), date(2026, 1, 4))


def test_the_previous_period_of_a_january_template_is_last_decembers():
    report = schedule_gaps([tpl("a", "monthly")], [], date(2026, 1, 15))
    previous = next(g for g in report.to_dict()["gaps"] if g["period"] == "previous")
    assert (previous["period_start"], previous["period_end"]) == ("2025-12-01", "2025-12-31")
