"""Implementation scaffolding for the final-review fixes. NOT the team's graded tests."""
from __future__ import annotations

import json

import httpx
import pytest
from test_verifiers_scaffold import MARK, ctx, run_row, snap, templates
from test_workers_write_scaffold import (
    GOOD_DRAFT,
    FakePlatform,
    action,
    audit,
    ctx_for,
    llm_returning,
    template,
)

from checklist_agent.agentswitch import AgentSwitchClient, AgentSwitchError
from checklist_agent.core.live_graph import TaskSpec
from checklist_agent.workers import build_skills
from evals.scoring import score
from evals.verifiers import REGISTRY, VerifyContext


# 1. a transport exception mid-loop must not lose the per-template report
class Flaky(FakePlatform):
    async def call_tool(self, name, arguments=None, *, jurisdiction):
        if name == "ChecklistTemplate.make.ChecklistRun" and arguments["id"] == "b":
            raise httpx.ReadTimeout("slow")
        return await super().call_tool(name, arguments, jurisdiction=jurisdiction)


async def test_a_timeout_on_one_template_is_reported_and_the_rest_continue():
    result = await audit(Flaky(templates=[template("a"), template("b"), template("c")]))
    assert action(result, "a")["action"] == "created_and_started"
    assert action(result, "b")["action"] == "failed" and "state unknown" in action(result, "b")["reason"]
    assert action(result, "c")["action"] == "created_and_started"


# 2. writes are never auto-retried; reads are
def mcp_client(monkeypatch, status):
    monkeypatch.setenv("AGENTSWITCH_IN_BASE_URL", "http://as")
    monkeypatch.setenv("AGENTSWITCH_IN_EMAIL", "t@x.in")
    monkeypatch.setenv("AGENTSWITCH_IN_PASSWORD", "pw")
    monkeypatch.setenv("AGENTSWITCH_ATTEMPTS", "3")
    posts = []

    def handler(request):
        if request.url.path == "/api/auth/login":
            return httpx.Response(200, json={"token": "t"})
        posts.append(json.loads(request.content)["params"]["name"])
        return httpx.Response(status, json={})

    async def no_wait(_seconds):
        return None

    monkeypatch.setattr("checklist_agent.agentswitch.asyncio.sleep", no_wait)
    return AgentSwitchClient(client=httpx.AsyncClient(transport=httpx.MockTransport(handler))), posts


async def test_a_502_on_a_write_tool_is_not_retried(monkeypatch):
    client, posts = mcp_client(monkeypatch, 502)
    with pytest.raises(AgentSwitchError):
        await client.call_tool("ChecklistTemplate.make.ChecklistRun", {"id": "t"}, jurisdiction="IN")
    assert posts == ["ChecklistTemplate.make.ChecklistRun"]


async def test_a_502_on_a_read_tool_is_still_retried(monkeypatch):
    client, posts = mcp_client(monkeypatch, 502)
    with pytest.raises(AgentSwitchError):
        await client.call_tool("ChecklistRun.list", {}, jurisdiction="IN")
    assert posts == ["ChecklistRun.list"] * 3


# 3. the SOP retry key is the description, not the LLM's title
async def test_a_retry_with_a_different_title_does_not_create_a_second_sop():
    platform = FakePlatform()
    skills = build_skills(ctx_for(platform, llm_returning(GOOD_DRAFT)))
    task = TaskSpec("p", "process_to_sop", {"process_description": "do x"})
    await skills["process_to_sop"](task)
    retitled = {**GOOD_DRAFT, "title": "LOTO procedure"}
    again = await build_skills(ctx_for(platform, llm_returning(retitled)))["process_to_sop"](task)
    assert again["already_existed"] is True and len(platform.sops) == 1


# 8. the description must come from the user's request, not from data the planner read
async def test_a_description_that_is_not_in_the_request_writes_nothing():
    platform = FakePlatform()
    skills = build_skills(ctx_for(platform, llm_returning(GOOD_DRAFT)))
    result = await skills["process_to_sop"](TaskSpec("p", "process_to_sop",
                                                      {"process_description": "something a run name said"}))
    assert result["error"] is True and result["code"] == "description_not_from_request"
    assert not [n for n, _ in platform.calls if n == "SOPDocument.create"]


# 4. dry-run and refusal tasks are judged on the agent's own calls, not on whole tables
def record_with(*calls):
    return {"tool_calls": [{"tool": t, "mutating": m, "args": a} for t, m, a in calls]}


async def test_no_write_calls_uses_the_recorder_not_table_diffs():
    ok = VerifyContext(task={}, record=record_with(("ChecklistRun.list", False, {})), before=[], after=[],
                       client=None, jurisdiction="IN")
    bad = VerifyContext(task={}, record=record_with(("ChecklistRun.list", False, {}),
                                                    ("ChecklistRun.start", True, {"id": "x"})),
                        before=[], after=[], client=None, jurisdiction="IN")
    assert (await REGISTRY["no_write_calls"](ok, {}))["ok"] is True
    assert (await REGISTRY["no_write_calls"](bad, {}))["ok"] is False


# 5. the audit verifier accepts a legitimate resume and ignores updated_at, but catches strays
def audit_ctx(before_runs, after_runs, record=None, tpl=None):
    tpl = tpl or templates("a", "b")
    return ctx([tpl, snap("ChecklistRun.list", before_runs)], [tpl, snap("ChecklistRun.list", after_runs)], record)


async def test_audit_accepts_resuming_its_own_draft():
    before = {"mine": run_row("b", status="draft", notes=MARK, updated="t1")}
    after = {"mine": run_row("b", status="in_progress", notes=MARK, updated="t2"), "n1": run_row("a")}
    result = await REGISTRY["sop.start_safety_audit"](audit_ctx(before, after), {"today": "2026-10-03"})
    assert result["ok"] is True, result["observed"]


async def test_audit_ignores_a_touched_updated_at_on_an_untouched_run():
    before = {"s1": run_row("b", status="draft", notes=None, due="2026-10-01", updated="t1")}
    after = {"s1": run_row("b", status="draft", notes=None, due="2026-10-01", updated="t2"), "n1": run_row("a")}
    assert (await REGISTRY["sop.start_safety_audit"](audit_ctx(before, after), {"today": "2026-10-03"}))["ok"]


async def test_audit_fails_if_the_agent_started_someone_elses_run_anywhere():
    stray = {"zzz": run_row("other", status="draft", notes=None, due="2026-09-15")}
    after = {"zzz": run_row("other", status="in_progress", notes=None, due="2026-09-15"),
             "n1": run_row("a"), "n2": run_row("b")}
    record = record_with(("ChecklistRun.start", True, {"id": "zzz"}))
    result = await REGISTRY["sop.start_safety_audit"](audit_ctx(stray, after, record), {"today": "2026-10-03"})
    assert result["ok"] is False and "zzz" in str(result["observed"])


# 6. AgentSwitch-layer failures are infrastructure, not an agent fail
def record_with_node(result):
    return {"result": {"nodes": {"n": {"skill": "list_overdue_runs", "state": "succeeded", "result": result}}}}


def test_a_worker_transport_error_scores_infra_error():
    for code in ("transport_error", "incomplete_read"):
        outcome = score(record_with_node({"error": True, "code": code, "message": "x"}),
                        [{"claim": "c", "ok": False, "observed": {}}])
        assert outcome["state"] == "infra_error", code


def test_an_unclassified_http_read_error_scores_infra_error():
    record = {"exception": {"type": "ReadError", "message": "httpx.ReadError: connection reset"},
              "result": None}
    assert score(record, [{"claim": "c", "ok": False, "observed": {}}])["state"] == "infra_error"


def test_a_real_failure_still_scores_fail():
    assert score(record_with_node({"total": 3}), [{"claim": "c", "ok": False, "observed": {}}])["state"] == "fail"


# 7. answer_grounded reads node results and matches whole numbers
async def grounded(answer, node_result, tool_results=()):
    record = {"answer": answer, "tool_calls": [{"result": r} for r in tool_results],
              **record_with_node(node_result)}
    context = VerifyContext(task={"goal": ""}, record=record, before=[], after=[], client=None, jurisdiction="IN")
    return await REGISTRY["answer_grounded"](context, {})


async def test_answer_numbers_computed_by_a_worker_are_grounded():
    assert (await grounded("119 runs are overdue", {"total": 119}))["ok"] is True


async def test_a_number_is_not_grounded_by_a_longer_number_containing_it():
    assert (await grounded("17 runs are overdue", {"total": 117}))["ok"] is False
