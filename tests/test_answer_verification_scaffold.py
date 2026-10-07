"""Implementation scaffolding. NOT the team's graded tests."""
from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace

from checklist_agent.core.live_graph import TaskSpec
from checklist_agent.run import run_goal
from checklist_agent.verify import render_evidence, ungrounded
from checklist_agent.workers import RunContext, build_skills

EVIDENCE = [{"capability": "list_overdue_runs", "input": {},
             "result": {"total": 119, "as_of": "2026-10-07", "by_status": {"draft": 97, "submitted": 19},
                        "completion_pct": 66.7, "oldest": [{"id": "9b1f4c2e-0000-4000-8000-000000000001",
                                                            "name": "5S audit — Press Shop"}]}}]


def clean(text, extra=""):
    return ungrounded(text, EVIDENCE, extra) == {"numbers": [], "ids": []}


def test_numbers_dates_and_ids_taken_from_the_evidence_are_grounded():
    assert clean("119 runs are overdue as of 2026-10-07: 97 draft and 19 submitted.")
    assert clean("The oldest is 9b1f4c2e-0000-4000-8000-000000000001, the 5S audit — Press Shop.")
    assert clean("About 1,000 would be wrong", extra="we have 1000") is True


def test_an_invented_number_date_or_id_is_reported():
    assert ungrounded("120 runs are overdue", EVIDENCE)["numbers"] == ["120"]
    assert ungrounded("as of 2026-10-09", EVIDENCE)["ids"] == ["2026-10-09"]
    assert ungrounded("run SOP-2026-00999", EVIDENCE)["ids"] == ["SOP-2026-00999"]
    assert ungrounded("97 draft and 20 submitted", EVIDENCE)["numbers"] == ["20"]


def test_list_markers_and_numbers_glued_to_words_are_not_claims():
    assert clean("1. First check\n2) Second check\n3. Third check")
    assert clean("Q4 and 5S and 74mm are labels, not figures")


def test_a_rounded_figure_is_grounded_but_a_different_one_is_not():
    assert clean("completion is 67%") and clean("completion is 66.7%")
    assert ungrounded("completion is 68%", EVIDENCE)["numbers"] == ["68"]


def test_render_evidence_is_deterministic_and_carries_the_figures():
    text = render_evidence(EVIDENCE)
    assert "list_overdue_runs" in text and "119" in text and "draft: 97" in text
    assert text == render_evidence(EVIDENCE)
    assert ungrounded(text, EVIDENCE) == {"numbers": [], "ids": []}


def test_a_failed_capability_is_rendered_as_a_failure_not_dropped():
    text = render_evidence([{"capability": "audit_templates", "input": {},
                             "result": {"error": True, "code": "transport_error", "message": "timeout"}}])
    assert "audit_templates" in text and "timeout" in text


# --- the answer worker verifies, retries once, then falls back to the evidence
class Store:
    def snapshot(self, run_id):
        return SimpleNamespace(nodes={"a": {"skill": "list_overdue_runs", "state": "succeeded", "input": {},
                                            "result": EVIDENCE[0]["result"]}})


def answer_skill(replies):
    seen = []

    async def llm(prompt, system, **kwargs):
        seen.append(prompt)
        return {"text": replies[len(seen) - 1], "provider": "p", "model": "m"}

    ctx = RunContext(run_id="r", store=Store(), llm=llm, agentswitch=None, clock=lambda: date(2026, 10, 7))
    return build_skills(ctx)["answer_with_evidence"], seen


TASK = TaskSpec("a", "answer_with_evidence", {"query": "Which checklists are overdue?"})


async def test_a_grounded_answer_is_accepted_first_time():
    skill, seen = answer_skill(["119 runs are overdue."])
    out = await skill(TASK)
    assert out["text"] == "119 runs are overdue." and len(seen) == 1
    assert out["verification"] == {"verified": True, "attempts": 1, "source": "model", "ungrounded": []}


async def test_an_invented_figure_gets_one_correction_attempt_that_names_the_problem():
    skill, seen = answer_skill(["120 runs are overdue.", "119 runs are overdue."])
    out = await skill(TASK)
    assert out["text"] == "119 runs are overdue." and len(seen) == 2
    assert "120" in seen[1] and out["verification"]["attempts"] == 2 and out["verification"]["verified"]


async def test_two_bad_answers_fall_back_to_the_evidence_never_to_the_invented_figures():
    skill, seen = answer_skill(["120 runs are overdue.", "121 runs are overdue."])
    out = await skill(TASK)
    assert "120" not in out["text"] and "121" not in out["text"] and "119" in out["text"]
    assert out["verification"]["source"] == "evidence_render" and out["verification"]["verified"] is True
    assert out["verification"]["rejected"] == ["121"] or "121" in out["verification"]["rejected"]


async def test_run_goal_reports_the_verification_with_the_answer():
    class Scripted:
        def __init__(self, steps):
            self.steps, self.i = steps, 0

        async def complete(self, prompt, system, **kwargs):
            reply = self.steps[self.i]
            self.i += 1
            return {"text": reply if isinstance(reply, str) else json.dumps(reply)}

        async def close(self):
            return None

    class Platform:
        async def call_tool(self, name, arguments=None, *, jurisdiction):
            rows = [{"id": "r1", "name": "R1", "template_id": "t", "due_date": "2026-09-01", "status": "draft"}]
            return {"data": rows, "total": 1}

        async def close(self):
            return None

    plan1 = {"add": [{"id": "late", "capability": "list_overdue_runs", "arguments": {}, "depends_on": []}],
             "cancel": [], "finish": False, "reason": "read"}
    plan2 = {"add": [{"id": "answer", "capability": "answer_with_evidence", "arguments": {"query": "overdue?"},
                      "depends_on": ["late"]}], "cancel": [], "finish": False, "reason": "answer"}
    llm = Scripted([plan1, plan2, {"ready": True, "missing": [], "reason": "ok"},
                    "7 runs are overdue.", "1 run is overdue."])
    result = await run_goal("overdue?", llm=llm, agentswitch=Platform(), clock=lambda: date(2026, 10, 7))
    assert result["answer"] == "1 run is overdue."
    assert result["verification"]["verified"] is True and result["verification"]["attempts"] == 2


# --- the SOP draft is held to the description the user gave
def test_a_sop_draft_may_not_introduce_figures_the_description_never_stated():
    description = "Before starting, check the guards and run the machine empty for one minute."
    steps = "1. Check guards. 2. Run empty for one minute."
    assert ungrounded("Start-up Steps " + steps, [], description) == {"numbers": [], "ids": []}
    assert ungrounded("Start-up Steps 1. Check guards. 2. Run empty for 15 minutes.", [], description)[
        "numbers"] == ["15"]


async def test_the_correction_tells_the_model_to_copy_ids_and_dates_exactly():
    skill, seen = answer_skill(["27 Sep 2025 is the oldest.", "119 runs are overdue."])
    await skill(TASK)
    assert "exactly" in seen[1] and "YYYY-MM-DD" in seen[1]


def test_the_answer_prompt_asks_for_ids_and_dates_to_be_copied_not_reformatted():
    from checklist_agent.workers import _ANSWER_SYSTEM
    assert "exactly as written" in _ANSWER_SYSTEM and "YYYY-MM-DD" in _ANSWER_SYSTEM


async def test_a_run_that_ends_without_an_answer_says_why():
    class Garbage:
        async def complete(self, prompt, system, **kwargs):
            return {"text": "this is not json"}

        async def close(self):
            return None

    class NoPlatform:
        async def close(self):
            return None

    result = await run_goal("Publish the SOP", llm=Garbage(), agentswitch=NoPlatform())
    assert result["answer"] is None and result["verification"] is None
    assert result["failure"].startswith("planner failed validation")


def test_the_parts_of_a_date_that_is_in_the_evidence_are_grounded_when_reformatted():
    # evidence holds 2026-10-07; "7 Oct 2026" and "07/10/2026" are the same date written differently
    assert clean("as of 7 Oct 2026")
    assert clean("as of 07/10/2026")
    assert ungrounded("as of 8 Oct 2026", EVIDENCE) == {"numbers": [], "ids": ["8 Oct 2026"]}   # a different date
    assert ungrounded("7 runs", EVIDENCE)["numbers"] == ["7"]      # the 7 in 2026-10-07 does not ground a count


def test_a_gateway_failure_in_the_answer_step_is_reported_with_its_cause():
    from checklist_agent.run import _failure
    events = [{"kind": "graph_patched", "reason": "answer now", "payload": {}},
              {"kind": "task_failed", "reason": None,
               "payload": {"error": "RuntimeError: gateway /v1/chat returned 503: all providers unavailable"}},
              {"kind": "graph_patched", "reason": "terminal text capability answer_1 failed", "payload": {}}]
    assert "gateway /v1/chat returned 503" in _failure(events)
    assert _failure([]) == "the run ended without producing an answer"


def test_non_breaking_and_typographic_hyphens_in_dates_and_ids_do_not_hide_them():
    # models often emit U+2011 (non-breaking hyphen) or U+2013 (en dash) inside dates and ids
    assert clean("as of 2026‑10‑07")
    assert clean("oldest 9b1f4c2e‑0000‑4000‑8000‑000000000001")
    assert clean("as of 2026–10–07")
    assert ungrounded("as of 2026‑10‑09", EVIDENCE)["ids"] == ["2026-10-09"]
    # an em dash in a name is real punctuation and must stay one
    assert clean("the 5S audit — Press Shop")


def test_the_clock_digits_of_a_timestamp_in_the_evidence_ground_nothing():
    evidence = [{"capability": "c", "input": {}, "result": {"total": 119, "read_at": "2026-10-07T17:07:07.123456+00:00"}}]
    assert ungrounded("7 runs and 17 runs and 123456 runs", evidence)["numbers"] == ["7", "17", "123456"]
    assert ungrounded("119 runs, read on 2026-10-07", evidence) == {"numbers": [], "ids": []}
