"""Implementation scaffolding. NOT the team's graded tests."""
from __future__ import annotations

import json

from checklist_agent.capabilities import Argument, Capability, CapabilityRegistry, default_registry
from checklist_agent.core.live_graph import Event, GraphSnapshot
from checklist_agent.planner import ChecklistPlanner

GRAPH = GraphSnapshot("r", False, {}, ())
EVENT = Event(1, "run_started", None, {})
DECLINE = {"add": [{"id": "d1", "capability": "decline_request", "depends_on": [],
                    "arguments": {"reason_code": "not_permitted", "explanation": "publishing is a human step"}}],
           "cancel": [], "finish": False, "reason": "no publish capability"}


def llm_returning(payload):
    async def llm(prompt, system, **kwargs):
        return {"text": payload if isinstance(payload, str) else json.dumps(payload)}
    return llm


def registry_with_write():
    write = Capability("write_thing", "writes", {"x": Argument("string", "x")}, side_effect=True)
    return CapabilityRegistry([*[default_registry().get(n) for n in default_registry().names()], write])


async def test_valid_decline_is_accepted():
    planner = ChecklistPlanner(llm_returning(DECLINE), default_registry(), goal="Publish the SOP")
    patch = await planner.plan(GRAPH, EVENT)
    assert [t.skill for t in patch.add] == ["decline_request"]


async def test_unknown_capability_fails_visibly_after_repair():
    bad = {"add": [{"id": "x1", "capability": "approve_run", "arguments": {}, "depends_on": []}],
           "cancel": [], "finish": False, "reason": "try"}
    planner = ChecklistPlanner(llm_returning(bad), default_registry(), goal="g", repair_attempts=1)
    patch = await planner.plan(GRAPH, EVENT)
    assert patch.finish is True and patch.reason.startswith("planner failed validation")


async def test_side_effect_capability_needs_explicit_authority():
    plan = {"add": [{"id": "w1", "capability": "write_thing", "arguments": {"x": "y"}, "depends_on": []}],
            "cancel": [], "finish": False, "reason": "write"}
    denied = ChecklistPlanner(llm_returning(plan), registry_with_write(), goal="g", repair_attempts=0)
    assert "lacks explicit run authority" in (await denied.plan(GRAPH, EVENT)).reason
    allowed = ChecklistPlanner(llm_returning(plan), registry_with_write(), goal="g",
                               allowed_side_effects={"write_thing"})
    assert [t.skill for t in (await allowed.plan(GRAPH, EVENT)).add] == ["write_thing"]


async def test_unauthorised_side_effect_is_hidden_from_the_manifest():
    seen = {}

    async def llm(prompt, system, **kwargs):
        seen["capabilities"] = [c["name"] for c in json.loads(prompt)["capabilities"]]
        return {"text": json.dumps(DECLINE)}

    await ChecklistPlanner(llm, registry_with_write(), goal="g").plan(GRAPH, EVENT)
    assert "write_thing" not in seen["capabilities"] and "decline_request" in seen["capabilities"]


def test_manifest_has_no_publish_approve_or_delete_capability():
    names = set(default_registry().names())
    assert {"answer_with_evidence", "decline_request"} <= names
    assert not {n for n in names if any(w in n for w in ("approve", "reject", "publish", "delete"))}


async def test_prompts_are_about_checklists_not_another_domain():
    seen = {}

    async def llm(prompt, system, **kwargs):
        seen["prompt"], seen["system"] = prompt, system
        return {"text": json.dumps(DECLINE)}

    planner = ChecklistPlanner(llm, default_registry(), goal="g")
    await planner.plan(GRAPH, EVENT)
    text = (seen["prompt"] + seen["system"] + planner._review_system()).lower()
    assert "checklist" in text and "employee" not in text and "jurisdiction" not in text
