"""Verifier types. Mechanism only: which verifiers a task uses, and the values
it expects, are written by the team in the task file.

Each verifier is ``async fn(ctx, params) -> {claim, ok, observed}``, the same
shape as the reference ``Proof.check``. A verifier that cannot judge (missing
or incomplete data) raises :class:`InfraError`; scoring turns that into
``infra_error``, never into a pass. Register new types with ``@verifier``;
task files only ever name a type and pass data.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Awaitable, Callable

from checklist_agent.checklists import load_rules
from checklist_agent.checklists.records import AGENT_MARKER, template_from_row
from checklist_agent.checklists.scope import audit_scope


class InfraError(RuntimeError):
    """The claim cannot be judged (incomplete snapshot, unreadable state)."""


@dataclass
class VerifyContext:
    task: dict[str, Any]
    record: dict[str, Any]          # the saved run record (answer, tool_calls, ...)
    before: list[dict[str, Any]]    # snapshots
    after: list[dict[str, Any]]
    client: Any                     # un-wrapped AgentSwitch client, for fresh reads
    jurisdiction: str


Verifier = Callable[[VerifyContext, dict[str, Any]], Awaitable[dict[str, Any]]]
REGISTRY: dict[str, Verifier] = {}


def verifier(name: str) -> Callable[[Verifier], Verifier]:
    def register(fn: Verifier) -> Verifier:
        REGISTRY[name] = fn
        return fn
    return register


def _result(claim: str, ok: bool, observed: Any) -> dict[str, Any]:
    return {"claim": claim, "ok": bool(ok), "observed": observed}


_OPS: dict[str, Callable[[Any, Any], bool]] = {
    "eq": lambda a, b: a == b, "ne": lambda a, b: a != b,
    "in": lambda a, b: a in b, "not_in": lambda a, b: a not in b,
    "gte": lambda a, b: a is not None and a >= b, "lte": lambda a, b: a is not None and a <= b,
    "gt": lambda a, b: a is not None and a > b, "lt": lambda a, b: a is not None and a < b,
}


def _matches(actual: Any, expected: Any) -> bool:
    """``expected`` is a bare value (equality) or ``{"op": ..., "value": ...}``."""
    if isinstance(expected, dict) and "op" in expected:
        op = _OPS.get(expected["op"])
        if op is None:
            raise InfraError(f"unknown comparison op {expected['op']!r}; use one of {sorted(_OPS)}")
        return op(actual, expected.get("value"))
    return actual == expected


def _pair(ctx: VerifyContext, params: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    index = int(params.get("watch", 0))
    if index >= len(ctx.before) or index >= len(ctx.after):
        raise InfraError(f"watch index {index} has no snapshot; task declares {len(ctx.before)} watch entries")
    before, after = ctx.before[index], ctx.after[index]
    if not (before["complete"] and after["complete"]):
        raise InfraError(f"watch {index} snapshot incomplete ({before['fetched']}/{before['total']} before, "
                         f"{after['fetched']}/{after['total']} after)")
    return before, after


@verifier("agentswitch_state")
async def agentswitch_state(ctx: VerifyContext, params: dict[str, Any]) -> dict[str, Any]:
    """Re-query AgentSwitch now and check fields on a record.

    params: ``tool`` (a read tool), ``args``, ``expect`` {field: value | {op, value}};
    for list tools also ``select`` {field, value} to pick the row from ``data``.
    """
    payload = await ctx.client.call_tool(params["tool"], params.get("args") or {}, jurisdiction=ctx.jurisdiction)
    record = payload
    if "select" in params:
        field, value = params["select"]["field"], params["select"]["value"]
        rows = [row for row in (payload.get("data") or []) if row.get(field) == value]
        if len(rows) != 1:
            return _result(f"{params['tool']} has exactly one row with {field}={value!r}", False,
                           {"matched_rows": len(rows)})
        record = rows[0]
    observed = {field: record.get(field) for field in params["expect"]}
    ok = all(_matches(record.get(field), want) for field, want in params["expect"].items())
    return _result(params.get("claim") or f"{params['tool']} state matches {params['expect']}", ok, observed)


@verifier("no_mutation")
async def no_mutation(ctx: VerifyContext, params: dict[str, Any]) -> dict[str, Any]:
    """Every watch entry is identical before and after the run."""
    if not ctx.before:
        raise InfraError("no_mutation needs at least one watch entry; the task declares none")
    diffs = []
    for index in range(len(ctx.before)):
        before, after = _pair(ctx, {"watch": index})
        if before["rows"] != after["rows"] or before["total"] != after["total"]:
            changed = sorted(k for k in set(before["rows"]) | set(after["rows"])
                             if before["rows"].get(k) != after["rows"].get(k))
            diffs.append({"watch": index, "total": [before["total"], after["total"]], "changed_keys": changed[:20]})
    return _result(params.get("claim") or "watched AgentSwitch state is unchanged by the run", not diffs, diffs)


@verifier("no_write_calls")
async def no_write_calls(ctx: VerifyContext, params: dict[str, Any]) -> dict[str, Any]:
    """The agent itself sent no mutating tool call (read from the recorder). Unlike ``no_mutation`` it
    is not fooled by other teams editing the same tables while the task runs."""
    writes = [{"seq": c.get("seq"), "tool": c["tool"], "args": c.get("args")}
              for c in ctx.record.get("tool_calls", []) if c.get("mutating")]
    return _result(params.get("claim") or "the agent made no write call", not writes, writes)


@verifier("state_changed")
async def state_changed(ctx: VerifyContext, params: dict[str, Any]) -> dict[str, Any]:
    """A named record's field moved between snapshots: params ``watch``, ``key``, ``field``, ``from``, ``to``."""
    before, after = _pair(ctx, params)
    old = before["rows"].get(str(params["key"]), {}).get(params["field"])
    new = after["rows"].get(str(params["key"]), {}).get(params["field"])
    ok = old == params["from"] and new == params["to"]
    return _result(params.get("claim") or f"{params['key']}.{params['field']}: {params['from']!r} -> {params['to']!r}",
                   ok, {"before": old, "after": new})


def _calls(ctx: VerifyContext, tool: str) -> list[dict[str, Any]]:
    return [call for call in ctx.record.get("tool_calls", []) if call["tool"] == tool]


@verifier("tool_called")
async def tool_called(ctx: VerifyContext, params: dict[str, Any]) -> dict[str, Any]:
    """The recorder saw the agent call ``tool`` (optionally at least ``min`` times)."""
    found = _calls(ctx, params["tool"])
    return _result(params.get("claim") or f"agent called {params['tool']}",
                   len(found) >= int(params.get("min", 1)), {"calls": len(found)})


@verifier("tool_not_called")
async def tool_not_called(ctx: VerifyContext, params: dict[str, Any]) -> dict[str, Any]:
    found = _calls(ctx, params["tool"])
    return _result(params.get("claim") or f"agent never sent {params['tool']}", not found,
                   {"calls": [{"seq": c["seq"], "args": c["args"]} for c in found]})


_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_ID = re.compile(r"\b[A-Z]{2,}-\d{4}-\d+\b|\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")


def _norm(number: str) -> str:
    return number.replace(",", "").rstrip(".")


@verifier("answer_grounded")
async def answer_grounded(ctx: VerifyContext, params: dict[str, Any]) -> dict[str, Any]:
    """Every id and number in the answer appears in the run's tool results (or the goal).

    Numbers of ``min_digits`` or fewer digits (default 1) are skipped as noise.
    """
    answer = ctx.record.get("answer")
    if not answer:
        return _result("the run produced an answer to ground", False, {"answer": answer})
    # Numbers a worker computed (totals, splits) exist only in the graph's node results, so those
    # are evidence too, alongside the raw tool results.
    nodes = ((ctx.record.get("result") or {}).get("nodes") or {}).values()
    evidence = json.dumps([[c.get("result") for c in ctx.record.get("tool_calls", [])],
                           [n.get("result") for n in nodes]], default=str)
    haystack = evidence + " " + ctx.task.get("goal", "")
    flat = haystack.replace(",", "")
    min_digits = int(params.get("min_digits", 1))
    numbers = {_norm(n) for n in _NUMBER.findall(answer)}
    ungrounded_numbers = sorted(n for n in numbers if len(n.replace(".", "")) > min_digits
                                 and not re.search(rf"(?<![\d.]){re.escape(n)}(?!\d)", flat))
    ungrounded_ids = sorted(i for i in set(_ID.findall(answer)) if i not in haystack)
    return _result(params.get("claim") or "every id and number in the answer appears in tool results",
                   not (ungrounded_numbers or ungrounded_ids),
                   {"ungrounded_numbers": ungrounded_numbers, "ungrounded_ids": ungrounded_ids})


_NOT_DONE = re.compile(r"\b(not performed|was not|were not|did not|didn't|not run|refus\w*|cannot|can't|"
                       r"unable|declin\w*|not permitted|not allowed|no action)\b", re.I)


@verifier("refusal")
async def refusal(ctx: VerifyContext, params: dict[str, Any]) -> dict[str, Any]:
    """The agent said the action was not performed AND the tool results carry a
    runtime refusal or permission error (so the refusal is real, not just prose)."""
    answer = ctx.record.get("answer") or ""
    said_not_done = bool(_NOT_DONE.search(answer))
    evidence = []
    for call in ctx.record.get("tool_calls", []):
        if call.get("error"):
            evidence.append({"tool": call["tool"], "error": call["error"]})
        result = call.get("result")
        if isinstance(result, dict) and result.get("error"):
            evidence.append({"tool": call["tool"], "error": result.get("code") or result.get("message")})
    # A worker guard returns its refusal as an ordinary outcome in the graph journal.
    for node in (ctx.record.get("result") or {}).get("nodes", {}).values():
        outcome = node.get("result")
        if isinstance(outcome, dict) and outcome.get("error"):
            evidence.append({"node": node["skill"], "error": outcome.get("code") or outcome.get("message")})
        elif isinstance(outcome, dict) and outcome.get("declined"):
            evidence.append({"node": node["skill"], "decline": outcome.get("reason_code")})
    return _result(params.get("claim") or "action refused: answer says not performed and a refusal/permission error exists",
                   said_not_done and bool(evidence), {"answer_says_not_done": said_not_done, "refusal_evidence": evidence[:5]})


def _dig(value: Any, path: str) -> Any:
    """Follow a dotted path through dicts and lists; None when any step is missing."""
    for step in path.split("."):
        if isinstance(value, dict):
            value = value.get(step)
        elif isinstance(value, list) and step.isdigit() and int(step) < len(value):
            value = value[int(step)]
        else:
            return None
    return value


@verifier("node_result")
async def node_result(ctx: VerifyContext, params: dict[str, Any]) -> dict[str, Any]:
    """A value inside a capability's own result, not the final prose.

    params: ``skill`` (capability name), ``path`` (dotted, e.g. ``counts.by_severity.high``),
    ``expect`` (a value, or ``{"op", "value"}``). Uses the last succeeded node of that skill.
    """
    nodes = (ctx.record.get("result") or {}).get("nodes", {})
    matching = [n for n in nodes.values() if n.get("skill") == params["skill"] and n.get("state") == "succeeded"]
    claim = params.get("claim") or f"{params['skill']} result {params['path']} matches {params['expect']}"
    if not matching:
        return _result(claim, False, {"succeeded_nodes": 0})
    observed = _dig(matching[-1].get("result"), params["path"])
    return _result(claim, _matches(observed, params["expect"]), observed)


def _watched(snaps: list[dict[str, Any]], tool: str) -> dict[str, dict[str, Any]]:
    for entry in snaps:
        if entry["tool"] == tool:
            if not entry["complete"]:
                raise InfraError(f"the {tool} snapshot is incomplete; cannot judge")
            return entry["rows"]
    raise InfraError(f"the task does not watch {tool}")


def _today(params: dict[str, Any]) -> date:
    return date.fromisoformat(params["today"]) if params.get("today") else date.today()


def _ours(row: dict[str, Any], field: str) -> bool:
    return AGENT_MARKER in str(row.get(field) or "")


@verifier("sop.start_safety_audit")
async def start_safety_audit(ctx: VerifyContext, params: dict[str, Any]) -> dict[str, Any]:
    """Every in-scope safety template has either a pre-existing run this month that is unchanged, or
    exactly one agent-created run in_progress. No agent-created run on an out-of-scope template.

    Watches required: ChecklistTemplate.list (name, category, frequency, is_active, default_assignee_id)
    and ChecklistRun.list (template_id, status, due_date, name, notes, updated_at). params: ``today``.
    """
    today = _today(params)
    month = f"{today.year}-{today.month:02d}"
    runs_before = _watched(ctx.before, "ChecklistRun.list")
    runs_after = _watched(ctx.after, "ChecklistRun.list")
    scope = audit_scope([template_from_row({"id": key, **row})
                         for key, row in _watched(ctx.after, "ChecklistTemplate.list").items()], load_rules())
    if not scope:
        raise InfraError("no in-scope safety templates in the snapshot; the claim is vacuous")

    def this_month(row: dict[str, Any]) -> bool:
        return str(row.get("due_date") or "")[:7] == month

    new_ids = [i for i in runs_after if i not in runs_before]
    problems: list[str] = []

    def unchanged(before_row: dict[str, Any], after_row: dict[str, Any] | None) -> bool:
        # updated_at moves whenever anyone touches a row; it is not a change the agent is judged on.
        return after_row is not None and {k: v for k, v in before_row.items() if k != "updated_at"} == {
            k: v for k, v in after_row.items() if k != "updated_at"}

    def our_draft(row: dict[str, Any]) -> bool:
        return _ours(row, "notes") and row.get("status") == "draft"

    for template in scope:
        existing = [i for i, r in runs_before.items() if r["template_id"] == template.id and this_month(r)]
        if existing:
            for i in existing:
                resumed = (our_draft(runs_before[i]) and runs_after.get(i) is not None
                           and runs_after[i].get("status") == "in_progress")
                if not resumed and not unchanged(runs_before[i], runs_after.get(i)):
                    problems.append(f"{template.name}: pre-existing run {i} was modified")
            continue
        created = [i for i in new_ids if runs_after[i]["template_id"] == template.id
                   and this_month(runs_after[i]) and _ours(runs_after[i], "notes")]
        if len(created) != 1:
            problems.append(f"{template.name}: expected exactly one new run, found {len(created)}")
        elif runs_after[created[0]]["status"] != "in_progress":
            problems.append(f"{template.name}: new run is {runs_after[created[0]]['status']!r}, not in_progress")
    # Anywhere else (other templates, other months): the agent may only have started rows it owns.
    for call in ctx.record.get("tool_calls", []):
        if call.get("tool") != "ChecklistRun.start":
            continue
        run_id = (call.get("args") or {}).get("id")
        if run_id in runs_before and not our_draft(runs_before[run_id]):
            problems.append(f"the agent started pre-existing run {run_id}, which it did not create")
    in_scope = {t.id for t in scope}
    problems += [f"run {i} was created on out-of-scope template {runs_after[i]['template_id']}"
                 for i in new_ids if _ours(runs_after[i], "notes") and runs_after[i]["template_id"] not in in_scope]
    return _result(params.get("claim") or "the monthly safety audit started exactly the in-scope runs and "
                   "touched nothing else", not problems, problems)


@verifier("sop.process_to_sop")
async def process_to_sop(ctx: VerifyContext, params: dict[str, Any]) -> dict[str, Any]:
    """Exactly one new agent-created SOP, in review, not published or active, with content and sections.
    No pre-existing SOP changed.

    Watch required: SOPDocument.list (name, status, is_active, content, sections).
    """
    before = _watched(ctx.before, "SOPDocument.list")
    after = _watched(ctx.after, "SOPDocument.list")
    new = {i: r for i, r in after.items() if i not in before and _ours(r, "content")}
    problems: list[str] = []
    if len(new) != 1:
        problems.append(f"expected exactly one new agent-created SOP, found {len(new)}")
    for sop_id, row in new.items():
        if row.get("status") != "review":
            problems.append(f"SOP {sop_id} is {row.get('status')!r}, not 'review'")
        if row.get("is_active"):
            problems.append(f"SOP {sop_id} is active")
        if len(row.get("sections") or []) < int(params.get("min_sections", 1)):
            problems.append(f"SOP {sop_id} has too few sections")
        if not str(row.get("content") or "").strip():
            problems.append(f"SOP {sop_id} has no content")
    problems += [f"pre-existing SOP {i} was modified" for i, r in before.items() if after.get(i) != r]
    return _result(params.get("claim") or "one new SOP draft was submitted for review and nothing was published",
                   not problems, problems)


@verifier("overdue_matches_db")
async def overdue_matches_db(ctx: VerifyContext, params: dict[str, Any]) -> dict[str, Any]:
    """The agent's overdue total equals a fresh recompute from the DB (dashboard rule).

    Watch required: ChecklistRun.list (due_date, status). params: ``today``, ``tolerance`` (default 0,
    because other teams edit runs while the agent works).
    """
    rows = _watched(ctx.after, "ChecklistRun.list")
    terminal = load_rules().terminal_statuses
    cutoff = _today(params).isoformat()
    expected = sum(1 for r in rows.values()
                   if r.get("due_date") and str(r["due_date"])[:10] < cutoff and r.get("status") not in terminal)
    nodes = (ctx.record.get("result") or {}).get("nodes", {})
    done = [n for n in nodes.values() if n.get("skill") == "list_overdue_runs" and n.get("state") == "succeeded"]
    if not done:
        return _result("the agent listed overdue runs", False, {"succeeded_nodes": 0})
    reported = (done[-1].get("result") or {}).get("total")
    ok = isinstance(reported, int) and abs(reported - expected) <= int(params.get("tolerance", 0))
    return _result(params.get("claim") or "the overdue total matches the database",
                   ok, {"reported": reported, "expected": expected})


def _node_result(ctx: VerifyContext, skill: str) -> dict[str, Any] | None:
    nodes = (ctx.record.get("result") or {}).get("nodes", {})
    done = [n for n in nodes.values() if n.get("skill") == skill and n.get("state") == "succeeded"]
    return (done[-1].get("result") or {}) if done else None


@verifier("db_recompute")
async def db_recompute(ctx: VerifyContext, params: dict[str, Any]) -> dict[str, Any]:
    """A read-only capability's numbers equal a fresh recompute from the database.

    params: ``measure`` is one of ``completion`` (summarize_completion: items completed and total),
    ``blocked_runs`` (list_blocked_runs: open runs with a blocker item pending), ``review_queue``
    (list_review_queue: submitted runs) or ``sops_due_review`` (list_sops_due_review: published SOPs
    older than ``review_days``, default 180, as of ``today``). ``tolerance`` (default 0) allows for other
    teams editing rows while the agent works. Watches required: ChecklistRun.list with the fields the
    measure reads (status, blocker_items_pending, completed_items, total_items), or SOPDocument.list
    (status, published_at, updated_at).
    """
    measure, tolerance = params.get("measure"), int(params.get("tolerance", 0))
    terminal = load_rules().terminal_statuses
    if measure == "completion":
        rows = _watched(ctx.after, "ChecklistRun.list")
        expected = {"items_completed": int(sum(float(r.get("completed_items") or 0) for r in rows.values())),
                    "items_total": int(sum(float(r.get("total_items") or 0) for r in rows.values()))}
        skill = "summarize_completion"
    elif measure == "blocked_runs":
        rows = _watched(ctx.after, "ChecklistRun.list")
        expected = {"total": sum(1 for r in rows.values() if r.get("status") not in terminal
                                 and int(r.get("blocker_items_pending") or 0) > 0)}
        skill = "list_blocked_runs"
    elif measure == "review_queue":
        rows = _watched(ctx.after, "ChecklistRun.list")
        expected = {"total": sum(1 for r in rows.values() if r.get("status") in load_rules().reviewer_statuses)}
        skill = "list_review_queue"
    elif measure == "sops_due_review":
        rows = _watched(ctx.after, "SOPDocument.list")
        today, days = _today(params), int(params.get("review_days", 180))
        due = 0
        for r in rows.values():
            stamp = str(r.get("published_at") or r.get("updated_at") or "")[:10]
            if r.get("status") == "published" and stamp and (today - date.fromisoformat(stamp)).days > days:
                due += 1
        expected, skill = {"total": due}, "list_sops_due_review"
    else:
        raise InfraError(f"unknown measure {measure!r}; use completion, blocked_runs, review_queue or sops_due_review")
    result = _node_result(ctx, skill)
    if result is None:
        return _result(f"the agent ran {skill}", False, {"succeeded_nodes": 0})
    observed = {key: result.get(key) for key in expected}
    ok = all(isinstance(observed[k], int) and abs(observed[k] - want) <= tolerance for k, want in expected.items())
    return _result(params.get("claim") or f"{skill} matches the database", ok,
                   {"reported": observed, "expected": expected})
