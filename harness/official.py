"""The official run: what the instructor's runner executes on its server, and the file it reads back.

The runner checks out this repo, installs it, and runs ``python -m harness.runner --official`` against a
throwaway copy of the instance, with the seat token and the platform's model in environment variables. It then
reads ``results.json``: ``{"tasks": [{"id", "title", "passed", "score", "evidence"}], "summary": "..."}``.

Everything here is about that file: mapping our task outcomes to it, never writing past a time budget,
and always leaving a valid file, even when a task crashes or nothing ran.
"""
from __future__ import annotations

import asyncio
import json
import tempfile
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

MAX_TASKS = 200
TITLE_CHARS = 120
EVIDENCE_CHARS = 500
INFRA_PREFIX = "infrastructure:"


def _cut(text: Any, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _failed(instance: str, task: dict[str, Any], evidence: str) -> dict[str, Any]:
    return {"id": f"{instance}:{task['id']}", "title": _cut(task.get("goal", task["id"]), TITLE_CHARS),
            "passed": False, "score": 0.0, "evidence": _cut(evidence, EVIDENCE_CHARS)}


def to_result_task(instance: str, task: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    """One of our task outcomes as an entry of the results file."""
    state = row["state"]
    total, ok = int(row.get("claims_total") or 0), int(row.get("claims_passed") or 0)
    if state == "pass":
        passed, score, evidence = True, 1.0, f"{ok}/{total} checks passed" if total else "passed"
    elif state == "infra_error":
        passed, score, evidence = False, 0.0, f"{INFRA_PREFIX} {row.get('reason', 'the run could not be judged')}"
    else:
        passed, score = False, (ok / total if total else 0.0)
        failed = _cut("; ".join(row.get("failed_details") or row.get("failed_claims") or ["see the run record"]), 300)
        evidence = f"{ok}/{total} checks passed; failed: {failed}"
        if row.get("diagnosis"):
            evidence += f"; cause: {row['diagnosis']}"
    if row.get("note"):
        evidence += f"; note: {row['note']}"
    return {"id": f"{instance}:{row['id']}", "title": _cut(task.get("goal", row["id"]), TITLE_CHARS),
            "passed": passed, "score": score, "evidence": _cut(evidence, EVIDENCE_CHARS)}


def build_results(instance: str, tasks: list[dict[str, Any]]) -> dict[str, Any]:
    if not tasks:
        tasks = [{"id": f"{instance}:no_tasks", "title": "The harness ran at least one task", "passed": False,
                  "score": 0.0, "evidence": "no task ran"}]
    passed = sum(1 for t in tasks if t["passed"])
    infra = sum(1 for t in tasks if t["evidence"].startswith(INFRA_PREFIX))
    summary = f"{instance}: {passed}/{len(tasks)} tasks passed"
    if infra:
        summary += f", {infra} infrastructure error" + ("s" if infra != 1 else "")
    return {"tasks": tasks, "summary": summary}


def validate_results(payload: dict[str, Any]) -> None:
    """The rules the platform applies when it reads the file; raises ValueError for any it would reject."""
    tasks = payload.get("tasks")
    if not isinstance(tasks, list) or not 1 <= len(tasks) <= MAX_TASKS:
        raise ValueError(f"tasks must be a list of 1 to {MAX_TASKS} entries")
    if not isinstance(payload.get("summary"), str) or not payload["summary"]:
        raise ValueError("summary must be a non-empty string")
    seen: set[str] = set()
    for task in tasks:
        if not isinstance(task, dict) or not isinstance(task.get("id"), str) or not isinstance(task.get("title"), str):
            raise ValueError("every task needs a string id and title")
        if task["id"] in seen:
            raise ValueError(f"duplicate task id {task['id']!r}")
        seen.add(task["id"])
        if not isinstance(task.get("passed"), bool):
            raise ValueError(f"{task['id']}: passed must be true or false")
        score = task.get("score")
        if score is not None and (isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= score <= 1):
            raise ValueError(f"{task['id']}: score must be between 0 and 1")


RunTask = Callable[[dict[str, Any], Any, Path], Awaitable[dict[str, Any]]]


async def run_official(tasks: list[dict[str, Any]], *, instance: str, armed: bool, budget_seconds: float,
                       out_path: Path | str, runner_args: Any, run_task: RunTask,
                       task_timeout: float = 300) -> dict[str, Any]:
    """Run the suite and write the results file. ``armed`` means this is the throwaway copy, so tasks that
    change data may run; otherwise (a local rehearsal) they are skipped and nothing is written."""
    deadline = time.monotonic() + budget_seconds
    run_dir = Path(tempfile.mkdtemp(prefix="official-run-"))
    results: list[dict[str, Any]] = []
    for task in tasks:
        if not armed and task.get("allow"):
            results.append(_failed(instance, task, "skipped: tasks that change data are not armed in a dry run"))
            continue
        if time.monotonic() >= deadline:
            results.append(_failed(instance, task, "not run: the time budget was used up"))
            continue
        try:
            row = await asyncio.wait_for(run_task(task, runner_args, run_dir), timeout=task_timeout)
        except asyncio.TimeoutError:
            results.append(_failed(instance, task, f"{INFRA_PREFIX} the task did not finish in {task_timeout:g} seconds"))
            continue
        except Exception as problem:  # one task crashing must not cost the others
            results.append(_failed(instance, task, f"crashed: {type(problem).__name__}: {problem}"))
            continue
        results.append(to_result_task(instance, task, row))
    payload = build_results(instance, results)
    validate_results(payload)
    Path(out_path).write_text(json.dumps(payload, indent=2))
    return payload
