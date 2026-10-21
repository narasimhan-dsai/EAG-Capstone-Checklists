"""Implementation scaffolding for the instructor's third review. NOT the team's graded tests."""
from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace

from test_section2_workers_scaffold import Seat
from test_workers_write_scaffold import GOOD_DRAFT, FakePlatform

from checklist_agent.capabilities import default_registry
from checklist_agent.core.live_graph import Event, GraphSnapshot, TaskSpec
from checklist_agent.llm import OpenAIClient
from checklist_agent.planner import ChecklistPlanner
from checklist_agent.verify import compact_evidence, ungrounded
from checklist_agent.workers import RunContext, build_skills
from harness.official import to_result_task
from harness.runner import _answer_note
from harness.scoring import score

TODAY = date(2026, 10, 7)


# ================= 1. the finish reason is recorded
def completion(content, finish_reason="stop"):
    choice = SimpleNamespace(message=SimpleNamespace(content=content), finish_reason=finish_reason)
    return SimpleNamespace(model="m", usage=None, choices=[choice])


class Completions:
    def __init__(self, reply):
        self.reply = reply

    async def create(self, **kwargs):
        return self.reply


async def test_the_model_client_returns_why_the_model_stopped():
    client = SimpleNamespace(chat=SimpleNamespace(completions=Completions(completion("", "length"))))
    out = await OpenAIClient(client=client, model="m").complete("q", "s")
    assert out["text"] == "" and out["finish_reason"] == "length"
    bare = SimpleNamespace(model="m", usage=None, choices=[SimpleNamespace(message=SimpleNamespace(content="x"))])
    client = SimpleNamespace(chat=SimpleNamespace(completions=Completions(bare)))
    assert (await OpenAIClient(client=client, model="m").complete("q", "s"))["finish_reason"] is None


class Store:
    def snapshot(self, run_id):
        return SimpleNamespace(nodes={"a": {"skill": "list_overdue_runs", "state": "succeeded", "input": {},
                                            "result": {"total": 119}}})


def answer_skill(replies, goal=""):
    seen = []

    async def llm(prompt, system, **kwargs):
        reply = replies[len(seen)]
        seen.append(prompt)
        return reply

    ctx = RunContext(run_id="r", store=Store(), llm=llm, agentswitch=None, clock=lambda: TODAY, goal=goal)
    return build_skills(ctx)["answer_with_evidence"], seen


TASK = TaskSpec("a", "answer_with_evidence", {"query": "Which checklists are overdue?"})


async def test_the_answer_records_why_the_model_stopped_whether_it_answered_or_not():
    skill, _ = answer_skill([{"text": "", "finish_reason": "length"}, {"text": "", "finish_reason": "length"}])
    out = await skill(TASK)
    assert out["verification"]["source"] == "evidence_render" and out["verification"]["finish_reason"] == "length"
    skill, _ = answer_skill([{"text": "119 runs are overdue.", "finish_reason": "stop"}])
    assert (await skill(TASK))["verification"]["finish_reason"] == "stop"


async def test_a_planner_that_gives_up_names_how_its_last_reply_ended():
    async def llm(prompt, system, **kwargs):
        return {"text": "this is not json", "finish_reason": "length"}

    planner = ChecklistPlanner(llm, default_registry(), goal="g", repair_attempts=0)
    patch = await planner.plan(GraphSnapshot("r", False, {}, ()), Event(1, "run_started", None, {}))
    assert "finish_reason=length" in patch.reason


# ================= 2. the answer prompt carries counts and ids, not whole rows
def big_rows(n):
    return [{"id": f"id-{i}", "number": f"RUN-{i}", "name": "N" * 300, "status": "draft", "due_date": "2026-09-01",
             "days_late": i, "blocked_on": "owner", "assigned": True, "internal_blob": "x" * 400, "other": [1, 2, 3]}
            for i in range(n)]


def test_evidence_is_cut_to_counts_splits_and_a_few_identifying_rows():
    full = [{"capability": "list_overdue_runs", "input": {}, "result": {
        "total": 119, "by_status": {"draft": 97, "submitted": 19}, "rule": "due before today",
        "oldest": big_rows(25)}}]
    out = compact_evidence(full)[0]["result"]
    assert out["total"] == 119 and out["by_status"] == {"draft": 97, "submitted": 19}
    assert len(out["oldest"]) == 9 and out["oldest"][-1] == {"_omitted": 17}
    first = out["oldest"][0]
    assert first["id"] == "id-0" and first["number"] == "RUN-0" and first["days_late"] == 0
    assert "internal_blob" not in first and "other" not in first and len(first["name"]) <= 161
    assert len(json.dumps(compact_evidence(full))) < len(json.dumps(full)) / 4


def test_short_evidence_and_errors_are_left_alone():
    small = [{"capability": "c", "input": {}, "result": {"total": 3, "oldest": big_rows(3)[:1]}},
             {"capability": "d", "input": {}, "result": {"error": True, "code": "transport_error", "message": "timeout"}}]
    out = compact_evidence(small)
    assert out[1]["result"] == {"error": True, "code": "transport_error", "message": "timeout"}
    assert out[0]["result"]["total"] == 3 and len(out[0]["result"]["oldest"]) == 1


async def test_the_model_sees_the_short_form_but_the_answer_is_still_checked_against_everything():
    class Wide:
        def snapshot(self, run_id):
            return SimpleNamespace(nodes={"a": {"skill": "list_overdue_runs", "state": "succeeded", "input": {},
                                                "result": {"total": 119, "oldest": big_rows(25)}}})

    seen = []

    async def llm(prompt, system, **kwargs):
        seen.append(prompt)
        return {"text": "119 runs are overdue; the 24th oldest has 24 days late."}

    ctx = RunContext(run_id="r", store=Wide(), llm=llm, agentswitch=None, clock=lambda: TODAY)
    out = await build_skills(ctx)["answer_with_evidence"](TASK)
    assert "internal_blob" not in seen[0] and "RUN-24" not in seen[0] and "_omitted" in seen[0]
    assert out["verification"]["source"] == "model"          # 24 is in the full evidence, so it is not invented


# ================= 3. the SOP is drafted from the request's own description
REQUEST = ("Start the audit and turn this process description into an SOP: Check the guards and the emergency "
           "stop. Run the machine empty for one minute. Report any fault to the supervisor.")
FROM_REQUEST = "Check the guards and the emergency stop. Run the machine empty for one minute. Report any fault to the supervisor."


def sop_skill(platform, replies, goal=REQUEST):
    prompts = []

    async def llm(prompt, system, **kwargs):
        prompts.append(prompt)
        step = replies[min(len(prompts) - 1, len(replies) - 1)]
        return {"text": step if isinstance(step, str) else json.dumps(step)}

    ctx = RunContext(run_id="r1", store=None, llm=llm, agentswitch=platform, clock=lambda: TODAY, goal=goal)
    return build_skills(ctx)["process_to_sop"], prompts


async def test_the_description_comes_from_the_request_not_from_the_plannersCopy():
    platform = FakePlatform()
    skill, prompts = sop_skill(platform, [GOOD_DRAFT])
    out = await skill(TaskSpec("p", "process_to_sop", {"process_description": "a loose paraphrase about guards"}))
    assert json.loads(prompts[0])["process_description"] == FROM_REQUEST
    assert out["status"] == "review"


async def test_the_planner_may_leave_the_description_out_altogether():
    from checklist_agent.capabilities import default_registry
    assert default_registry().validate("process_to_sop", {}) == {}
    skill, prompts = sop_skill(FakePlatform(), [GOOD_DRAFT])
    out = await skill(TaskSpec("p", "process_to_sop", {}))
    assert out["status"] == "review" and json.loads(prompts[0])["process_description"] == FROM_REQUEST


async def test_without_a_colon_the_planners_text_is_used_only_if_it_is_from_the_request():
    goal = "Please make an SOP out of: x"                       # nothing usable after the colon
    skill, _ = sop_skill(FakePlatform(), [GOOD_DRAFT], goal="Turn this into an SOP. Check the guards and the stop.")
    ok = await skill(TaskSpec("p", "process_to_sop", {"process_description": "Check the guards and the stop."}))
    assert ok["status"] == "review"
    skill, _ = sop_skill(FakePlatform(), [GOOD_DRAFT], goal=goal)
    bad = await skill(TaskSpec("p", "process_to_sop", {"process_description": "something a run name said"}))
    assert bad["code"] == "description_not_from_request"


# ================= 3b. an unusable draft is asked for again once
async def test_an_unreadable_sop_draft_is_retried_once_and_the_second_prompt_names_the_problem():
    platform = FakePlatform()
    skill, prompts = sop_skill(platform, ["this is not json", GOOD_DRAFT])
    out = await skill(TaskSpec("p", "process_to_sop", {}))
    assert out["status"] == "review" and len(prompts) == 2 and "correction" in json.loads(prompts[1])


async def test_two_unusable_drafts_create_nothing_and_cost_exactly_two_calls():
    platform = FakePlatform()
    skill, prompts = sop_skill(platform, ["not json", "still not json"])
    out = await skill(TaskSpec("p", "process_to_sop", {}))
    assert out["code"] == "invalid_draft" and len(prompts) == 2
    assert not [n for n, _ in platform.calls if n == "SOPDocument.create"]


async def test_a_template_draft_is_retried_once_too():
    sop = {"id": "sop-1", "number": "SOP-1", "name": "Machine start-up", "status": "review", "category": "safety",
           "content": "<p>Check the guards and the emergency stop. Run the machine empty. Report any fault.</p>",
           "sections": []}
    platform = Seat(sops=[sop])
    prompts = []

    async def llm(prompt, system, **kwargs):
        prompts.append(prompt)
        return {"text": "nonsense" if len(prompts) == 1 else json.dumps(
            {"items": ["Check the guards", "Check the emergency stop", "Report any fault"]})}

    ctx = RunContext(run_id="r", store=None, llm=llm, agentswitch=platform, clock=lambda: TODAY)
    out = await build_skills(ctx)["draft_template_from_sop"](TaskSpec("t", "draft_template_from_sop", {"sop": "SOP-1"}))
    assert out["is_active"] is False and out["items"] == 3 and len(prompts) == 2


# ================= 4. a failed check says what was observed, including the tool that wrote
def test_a_failed_check_carries_what_was_observed_and_names_the_writing_tools():
    results = [{"claim": "the agent made no write call", "ok": False, "observed": [
        {"seq": 3, "tool": "ChecklistRun.start", "args": {"id": "x"}}, {"seq": 4, "tool": "AgentTodo.create", "args": {}},
        {"seq": 5, "tool": "AgentTodo.create", "args": {}}]},
        {"claim": "the total matches", "ok": False, "observed": {"reported": 2, "expected": 3}},
        {"claim": "fine", "ok": True, "observed": {}}]
    out = score({"result": {"patch_events": []}}, results)
    assert out["failed_claims"] == ["the agent made no write call", "the total matches"]       # unchanged
    assert out["failed_details"][0] == "the agent made no write call: tools: ChecklistRun.start, AgentTodo.create"
    assert '"reported": 2' in out["failed_details"][1]


def test_the_results_evidence_names_the_tool_that_wrote():
    row = {"id": "t", "state": "fail", "claims_passed": 1, "claims_total": 2, "failed_claims": ["no write"],
           "failed_details": ["the agent made no write call: tools: ChecklistRun.start"]}
    out = to_result_task("suryodaya", {"id": "t", "goal": "g"}, row)
    assert "tools: ChecklistRun.start" in out["evidence"] and len(out["evidence"]) <= 500


# ================= 1b. a replaced answer is noted, with the reason, even when the task passes
def record_with(verification):
    return {"result": {"verification": verification, "nodes": {}, "failure": None}}


def test_a_replaced_answer_is_noted_with_why():
    note = _answer_note(record_with({"source": "evidence_render", "attempts": 2, "rejected": ["(the answer was empty)"],
                                     "finish_reason": "length", "llm_error": "ReadTimeout: slow"}))
    assert "empty" in note and "finish_reason=length" in note and "ReadTimeout" in note
    assert "no written answer" in _answer_note(record_with({"source": "evidence_render", "attempts": 0}))
    assert _answer_note(record_with({"source": "model", "attempts": 1})) == ""
    assert _answer_note({"result": None}) == ""


def test_the_note_reaches_the_evidence_for_passing_and_failing_tasks():
    base = {"id": "t", "claims_total": 3, "note": "the model's answer was replaced by the data (finish_reason=length)"}
    ok = to_result_task("suryodaya", {"id": "t", "goal": "g"}, {**base, "state": "pass", "claims_passed": 3})
    assert ok["evidence"].startswith("3/3 checks passed") and "finish_reason=length" in ok["evidence"]
    bad = to_result_task("suryodaya", {"id": "t", "goal": "g"}, {**base, "state": "fail", "claims_passed": 1,
                                                                 "failed_claims": ["x"], "diagnosis": "boom"})
    assert "cause: boom" in bad["evidence"] and "finish_reason=length" in bad["evidence"]
    assert ungrounded("", [], "") == {"numbers": [], "ids": []}
