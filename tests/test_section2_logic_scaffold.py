"""Implementation scaffolding. NOT the team's graded tests."""
from __future__ import annotations

from datetime import date

import pytest

from checklist_agent.checklists import Item, Run, RunStatus, Template
from checklist_agent.checklists.actions import (
    followups,
    incomplete_required_items,
    recurring_candidates,
    validate_template_draft,
)
from checklist_agent.checklists.records import run_from_row

TODAY = date(2026, 10, 7)  # a Wednesday


def tpl(id, frequency="weekly", category="quality", active=True, name=None):
    return Template(id=id, name=name or f"T-{id}", category=category, frequency=frequency,
                    assignee="u", active=active, items=(Item("0", "x"),))


def run(id, template_id="t", due=None, status=RunStatus.DRAFT, owner="u1", owner_name="Ana", reviewer=None, **kw):
    return Run(id=id, template_id=template_id, status=status, due_date=due, owner=owner, owner_name=owner_name,
               reviewer=reviewer, name=kw.pop("name", f"R-{id}"), **kw)


# --- recurring runs: only the current period, oldest period first, optionally one category
def test_recurring_candidates_are_current_period_gaps_in_a_stable_order():
    templates = [tpl("a", "weekly"), tpl("b", "daily"), tpl("c", "monthly", category="safety"),
                 tpl("d", "once"), tpl("e", "weekly", active=False)]
    runs = [run("r1", "a", date(2026, 10, 6))]                      # a's week is covered
    out = recurring_candidates(templates, runs, TODAY)
    assert [(g["template_id"], g["period_end"]) for g in out] == [("b", "2026-10-07"), ("c", "2026-10-31")]
    assert [g["template_id"] for g in recurring_candidates(templates, runs, TODAY, category="safety")] == ["c"]


# --- follow-ups: who to chase, and what needs a human
def test_followups_group_overdue_runs_by_owner_and_collect_what_needs_a_human():
    runs = [run("1", due=date(2026, 9, 1), owner_name="Ana"),
            run("2", due=date(2026, 9, 5), owner_name="Ana"),
            run("3", due=date(2026, 9, 3), owner_name="Bo"),
            run("4", due=date(2026, 9, 4), owner=None, owner_name=None),
            run("5", due=date(2026, 10, 7), owner_name="Ana"),                      # due today: not late
            run("6", due=date(2026, 9, 2), status=RunStatus.REVIEWED),               # done
            run("7", due=date(2026, 10, 20), status=RunStatus.SUBMITTED, reviewer=None),
            run("8", due=date(2026, 10, 20), status=RunStatus.SUBMITTED, reviewer="rv")]
    out = followups(runs, TODAY)
    assert {k: [r["id"] for r in v] for k, v in out["by_owner"].items()} == {"Ana": ["1", "2"], "Bo": ["3"]}
    assert [r["id"] for r in out["no_assignee"]] == ["4"]
    assert [r["id"] for r in out["no_reviewer"]] == ["7"]
    assert out["by_owner"]["Ana"][0]["days_late"] == 36


# --- template drafted from an SOP is held to the SOP's own words
SOP_TEXT = "Check the guards and the emergency stop. Run the machine empty for one minute. Report any fault."


def test_a_template_draft_is_cleaned_and_numbered():
    draft = {"items": [{"name": " Check the guards ", "item_type": "check"},
                       {"name": "Run the machine empty for one minute", "instructions": "Listen for noise."}]}
    items = validate_template_draft(draft, SOP_TEXT)
    assert [(i["order"], i["name"], i["item_type"]) for i in items] == [
        (1, "Check the guards", "check"), (2, "Run the machine empty for one minute", "check")]


@pytest.mark.parametrize("bad", [
    {"items": []}, {"items": "x"}, {"items": [{"name": ""}]}, {"items": [{"name": "x", "item_type": "video"}]},
    {"items": [{"name": f"step {i}"} for i in range(21)]},
    {"items": [{"name": "Run the machine for 15 minutes"}]},            # a figure the SOP never gave
])
def test_a_bad_or_invented_template_draft_is_rejected(bad):
    with pytest.raises(ValueError):
        validate_template_draft(bad, SOP_TEXT)


# --- CAPA: a rejected run's unfinished required items are the candidate findings
def test_incomplete_required_items_become_candidate_findings():
    items = [{"order": 1767, "name": "Guard", "is_required": True, "completed": False},
             {"order": 292.74, "name": "Oil", "is_required": True, "completed": False},
             {"order": 3, "name": "Done", "is_required": True, "completed": True},
             {"order": 4, "name": "Optional", "is_required": False, "completed": False},
             {"order": 5, "name": "Skipped", "is_required": True, "completed": False, "branch_skipped": True}]
    assert [(c["key"], c["name"]) for c in incomplete_required_items(items)] == [
        ("item:1767", "Guard"), ("item:292.74", "Oil")]


def test_run_rows_carry_their_number():
    assert run_from_row({"id": "r", "number": "RUN-2026-00304", "status": "draft"}).number == "RUN-2026-00304"
