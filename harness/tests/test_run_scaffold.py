"""Implementation scaffolding. NOT the team's graded tests."""
from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace

from checklist_agent.run import run_goal

DECLINE = {"add": [{"id": "decline", "capability": "decline_request", "depends_on": [],
                    "arguments": {"reason_code": "not_permitted",
                                  "explanation": "publishing an SOP is a human reviewer's step",
                                  "alternative": "I can submit it for review"}}],
           "cancel": [], "finish": False, "reason": "no capability publishes an SOP"}
OVERDUE = {"add": [{"id": "late", "capability": "list_overdue_runs", "arguments": {}, "depends_on": []}],
           "cancel": [], "finish": False, "reason": "read the overdue runs"}
ANSWER = {"add": [{"id": "answer", "capability": "answer_with_evidence",
                   "arguments": {"query": "Which checklists are overdue?"}, "depends_on": ["late"]}],
          "cancel": [], "finish": False, "reason": "lookup done"}


class ScriptedLLM:
    """Replays preset planner and answer replies, in call order."""

    def __init__(self, steps):
        self.steps, self.calls = list(steps), 0

    async def complete(self, prompt, system, **kwargs):
        step = self.steps[self.calls]
        self.calls += 1
        reply = step["reply"]
        return {"text": reply if isinstance(reply, str) else json.dumps(reply)}

    async def close(self):
        return None


class OneRunPlatform:
    async def call_tool(self, name, arguments=None, *, jurisdiction):
        assert name == "ChecklistRun.list"
        rows = [{"id": "r1", "name": "R1", "template_id": "t", "due_date": "2026-09-01",
                 "status": "draft", "assigned_to_id": "u", "reviewer_id": "v"}]
        return {"data": rows, "total": 1}

    async def close(self):
        return None


async def test_a_decline_is_the_answer_and_is_flagged():
    llm = ScriptedLLM([{"reply": DECLINE}])
    result = await run_goal("Publish the SOP", llm=llm, agentswitch=SimpleNamespace(close=OneRunPlatform().close))
    assert result["declined"] is True and result["decline"]["reason_code"] == "not_permitted"
    assert result["answer"].startswith("I can't do that: publishing an SOP")


async def test_a_read_only_goal_reaches_an_answer_through_the_real_graph():
    llm = ScriptedLLM([{"reply": OVERDUE}, {"reply": ANSWER},
                       {"reply": {"ready": True, "missing": [], "reason": "ok"}},
                       {"reply": "1 run is overdue."}])
    result = await run_goal("Which checklists are overdue?", llm=llm, agentswitch=OneRunPlatform(),
                            clock=lambda: date(2026, 10, 3))
    assert result["finished"] is True and result["answer"] == "1 run is overdue."
    assert result["nodes"]["late"]["result"]["total"] == 1
    assert result["declined"] is False


async def test_a_change_capability_is_not_offered_without_authority():
    seen = {}

    async def complete(prompt, system, **kwargs):
        seen["names"] = [c["name"] for c in json.loads(prompt)["capabilities"]]
        return {"text": json.dumps(DECLINE)}

    llm = SimpleNamespace(complete=complete, close=OneRunPlatform().close)
    await run_goal("Start the monthly safety audit", llm=llm, agentswitch=OneRunPlatform())
    assert "start_monthly_safety_audit" not in seen["names"] and "plan_safety_audit" in seen["names"]
    seen.clear()
    await run_goal("Start the monthly safety audit", llm=llm, agentswitch=OneRunPlatform(),
                   allowed_side_effects={"start_monthly_safety_audit"})
    assert "start_monthly_safety_audit" in seen["names"]
