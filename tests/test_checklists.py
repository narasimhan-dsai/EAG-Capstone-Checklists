from __future__ import annotations

from datetime import date

from checklist_agent.checklists import (
    Item,
    ItemResult,
    Run,
    RunStatus,
    Template,
    audit_template,
    load_rules,
    plan_capa_for_rejection,
)

TODAY = date(2026, 10, 3)
ITEMS = (Item("i1", "Exit clear", blocker=True), Item("i2", "Extinguisher tagged"))


def tpl(**kw):
    base = dict(id="t1", name="Fire", category="safety", frequency="weekly", assignee="ana", items=ITEMS)
    return Template(**{**base, **kw})


def codes(t):
    return {f.code for f in audit_template(t)}


def test_clean_template_has_no_findings():
    assert audit_template(tpl()) == []


def test_flags_wrong_category_odd_frequency_missing_assignee():
    assert codes(tpl(category="astrology")) == {"wrong_category"}
    assert codes(tpl(frequency="yearly")) == {"odd_frequency"}
    assert codes(tpl(frequency="fortnightly")) == {"odd_frequency"}
    assert codes(tpl(assignee=None)) == {"missing_assignee"}


def test_inactive_template_does_not_need_assignee():
    assert codes(tpl(assignee=None, active=False)) == set()


def test_missing_fields_and_no_items():
    assert codes(tpl(category=None, frequency=None, items=())) == {
        "missing_category", "missing_frequency", "no_items"}


def run(status=RunStatus.REJECTED, **kw):
    results = (ItemResult("i1", False), ItemResult("i2", True))
    return Run(**{**dict(id="r1", template_id="t1", status=status, owner="bo", results=results), **kw})


def test_rejected_run_opens_capa_per_failed_item_and_followup():
    plan = plan_capa_for_rejection(run(), tpl(), today=TODAY)
    [capa] = plan.capas
    assert (capa.item_id, capa.owner, capa.root_cause, capa.repeat) == ("i1", "bo", None, False)
    assert capa.due_date == date(2026, 10, 17)
    assert plan.followup_template_id == "t1" and plan.followup_due == date(2026, 10, 10)


def test_non_rejected_run_opens_nothing():
    assert plan_capa_for_rejection(run(RunStatus.APPROVED), tpl(), today=TODAY).capas == ()


def test_repeat_failure_is_flagged_and_planning_is_idempotent():
    first = plan_capa_for_rejection(run(), tpl(), today=TODAY).capas
    second = plan_capa_for_rejection(run(id="r2"), tpl(), today=TODAY, existing=first)
    assert second.capas[0].repeat is True
    assert plan_capa_for_rejection(run(), tpl(), today=TODAY, existing=first).capas == ()


def test_rejected_without_failed_item_gets_run_level_capa_owned_by_assignee():
    plan = plan_capa_for_rejection(run(results=(), owner=None), tpl(), today=TODAY)
    assert plan.capas[0].item_id == "" and plan.capas[0].owner == "ana"


def test_rules_load_from_yaml():
    assert "safety" in load_rules().categories
