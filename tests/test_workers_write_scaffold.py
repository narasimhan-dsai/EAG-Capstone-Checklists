"""Implementation scaffolding. NOT the team's graded tests."""
from __future__ import annotations

import json
from datetime import date

from checklist_agent.agentswitch import AgentSwitchToolError
from checklist_agent.checklists.records import AGENT_MARKER
from checklist_agent.core.live_graph import TaskSpec
from checklist_agent.workers import RunContext, build_skills

TODAY = date(2026, 10, 3)


class FakePlatform:
    """A small stateful AgentSwitch: templates, runs and SOPs, with failure injection."""

    def __init__(self, templates=(), runs=(), sops=(), fail=None, hide_created=False):
        self.templates, self.runs, self.sops = list(templates), list(runs), list(sops)
        self.fail = fail or {}                       # {(tool, key): message}
        self.hide_created = hide_created
        self.calls, self.next = [], 0

    def _boom(self, name, key=None):
        message = self.fail.get((name, key)) or self.fail.get((name, None))
        if message:
            raise AgentSwitchToolError(name, -32000, message)

    async def call_tool(self, name, arguments=None, *, jurisdiction):
        a = arguments or {}
        self.calls.append((name, a))
        self._boom(name, a.get("id"))
        if name == "ChecklistTemplate.list":
            return {"data": self.templates, "total": len(self.templates)}
        if name == "ChecklistRun.list":
            rows = [r for r in self.runs if "template_id" not in a or r["template_id"] == a["template_id"]]
            if self.hide_created:
                rows = [r for r in rows if not (r.get("notes") or "").startswith(AGENT_MARKER)]
            return {"data": rows[a.get("offset", 0):a.get("offset", 0) + a["limit"]], "total": len(rows)}
        if name == "ChecklistTemplate.make.ChecklistRun":
            self.next += 1
            self.runs.append({"id": f"new{self.next}", "name": a["name"], "template_id": a["id"],
                              "due_date": a["due_date"], "status": "draft", "assigned_to_id": "u1",
                              "reviewer_id": "v1", "notes": a["notes"]})
            return {"status": "ok"}                  # deliberately no run id: the worker must re-read
        if name == "ChecklistRun.get":
            return next(r for r in self.runs if r["id"] == a["id"])
        if name == "ChecklistRun.start":
            run = next(r for r in self.runs if r["id"] == a["id"])
            run["status"] = "in_progress"
            return run
        if name == "SOPDocument.list":
            return {"data": self.sops, "total": len(self.sops)}
        if name == "SOPDocument.create":
            self.next += 1
            sop = {"id": f"sop{self.next}", "number": f"SOP-{self.next}", "status": "draft", **a}
            self.sops.append(sop)
            return sop
        if name == "SOPDocument.submit_for_review":
            sop = next(s for s in self.sops if s["id"] == a["id"])
            sop["status"] = "review"
            return sop
        if name == "SOPDocument.get":
            return next(s for s in self.sops if s["id"] == a["id"])
        raise AssertionError(f"unexpected tool {name}")

    def started(self):
        return [a["id"] for n, a in self.calls if n == "ChecklistRun.start"]

    async def close(self):
        return None


def template(id, assignee="u1", frequency="weekly"):
    return {"id": id, "name": f"T-{id}", "category": "safety", "frequency": frequency,
            "is_active": 1, "default_assignee_id": assignee, "items": []}


def seeded_run(id, template_id, due="2026-10-01", status="draft", notes=None):
    return {"id": id, "name": f"R-{id}", "template_id": template_id, "due_date": due, "status": status,
            "assigned_to_id": "u1", "reviewer_id": "v1", "notes": notes}


def ctx_for(platform, llm=None):
    return RunContext(run_id="r1", store=None, llm=llm, agentswitch=platform, clock=lambda: TODAY,
                      goal="Please turn this process into an SOP: do x")


async def audit(platform):
    skills = build_skills(ctx_for(platform))
    return await skills["start_monthly_safety_audit"](TaskSpec("s", "start_monthly_safety_audit", {}))


def action(result, template_id):
    return next(o for o in result["outcomes"] if o["template_id"] == template_id)


async def test_creates_and_starts_missing_runs_and_leaves_existing_ones_alone():
    platform = FakePlatform(templates=[template("a"), template("b")], runs=[seeded_run("seed1", "b")])
    result = await audit(platform)
    assert action(result, "a")["action"] == "created_and_started"
    assert action(result, "b")["action"] == "skipped_existing" and action(result, "b")["run_id"] == "seed1"
    assert platform.started() == ["new1"]                         # never the seeded run
    created = next(r for r in platform.runs if r["id"] == "new1")
    assert created["status"] == "in_progress" and created["due_date"] == "2026-10-31"
    assert created["notes"].startswith(AGENT_MARKER)
    assert created["name"] == "T-a - 2026-10-03"


async def test_a_second_run_does_not_create_duplicates():
    platform = FakePlatform(templates=[template("a")])
    await audit(platform)
    second = await audit(platform)
    assert action(second, "a")["action"] == "skipped_existing"
    assert len([r for r in platform.runs if r["template_id"] == "a"]) == 1


async def test_our_own_stuck_draft_is_started_not_recreated():
    stuck = seeded_run("mine", "a", due="2026-10-31", notes=f"{AGENT_MARKER} audit 2026-10 run x")
    platform = FakePlatform(templates=[template("a")], runs=[stuck])
    result = await audit(platform)
    assert action(result, "a")["action"] == "started_existing_draft"
    assert platform.started() == ["mine"] and len(platform.runs) == 1


async def test_a_failed_create_is_reported_and_the_rest_continue():
    platform = FakePlatform(templates=[template("a"), template("b")],
                            fail={("ChecklistTemplate.make.ChecklistRun", "a"): "no permission"})
    result = await audit(platform)
    assert action(result, "a")["action"] == "failed" and "no permission" in json.dumps(action(result, "a"))
    assert action(result, "b")["action"] == "created_and_started"


async def test_if_the_created_run_cannot_be_found_it_is_not_started():
    platform = FakePlatform(templates=[template("a")], hide_created=True)
    result = await audit(platform)
    assert action(result, "a")["action"] == "failed" and platform.started() == []


async def test_a_run_changed_by_someone_else_before_start_is_not_started():
    platform = FakePlatform(templates=[template("a")])
    original = platform.call_tool

    async def racing(name, arguments=None, *, jurisdiction):
        if name == "ChecklistRun.get":
            for run in platform.runs:
                run["status"] = "submitted"                       # another team moved it first
        return await original(name, arguments, jurisdiction=jurisdiction)

    platform.call_tool = racing
    result = await audit(platform)
    assert action(result, "a")["action"] == "skipped_changed" and platform.started() == []


async def test_a_template_without_an_assignee_is_flagged_not_given_an_owner():
    platform = FakePlatform(templates=[template("a", assignee=None)])
    result = await audit(platform)
    assert result["needs_owner"] == ["a"]
    assert all("assigned_to_id" not in a for n, a in platform.calls
               if n == "ChecklistTemplate.make.ChecklistRun")


GOOD_DRAFT = {"title": "Lockout tagout", "category": "safety",
              "sections": [{"title": "Purpose", "body": "Isolate energy."},
                           {"title": "Steps", "body": "1. Notify. 2. Isolate. 3. Lock."}]}


def llm_returning(payload):
    async def llm(prompt, system, **kwargs):
        return {"text": payload if isinstance(payload, str) else json.dumps(payload)}
    return llm


async def sop(platform, llm):
    skills = build_skills(ctx_for(platform, llm))
    return await skills["process_to_sop"](TaskSpec("p", "process_to_sop", {"process_description": "do x"}))


async def test_process_to_sop_creates_a_draft_then_submits_it_for_review():
    platform = FakePlatform()
    result = await sop(platform, llm_returning(GOOD_DRAFT))
    assert result["status"] == "review" and result["submitted"] is True and result["partial"] is False
    created = platform.sops[0]
    assert created["category"] == "safety" and AGENT_MARKER in created["content"]
    assert [s["title"] for s in created["sections"]] == ["Purpose", "Steps"]
    assert [n for n, _ in platform.calls if n.startswith("SOPDocument.") and n != "SOPDocument.list"][:2] == [
        "SOPDocument.create", "SOPDocument.submit_for_review"]


async def test_a_failed_submit_reports_the_partial_state():
    platform = FakePlatform(fail={("SOPDocument.submit_for_review", None): "not allowed"})
    result = await sop(platform, llm_returning(GOOD_DRAFT))
    assert result["partial"] is True and result["status"] == "draft" and result["submitted"] is False
    assert "not allowed" in json.dumps(result["submit_error"])


async def test_a_retry_finds_its_own_document_and_does_not_create_another():
    platform = FakePlatform()
    await sop(platform, llm_returning(GOOD_DRAFT))
    again = await sop(platform, llm_returning(GOOD_DRAFT))
    assert again["already_existed"] is True and len(platform.sops) == 1


async def test_bad_llm_output_writes_nothing():
    for bad in ("not json at all", {**GOOD_DRAFT, "category": "astrology"}, {**GOOD_DRAFT, "sections": []},
                {"title": "", "category": "safety", "sections": GOOD_DRAFT["sections"]}):
        platform = FakePlatform()
        result = await sop(platform, llm_returning(bad))
        assert result["error"] is True and result["code"] == "invalid_draft"
        assert not [n for n, _ in platform.calls if n == "SOPDocument.create"]


async def test_a_sop_draft_with_a_figure_the_description_never_gave_writes_nothing():
    invented = {**GOOD_DRAFT, "sections": [{"title": "Steps", "body": "1. Wait 45 minutes. 2. Lock."}]}
    platform = FakePlatform()
    result = await sop(platform, llm_returning(invented))
    assert result["error"] is True and result["code"] == "invalid_draft" and "45" in result["message"]
    assert not [n for n, _ in platform.calls if n == "SOPDocument.create"]
