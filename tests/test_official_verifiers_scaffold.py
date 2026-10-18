"""Implementation scaffolding. NOT the team's graded tests."""
from __future__ import annotations

import pytest
from test_verifiers_scaffold import ctx, run_row, snap, templates

from checklist_agent.checklists.records import AGENT_MARKER
from harness.verifiers import REGISTRY, InfraError

TODAY = "2026-10-07"                       # a Wednesday
RECURRING = f"{AGENT_MARKER} recurring weekly 2026-10-11 run x"


class FakeClient:
    """Answers the few reads a verifier makes through the un-wrapped client."""

    def __init__(self, broken=(), todos=(), escalations=(), fail=False):
        self.broken, self.todos, self.escalations, self.fail = set(broken), list(todos), list(escalations), fail

    async def call_tool(self, name, arguments=None, *, jurisdiction):
        a = arguments or {}
        if self.fail:
            raise RuntimeError("platform unreachable")
        if name == "endpoint.checklists.branch_rules":
            bad = a["template_id"] in self.broken
            return {"result": {"items": [{"name": "i", "problems": ["malformed_rule"] if bad else []}]}}
        rows = {"AgentTodo.list": self.todos, "AgentEscalation.list": self.escalations}[name]
        return {"data": rows[a.get("offset", 0):a.get("offset", 0) + a.get("limit", 20)], "total": len(rows)}


# --- the audit no longer demands runs the platform refuses to create
async def audit(before, after, client=None):
    tpl = templates("a", "b")
    c = ctx([tpl, snap("ChecklistRun.list", before)], [tpl, snap("ChecklistRun.list", after)])
    c.client = client
    return await REGISTRY["sop.start_safety_audit"](c, {"today": TODAY})


async def test_a_template_the_platform_cannot_run_is_not_expected_to_have_a_run():
    only_a = {"n1": run_row("a")}
    assert (await audit({}, only_a, FakeClient(broken={"b"})))["ok"] is True
    assert (await audit({}, only_a))["ok"] is False                                  # no client: b is expected


async def test_creating_a_run_on_a_template_the_platform_refuses_is_still_a_problem_and_a_blind_check_is_infra():
    both = {"n1": run_row("a"), "n2": run_row("b")}
    assert (await audit({}, both, FakeClient(broken={"b"})))["ok"] is False
    with pytest.raises(InfraError):
        await audit({}, only_a_run(), FakeClient(fail=True))


def only_a_run():
    return {"n1": run_row("a")}


# --- recurring runs
def recurring_run(template_id, due="2026-10-11", status="draft", notes=RECURRING):
    return {"template_id": template_id, "status": status, "due_date": due, "name": "n", "notes": notes,
            "updated_at": "t1"}


def weekly(*ids, active=1, frequency="weekly"):
    return snap("ChecklistTemplate.list", {i: {"name": f"T-{i}", "category": "quality", "frequency": frequency,
                                               "is_active": active, "default_assignee_id": "u"} for i in ids})


async def recurring(before, after, tpl=None, client=None, **params):
    tpl = tpl or weekly("a", "b")
    c = ctx([tpl, snap("ChecklistRun.list", before)], [tpl, snap("ChecklistRun.list", after)])
    c.client = client
    return await REGISTRY["recurring_runs_created"](c, {"today": TODAY, **params})


async def test_recurring_runs_must_be_drafts_due_at_the_period_end_on_recurring_templates():
    assert (await recurring({}, {"n1": recurring_run("a")}))["ok"] is True
    assert (await recurring({}, {"n1": recurring_run("a", due="2026-10-18")}))["ok"] is False      # wrong period end
    assert (await recurring({}, {"n1": recurring_run("a", status="in_progress")}))["ok"] is False  # must not start
    assert (await recurring({}, {"n1": recurring_run("a"), "n2": recurring_run("a")}))["ok"] is False   # duplicate
    assert (await recurring({}, {"n1": recurring_run("a")}, tpl=weekly("a", frequency="once")))["ok"] is False
    assert (await recurring({}, {"n1": recurring_run("a")}, tpl=weekly("a", active=0)))["ok"] is False


async def test_a_run_that_duplicates_a_covered_period_or_touches_an_existing_run_fails():
    existing = {"s1": {**recurring_run("a", notes=None), "due_date": "2026-10-08"}}          # a's week was covered
    assert (await recurring(existing, {**existing, "n1": recurring_run("a")}))["ok"] is False
    touched = {**existing, "s1": {**existing["s1"], "status": "in_progress"}, "n1": recurring_run("b")}
    assert (await recurring(existing, touched))["ok"] is False


async def test_the_count_bounds_and_the_platforms_refusals_are_checked():
    assert (await recurring({}, {}, min=1))["ok"] is False
    assert (await recurring({}, {"n1": recurring_run("a"), "n2": recurring_run("b")}, max=1))["ok"] is False
    assert (await recurring({}, {"n1": recurring_run("a")}, client=FakeClient(broken={"a"})))["ok"] is False


# --- follow-ups
def todo(title, status="open"):
    return {"title": title, "status": status}


def escalation(reason, session, status="open"):
    return {"reason": reason, "session_id": session, "status": status}


async def followups(client, **params):
    c = ctx([], [])
    c.client = client
    return await REGISTRY["followups_raised"](c, params)


async def test_followups_need_unique_todos_and_one_session_per_escalation():
    good = FakeClient(todos=[todo("[seat23] Ana: 2 overdue checklist run(s)"), todo("other team's todo")],
                      escalations=[escalation("[seat23] submitted runs without a reviewer: 19", "s1"),
                                   escalation("[seat23] overdue runs without an assignee: 90", "s2")])
    assert (await followups(good))["ok"] is True
    assert (await followups(FakeClient()))["ok"] is False                                  # nothing raised
    dup = FakeClient(todos=[todo("[seat23] Ana: 2 x"), todo("[seat23] Ana: 3 x")])
    assert (await followups(dup, escalations=False))["ok"] is False                        # one owner twice
    shared = FakeClient(todos=[todo("[seat23] Ana: 2 x")],
                        escalations=[escalation("[seat23] a: 1", "s1"), escalation("[seat23] b: 1", "s1")])
    assert (await followups(shared))["ok"] is False                                        # one session, two escalations


async def test_followups_cannot_be_judged_without_a_client():
    with pytest.raises(InfraError):
        await REGISTRY["followups_raised"](ctx([], []), {})


# --- a template drafted from an SOP
def template_row(active=0, description=f"{AGENT_MARKER} sop=abc drafted from SOP-1", items=2):
    return {"name": "T", "is_active": active, "description": description, "items": [{"name": "i"}] * items}


async def drafted(before, after, **params):
    return await REGISTRY["template_drafted_inactive"](
        ctx([snap("ChecklistTemplate.list", before)], [snap("ChecklistTemplate.list", after)]), params)


async def test_a_drafted_template_must_be_new_inactive_linked_and_have_items():
    assert (await drafted({}, {"n": template_row()}))["ok"] is True
    assert (await drafted({}, {"n": template_row(active=1)}))["ok"] is False
    assert (await drafted({}, {"n": template_row(items=0)}))["ok"] is False
    assert (await drafted({}, {"n": template_row(description="no marker")}))["ok"] is False
    assert (await drafted({"o": template_row()}, {"o": template_row()}))["ok"] is False        # nothing new
