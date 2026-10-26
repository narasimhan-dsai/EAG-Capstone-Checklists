"""Implementation scaffolding. NOT the team's graded tests."""
from __future__ import annotations

import json
from datetime import date

from checklist_agent.agentswitch import AgentSwitchToolError
from checklist_agent.checklists.records import AGENT_MARKER
from checklist_agent.core.live_graph import TaskSpec
from checklist_agent.workers import RunContext, build_skills

TODAY = date(2026, 10, 7)  # a Wednesday


class Seat:
    """A stateful fake of what this seat can reach: checklists, SOPs, agent todos, escalations and tasks."""

    def __init__(self, templates=(), runs=(), sops=(), todos=(), escalations=(), tasks=(), findings=(),
                 broken=None, assignees=({"id": "party-meera", "display": "Meera Kulkarni"},),
                 fail=None, ignore_task_status=False, trends=None, silent_raise=False):
        self.templates, self.runs, self.sops = list(templates), list(runs), list(sops)
        self.todos, self.escalations, self.tasks, self.findings = list(todos), list(escalations), list(tasks), list(findings)
        self.broken = broken or {}                      # template id -> names of items with malformed rules
        self.assignees, self.fail, self.ignore_task_status = list(assignees), fail or {}, ignore_task_status
        self.silent_raise = silent_raise
        self.trends = trends or {"result": {"status": "partial", "coverage": "incomplete_authorized_evidence",
                                            "complete": False, "totals": {}, "recurrence_total": 0,
                                            "recurrence_groups": [], "explicit_nonconformance_findings": [],
                                            "gaps": [{"code": "capa_audit_read_denied"}],
                                            "review_evidence_unavailable": "capa_audit_read_denied"}}
        self.calls, self.n, self.sessions = [], 0, []

    def writes(self, *names):
        return [(n, a) for n, a in self.calls if n in names]

    def _id(self, prefix):
        self.n += 1
        return f"{prefix}{self.n}"

    async def call_tool(self, name, arguments=None, *, jurisdiction):
        a = arguments or {}
        self.calls.append((name, a))
        if name in self.fail:
            raise AgentSwitchToolError(name, -32000, self.fail[name])
        page = lambda rows: {"data": rows[a.get("offset", 0):a.get("offset", 0) + a.get("limit", 20)], "total": len(rows)}  # noqa: E731
        if name == "ChecklistTemplate.list":
            return page(self.templates)
        if name == "ChecklistTemplate.get":
            return next(t for t in self.templates if t["id"] == a["id"])
        if name == "ChecklistTemplate.create":
            row = {"id": self._id("tpl"), **a, "is_active": 1 if a.get("is_active", True) else 0}
            self.templates.append(row)
            return row
        if name == "ChecklistTemplate.update":
            row = next(t for t in self.templates if t["id"] == a["id"])
            row.update({k: (1 if v is True else 0 if v is False else v) for k, v in a.items() if k != "id"})
            return row
        if name == "ChecklistTemplate.make.ChecklistRun":
            tpl = next(t for t in self.templates if t["id"] == a["id"])
            self.runs.append({"id": self._id("run"), "number": f"RUN-{self.n}", "name": a["name"],
                              "template_id": a["id"], "due_date": a["due_date"], "status": "draft",
                              "assigned_to_id": "u1", "notes": a["notes"], "items": tpl.get("items", []),
                              "required_items_pending": 1, "blocker_items_pending": 0})
            return {"status": "ok"}
        if name == "ChecklistRun.list":
            rows = [r for r in self.runs if "template_id" not in a or r["template_id"] == a["template_id"]]
            return page(rows)
        if name == "ChecklistRun.get":
            return next(r for r in self.runs if r["id"] == a["id"])
        if name == "ChecklistRun.submit_for_review":
            run = next(r for r in self.runs if r["id"] == a["id"])
            run["status"] = "submitted"
            return run
        if name == "endpoint.checklists.branch_rules":
            names = self.broken.get(a["template_id"], [])
            return {"result": {"items": [{"name": n, "problems": ["malformed_rule"]} for n in names] + [
                {"name": "fine", "problems": []}]}, "status": "ok"}
        if name == "AgentTodo.list":
            return page(self.todos)
        if name == "AgentTodo.create":
            row = {"id": self._id("todo"), "status": "open", **a}
            self.todos.append(row)
            return row
        if name == "AgentSession.create":
            row = {"id": self._id("sess"), **a}
            self.sessions.append(row)
            return row
        if name == "endpoint.agent_governance.escalations.assignees":
            return {"result": {"options": self.assignees}, "status": "ok"}
        if name == "endpoint.agent_governance.escalations.raise":
            if self.silent_raise or any(e["session_id"] == a["session_id"] for e in self.escalations):
                return {"result": {"ok": False, "reason_code": "session_already_handed_off"}, "status": "ok"}
            row = {"id": self._id("esc"), "status": "open", **a}
            self.escalations.append(row)
            return {"result": row, "status": "ok"}
        if name == "AgentEscalation.list":
            return page(self.escalations)
        if name == "AgentProvider.list":
            return {"data": [{"id": "prov-1", "name": "AgentSwitch AI"}], "total": 1}
        if name == "AgentTask.list":
            return page(self.tasks)
        if name == "AgentTask.create":
            row = {"id": self._id("task"), **{k: v for k, v in a.items() if k != "status" or not self.ignore_task_status},
                   }
            row.setdefault("status", "active")
            self.tasks.append(row)
            return row
        if name == "AgentTask.get":
            return next(t for t in self.tasks if t["id"] == a["id"])
        if name == "AgentTask.pause":
            next(t for t in self.tasks if t["id"] == a["id"])["status"] = "paused"
            return {"status": "paused"}
        if name == "SOPDocument.list":
            return page(self.sops)
        if name == "ChecklistCapaFinding.list":
            return page(self.findings)
        if name == "endpoint.checklists.capa_finding":
            row = {"id": self._id("find"), "checklist_run_id": a["checklist_run_id"], "finding_key": a["finding_key"]}
            self.findings.append(row)
            return row
        if name == "endpoint.checklists.capa_trends":
            return self.trends
        raise AssertionError(f"unexpected tool {name}")

    async def close(self):
        return None


def tpl(id, frequency="weekly", category="quality", items=1, name=None, active=1):
    return {"id": id, "name": name or f"T-{id}", "category": category, "frequency": frequency, "is_active": active,
            "default_assignee_id": "u", "items": [{"name": f"step {i}", "order": i} for i in range(items)]}


def run_row(id, template_id="t", due="2026-09-01", status="draft", owner="u1", owner_name="Ana", **extra):
    return {"id": id, "number": f"RUN-{id}", "name": f"R-{id}", "template_id": template_id, "due_date": due,
            "status": status, "assigned_to_id": owner, "_assigned_to_id_display": owner_name,
            "reviewer_id": None, "notes": None, "items": [], "updated_at": "2026-10-01T09:00:00", **extra}


def skills_for(seat, llm=None, goal=""):
    ctx = RunContext(run_id="r1", store=None, llm=llm, agentswitch=seat, clock=lambda: TODAY, goal=goal)
    return build_skills(ctx)


async def go(skills, name, **args):
    return await skills[name](TaskSpec("t", name, args))


def llm_returning(payload):
    async def llm(prompt, system, **kwargs):
        return {"text": payload if isinstance(payload, str) else json.dumps(payload)}
    return llm


# ================= recurring runs
async def test_recurring_runs_are_created_for_the_current_period_with_a_marker_and_skip_unrunnable_templates():
    seat = Seat(templates=[tpl("a", "weekly"), tpl("b", "monthly"), tpl("c", "daily"), tpl("d", "weekly", items=0)],
                broken={"c": ["bad item"]})
    out = await go(skills_for(seat), "create_recurring_runs", max_runs=10)
    actions = {o["template_id"]: o["action"] for o in out["outcomes"]}
    assert actions == {"c": "skipped", "a": "created", "d": "skipped", "b": "created"}
    skipped = {o["template_id"]: o["reason"] for o in out["outcomes"] if o["action"] == "skipped"}
    assert "malformed branch rules" in skipped["c"] and "no items" in skipped["d"]
    made = {r["template_id"]: r for r in seat.runs}
    assert made["a"]["due_date"] == "2026-10-11" and made["b"]["due_date"] == "2026-10-31"
    assert all(r["notes"].startswith(AGENT_MARKER) and r["status"] == "draft" for r in made.values())
    assert seat.writes("ChecklistRun.start") == []                  # recurring runs are created, not started


async def test_a_second_pass_creates_nothing_because_the_period_is_now_covered():
    seat = Seat(templates=[tpl("a", "weekly")])
    skills = skills_for(seat)
    await go(skills, "create_recurring_runs", max_runs=10)
    again = await go(skills, "create_recurring_runs", max_runs=10)
    assert again["created"] == 0 and again["candidates"] == 0 and len(seat.runs) == 1


async def test_the_cap_limits_runs_created_and_reports_what_was_not_reached():
    seat = Seat(templates=[tpl(f"t{i}", "weekly") for i in range(5)])
    out = await go(skills_for(seat), "create_recurring_runs", max_runs=2)
    assert out["created"] == 2 and out["not_reached"] == 3 and len(seat.runs) == 2


async def test_a_template_whose_branch_rules_cannot_be_read_is_not_run_blind():
    seat = Seat(templates=[tpl("a", "weekly")], fail={"endpoint.checklists.branch_rules": "denied"})
    out = await go(skills_for(seat), "create_recurring_runs", max_runs=10)
    assert out["created"] == 0 and seat.runs == []
    assert "could not check" in out["outcomes"][0]["reason"]


async def test_the_category_filter_limits_which_templates_are_considered():
    seat = Seat(templates=[tpl("a", "weekly", category="safety"), tpl("b", "weekly", category="hr")])
    out = await go(skills_for(seat), "create_recurring_runs", max_runs=10, category="safety")
    assert [o["template_id"] for o in out["outcomes"]] == ["a"]


async def test_a_failed_create_is_reported_and_the_rest_continue():
    class Flaky(Seat):
        async def call_tool(self, name, arguments=None, *, jurisdiction):
            if name == "ChecklistTemplate.make.ChecklistRun" and arguments["id"] == "a":
                raise AgentSwitchToolError(name, -32602, "refused")
            return await super().call_tool(name, arguments, jurisdiction=jurisdiction)

    seat = Flaky(templates=[tpl("a", "weekly"), tpl("b", "weekly")])
    out = await go(skills_for(seat), "create_recurring_runs", max_runs=10)
    assert {o["template_id"]: o["action"] for o in out["outcomes"]} == {"a": "failed", "b": "created"}


# ================= scheduling
async def test_scheduling_creates_a_paused_cron_task_once():
    seat = Seat()
    skills = skills_for(seat)
    out = await go(skills, "schedule_recurring_work", job="weekly_recurring_runs")
    assert out["action"] == "scheduled_paused" and out["status"] == "paused"
    [(_, args)] = seat.writes("AgentTask.create")
    assert args["schedule_type"] == "cron" and args["cron_expression"] == "0 6 * * 1" and args["status"] == "paused"
    assert args["name"].startswith("[seat23]") and args["provider_id"] == "prov-1"
    again = await go(skills, "schedule_recurring_work", job="weekly_recurring_runs")
    assert again["action"] == "already_scheduled" and len(seat.tasks) == 1


async def test_a_task_the_platform_creates_active_is_paused_straight_away():
    seat = Seat(ignore_task_status=True)
    out = await go(skills_for(seat), "schedule_recurring_work", job="monthly_safety_audit")
    assert out["status"] == "paused" and seat.writes("AgentTask.pause")


# ================= follow-ups
def overdue_runs():
    return [run_row("1", due="2026-09-01", owner_name="Ana"), run_row("2", due="2026-09-05", owner_name="Ana"),
            run_row("3", due="2026-09-03", owner_name="Bo"), run_row("4", due="2026-09-04", owner=None, owner_name=None),
            run_row("5", due="2026-10-20", status="submitted")]


async def test_followups_create_one_todo_per_owner_and_one_batched_escalation_per_issue():
    seat = Seat(runs=overdue_runs())
    out = await go(skills_for(seat), "raise_followups", max_todos=10, escalate=True)
    titles = sorted(t["title"] for t in seat.todos)
    assert titles == ["[seat23] Ana: 2 overdue checklist run(s)", "[seat23] Bo: 1 overdue checklist run(s)"]
    assert out["todos"]["created"] == 2
    reasons = [e["reason"] for e in seat.escalations]
    assert len(reasons) == 2 and any("without a reviewer" in r for r in reasons)
    assert any("without an assignee" in r for r in reasons)
    assert all(e["assignee_party_id"] == "party-meera" for e in seat.escalations)
    assert len(seat.sessions) == 2 and len({e["session_id"] for e in seat.escalations}) == 2   # one session each
    assert out["escalations"]["created"] == 2


async def test_running_followups_twice_creates_nothing_new():
    seat = Seat(runs=overdue_runs())
    skills = skills_for(seat)
    await go(skills, "raise_followups", max_todos=10, escalate=True)
    again = await go(skills, "raise_followups", max_todos=10, escalate=True)
    assert again["todos"]["created"] == 0 and again["escalations"]["created"] == 0
    assert len(seat.todos) == 2 and len(seat.escalations) == 2


async def test_escalate_false_makes_todos_only_and_no_assignee_means_no_escalation():
    seat = Seat(runs=overdue_runs())
    await go(skills_for(seat), "raise_followups", max_todos=10, escalate=False)
    assert len(seat.todos) == 2 and seat.escalations == [] and seat.sessions == []
    nobody = Seat(runs=overdue_runs(), assignees=())
    out = await go(skills_for(nobody), "raise_followups", max_todos=10, escalate=True)
    assert nobody.escalations == [] and "no assignable human" in json.dumps(out["escalations"])


async def test_the_todo_cap_chases_the_owners_with_the_most_overdue_runs_first():
    seat = Seat(runs=overdue_runs())
    await go(skills_for(seat), "raise_followups", max_todos=1, escalate=False)
    assert [t["title"] for t in seat.todos] == ["[seat23] Ana: 2 overdue checklist run(s)"]


# ================= template drafted from an SOP
SOP = {"id": "sop-1", "number": "SOP-2026-00101", "name": "Machine start-up", "status": "review", "category": "safety",
       "content": "<h3>Steps</h3><p>Check the guards and the emergency stop. Run the machine empty for one minute.</p>",
       "sections": [{"title": "Steps", "content": "Report any fault to the supervisor."}]}
GOOD = {"items": [{"name": "Check the guards"}, {"name": "Check the emergency stop"},
                  {"name": "Run the machine empty for one minute"}, {"name": "Report any fault to the supervisor"}]}


async def test_a_template_is_drafted_inactive_with_the_sops_steps_and_a_marker():
    seat = Seat(sops=[SOP])
    out = await go(skills_for(seat, llm_returning(GOOD)), "draft_template_from_sop", sop="SOP-2026-00101")
    assert out["is_active"] is False and out["items"] == 4 and out["already_existed"] is False
    [(_, args)] = seat.writes("ChecklistTemplate.create")
    assert args["is_active"] is False and args["category"] == "safety"
    assert AGENT_MARKER in args["description"] and "sop=sop-1" in args["description"]
    assert [i["order"] for i in args["items"]] == [1, 2, 3, 4] and all(i["is_blocker"] is False for i in args["items"])


async def test_drafting_twice_finds_the_first_template_and_creates_no_second():
    seat = Seat(sops=[SOP])
    skills = skills_for(seat, llm_returning(GOOD))
    await go(skills, "draft_template_from_sop", sop="SOP-2026-00101")
    again = await go(skills, "draft_template_from_sop", sop="sop-1")
    assert again["already_existed"] is True and len(seat.templates) == 1


async def test_a_draft_that_invents_a_figure_or_comes_from_an_unready_sop_writes_nothing():
    invented = {"items": [{"name": "Run the machine for 15 minutes"}]}
    seat = Seat(sops=[SOP])
    out = await go(skills_for(seat, llm_returning(invented)), "draft_template_from_sop", sop="SOP-2026-00101")
    assert out["error"] is True and out["code"] == "invalid_draft" and seat.templates == []   # no steps to fall back on
    for sop, code in (({**SOP, "status": "draft"}, "sop_not_ready"), ({**SOP, "content": None, "sections": []}, "sop_has_no_content")):
        seat = Seat(sops=[sop])
        res = await go(skills_for(seat, llm_returning(GOOD)), "draft_template_from_sop", sop="SOP-2026-00101")
        assert res["code"] == code and seat.templates == []
    missing = await go(skills_for(Seat()), "draft_template_from_sop", sop="SOP-9")
    assert missing["code"] == "sop_not_found"


async def test_a_template_the_platform_leaves_active_is_deactivated():
    class Sticky(Seat):
        async def call_tool(self, name, arguments=None, *, jurisdiction):
            out = await super().call_tool(name, arguments, jurisdiction=jurisdiction)
            if name == "ChecklistTemplate.create":
                self.templates[-1]["is_active"] = 1               # the platform ignored is_active=false
            return out

    seat = Sticky(sops=[SOP])
    out = await go(skills_for(seat, llm_returning(GOOD)), "draft_template_from_sop", sop="SOP-2026-00101")
    assert out["is_active"] is False and seat.writes("ChecklistTemplate.update")


# ================= CAPA
def rejected_run(id="9", template_id="t", items=None):
    return run_row(id, template_id, status="rejected", items=items if items is not None else [
        {"order": 1767, "name": "Guard", "is_required": True, "completed": False},
        {"order": 3, "name": "Done", "is_required": True, "completed": True}])


async def test_a_rejected_run_gets_a_finding_per_unfinished_required_item_and_a_followup_run():
    seat = Seat(templates=[tpl("t")], runs=[rejected_run()])
    out = await go(skills_for(seat), "open_capa_findings", max_findings=10)
    [(_, args)] = seat.writes("endpoint.checklists.capa_finding")
    assert args["operation"] == "create" and args["finding_key"] == "item:1767"
    assert args["evidence_kind"] == "explicit_item_finding" and args["classification"] == "nonconformance"
    assert args["source_revision"] == "2026-10-01T09:00:00" and "Guard" in args["finding_note"]
    followup = next(r for r in seat.runs if r["id"] != "9")
    assert followup["due_date"] == "2026-10-14" and "followup of RUN-9 item:1767" in followup["notes"]
    assert out["opened"] == 1 and out["outcomes"][0]["followup"]["action"] == "created"


async def test_capa_is_idempotent_and_flags_a_repeat():
    seat = Seat(templates=[tpl("t")], runs=[rejected_run()])
    skills = skills_for(seat)
    await go(skills, "open_capa_findings", max_findings=10)
    again = await go(skills, "open_capa_findings", max_findings=10)
    assert again["opened"] == 0 and len(seat.findings) == 1
    earlier = Seat(templates=[tpl("t")], runs=[rejected_run("8"), rejected_run("9")],
                   findings=[{"id": "f0", "checklist_run_id": "8", "finding_key": "item:1767"}])
    out = await go(skills_for(earlier), "open_capa_findings", max_findings=10)
    assert out["outcomes"][0]["repeat"] is True and "Repeat" in earlier.writes("endpoint.checklists.capa_finding")[0][1]["finding_note"]


async def test_no_rejected_runs_means_no_findings_and_a_refused_finding_is_reported():
    assert (await go(skills_for(Seat(templates=[tpl("t")], runs=[run_row("1")])), "open_capa_findings", max_findings=10))["opened"] == 0

    class Refusing(Seat):
        async def call_tool(self, name, arguments=None, *, jurisdiction):
            if name == "endpoint.checklists.capa_finding":
                raise AgentSwitchToolError(name, 409, "capa_source_changed")
            return await super().call_tool(name, arguments, jurisdiction=jurisdiction)

    seat = Refusing(templates=[tpl("t")], runs=[rejected_run()])
    out = await go(skills_for(seat), "open_capa_findings", max_findings=10)
    assert out["opened"] == 0 and "capa_source_changed" in json.dumps(out["outcomes"])
    assert not [r for r in seat.runs if r["id"] != "9"]                  # no follow-up without a finding


async def test_repeat_findings_report_the_platforms_coverage_gap_honestly():
    seat = Seat()
    out = await go(skills_for(seat), "list_repeat_findings", days=365)
    assert out["complete"] is False and out["review_evidence_unavailable"] == "capa_audit_read_denied"
    assert out["window"] == {"from": "2025-10-07", "to": "2026-10-07"}
    [(_, args)] = seat.writes("endpoint.checklists.capa_trends")
    assert args == {"from": "2025-10-07", "to": "2026-10-07"}


# ================= guarded submit
def mine(**extra):
    return run_row("7", status="in_progress", notes=f"{AGENT_MARKER} audit", required_items_pending=0,
                   blocker_items_pending=0, **extra)


async def test_a_clean_run_the_agent_created_is_submitted_for_review():
    seat = Seat(runs=[mine()])
    out = await go(skills_for(seat), "submit_run", run_id="7")
    assert out["action"] == "submitted_for_review" and out["status"] == "submitted"


async def test_submit_refuses_pending_blockers_pending_required_items_other_teams_runs_and_wrong_states():
    cases = [({"blocker_items_pending": 2}, "pending_items"), ({"required_items_pending": 1}, "pending_items"),
             ({"notes": "someone else's"}, "not_created_by_agent"), ({"status": "draft"}, "invalid_state")]
    for change, code in cases:
        seat = Seat(runs=[{**mine(), **change}])
        out = await go(skills_for(seat), "submit_run", run_id="7")
        assert out["error"] is True and out["code"] == code, (change, out)
        assert seat.writes("ChecklistRun.submit_for_review") == []


# ================= the manifest
def test_the_seven_new_capabilities_are_declared_and_only_the_report_is_read_only():
    from checklist_agent.capabilities import default_registry
    registry = default_registry()
    writes = {"create_recurring_runs", "schedule_recurring_work", "raise_followups", "draft_template_from_sop",
              "open_capa_findings", "submit_run"}
    assert writes | {"list_repeat_findings"} <= set(registry.names())
    assert all(registry.get(n).side_effect for n in writes)
    assert not registry.get("list_repeat_findings").side_effect
    assert not {n for n in registry.names() if any(w in n for w in ("approve", "reject", "publish", "delete"))}


def test_arguments_get_safe_defaults_and_bounds():
    from checklist_agent.capabilities import CapabilityError, default_registry
    registry = default_registry()
    assert registry.validate("create_recurring_runs", {}) == {"max_runs": 10}
    assert registry.validate("raise_followups", {}) == {"max_todos": 10, "escalate": True}
    assert registry.validate("list_repeat_findings", {}) == {"days": 365}
    for name, bad in (("create_recurring_runs", {"max_runs": 500}), ("schedule_recurring_work", {"job": "every_minute"}),
                      ("raise_followups", {"max_todos": 0}), ("schedule_recurring_work", {})):
        try:
            registry.validate(name, bad)
        except CapabilityError:
            continue
        raise AssertionError(f"{name} accepted {bad}")


async def test_an_escalation_the_platform_silently_declines_is_reported_not_counted_as_created():
    seat = Seat(runs=overdue_runs(), silent_raise=True)
    out = await go(skills_for(seat), "raise_followups", max_todos=1, escalate=True)
    assert out["escalations"]["created"] == 0 and seat.escalations == []
    assert all("not confirmed" in json.dumps(i) for i in out["escalations"]["items"])
