"""Implementation scaffolding. NOT the team's graded tests."""
from __future__ import annotations

from datetime import date

from checklist_agent.agentswitch import AgentSwitchToolError
from checklist_agent.core.live_graph import TaskSpec
from checklist_agent.workers import RunContext, build_skills

TODAY = date(2026, 10, 3)


class FakeAS:
    """In-memory AgentSwitch: list tools page like the real ones."""

    def __init__(self, tables, errors=None):
        self.tables, self.errors, self.calls = tables, errors or {}, []

    async def call_tool(self, name, arguments=None, *, jurisdiction):
        arguments = arguments or {}
        self.calls.append((name, arguments))
        if name in self.errors:
            raise AgentSwitchToolError(name, -32000, self.errors[name])
        if name.endswith(".list"):
            rows = self.tables[name]
            offset, limit = arguments.get("offset", 0), arguments.get("limit", 20)
            return {"data": rows[offset:offset + limit], "total": len(rows)}
        raise AssertionError(f"unexpected tool {name}")

    async def close(self):
        return None


def ctx_for(fake):
    return RunContext(run_id="r1", store=None, llm=None, agentswitch=fake, clock=lambda: TODAY)


def template(id, frequency="weekly", category="safety", assignee="u1", active=1):
    return {"id": id, "name": f"T-{id}", "category": category, "frequency": frequency,
            "is_active": active, "default_assignee_id": assignee, "items": []}


def run(id, template_id, due, status="draft", assignee="u1", notes=None):
    return {"id": id, "name": f"R-{id}", "template_id": template_id, "due_date": due, "status": status,
            "assigned_to_id": assignee, "reviewer_id": "v1", "notes": notes}


async def test_plan_safety_audit_reports_without_writing():
    fake = FakeAS({"ChecklistTemplate.list": [template("a"), template("b"), template("c", frequency="yearly"),
                                              template("d", assignee=None)],
                   "ChecklistRun.list": [run("r1", "b", "2026-10-01")]})
    skills = build_skills(ctx_for(fake))
    result = await skills["plan_safety_audit"](TaskSpec("p", "plan_safety_audit", {}))
    assert [t["template_id"] for t in result["would_create"]] == ["a", "d"]
    assert [t["run_id"] for t in result["skip_existing"]] == ["r1"]
    assert result["needs_owner"] == ["d"] and result["month"] == "2026-10"
    assert all(name.endswith(".list") for name, _ in fake.calls)


async def test_list_overdue_pages_through_every_run():
    runs = [run(f"r{i}", "a", "2026-09-01") for i in range(450)]          # more than one page
    runs.append(run("done", "a", "2026-09-01", status="reviewed"))
    fake = FakeAS({"ChecklistRun.list": runs})
    result = await build_skills(ctx_for(fake))["list_overdue_runs"](TaskSpec("o", "list_overdue_runs", {}))
    assert result["total"] == 450 and result["as_of"] == "2026-10-03"
    assert result["blocked_on"]["owner"] == 450


async def test_a_platform_error_becomes_evidence_not_a_crash():
    fake = FakeAS({}, errors={"ChecklistRun.list": "denied"})
    result = await build_skills(ctx_for(fake))["list_overdue_runs"](TaskSpec("o", "list_overdue_runs", {}))
    assert result["error"] is True and result["tool"] == "ChecklistRun.list"
    assert result["message"] == "denied"


async def test_decline_is_deterministic_and_makes_no_calls():
    fake = FakeAS({})
    task = TaskSpec("d", "decline_request", {"reason_code": "not_permitted",
                                              "explanation": "publishing an SOP is a human step",
                                              "alternative": "I can submit it for review"})
    result = await build_skills(ctx_for(fake))["decline_request"](task)
    assert result["declined"] is True and result["reason_code"] == "not_permitted"
    assert result["text"].startswith("I can't do that: publishing an SOP is a human step")
    assert fake.calls == []
