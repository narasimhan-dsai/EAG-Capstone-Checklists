"""Evaluation runner: load tasks, run the real agent, verify against AgentSwitch.

    .venv/bin/python -m harness.runner                      # every task, dry-run
    .venv/bin/python -m harness.runner --family refusal
    .venv/bin/python -m harness.runner --task-id <id> --live   # arm a mutating task

Per task: preflight -> snapshot before -> run the agent with a recording
AgentSwitch client -> snapshot after -> write the raw record to
``runs/<timestamp>/<id>.json`` -> only then run verifiers and write
``<id>.score.json``. Exit code: 0 all pass, 1 any fail, 2 infra errors only.
"""
from __future__ import annotations

import argparse
import asyncio
import glob
import json
import os
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from checklist_agent.agentswitch import AgentSwitchClient
from checklist_agent.gateway import GatewayClient
from checklist_agent.run import run_goal

from . import snapshot
from .recorder import RecordingClient
from .scoring import INFRA, exit_code, score
from .transport import PacedLLM, ScriptedLLM
from .verifiers import REGISTRY, InfraError, VerifyContext

EVALS = Path(__file__).resolve().parent
FAMILIES = {"audit", "overdue", "sop", "refusal"}
REQUIRED = ("id", "family", "goal", "jurisdiction", "authored_by", "transport", "verifiers")


class TaskFileError(ValueError):
    pass


def load_tasks(pattern: str) -> list[dict[str, Any]]:
    tasks, seen = [], set()
    paths = sorted(glob.glob(pattern))
    if not paths:
        raise TaskFileError(f"no task files match {pattern!r}")
    for path in paths:
        for number, line in enumerate(Path(path).read_text().splitlines(), 1):
            if not line.strip():
                continue
            where = f"{path}:{number}"
            try:
                task = json.loads(line)
            except json.JSONDecodeError as problem:
                raise TaskFileError(f"{where}: invalid JSON ({problem})") from problem
            _validate(task, where)
            if task["id"] in seen:
                raise TaskFileError(f"{where}: duplicate task id {task['id']!r}")
            seen.add(task["id"])
            tasks.append(task)
    return tasks


def _validate(task: dict[str, Any], where: str) -> None:
    missing = [field for field in REQUIRED if field not in task]
    if missing:
        raise TaskFileError(f"{where}: missing fields {missing}")
    if task["family"] not in FAMILIES:
        raise TaskFileError(f"{where}: family must be one of {sorted(FAMILIES)}")
    if task["jurisdiction"] not in {"IN", "US"}:
        raise TaskFileError(f"{where}: jurisdiction must be IN or US")
    if task["authored_by"] not in {"team", "scaffold"}:
        raise TaskFileError(f"{where}: authored_by must be 'team' or 'scaffold'")
    if task["transport"] not in {"live", "scripted"}:
        raise TaskFileError(f"{where}: transport must be 'live' or 'scripted'")
    if task["transport"] == "scripted" and not task.get("script"):
        raise TaskFileError(f"{where}: scripted transport needs a 'script' path")
    if task.get("mode", "dry_run") not in {"dry_run", "live"}:
        raise TaskFileError(f"{where}: mode must be 'dry_run' or 'live'")
    for verifier_spec in task["verifiers"]:
        if verifier_spec.get("type") not in REGISTRY:
            raise TaskFileError(f"{where}: unknown verifier type {verifier_spec.get('type')!r}; "
                                f"known: {sorted(REGISTRY)}")


def _prior_live_record(runs_dir: Path, task_id: str) -> Path | None:
    for path in sorted(runs_dir.glob(f"*/{task_id}.json")):
        try:
            if json.loads(path.read_text()).get("mode") == "live":
                return path
        except (OSError, json.JSONDecodeError):
            continue
    return None


def _write(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, default=str))


async def _preflight(task: dict[str, Any], llm: Any, agentswitch: AgentSwitchClient) -> str | None:
    if task["transport"] == "live":
        try:
            await llm.health()
        except Exception as problem:
            return f"gateway unreachable: {type(problem).__name__}: {problem}"
    if not agentswitch.configured(task["jurisdiction"]):
        return f"AgentSwitch {task['jurisdiction']} credentials not configured"
    try:
        await agentswitch.list_tools(jurisdiction=task["jurisdiction"])
    except Exception as problem:
        return f"AgentSwitch {task['jurisdiction']} login/tools failed: {type(problem).__name__}: {problem}"
    return None


def _make_llm(task: dict[str, Any], args: argparse.Namespace) -> Any:
    if task["transport"] == "scripted":
        return ScriptedLLM.from_file(EVALS / task["script"])
    return PacedLLM(GatewayClient(), min_interval=args.min_interval)


async def run_task(task: dict[str, Any], args: argparse.Namespace, run_dir: Path) -> dict[str, Any]:
    armed = task.get("mode", "dry_run") == "live" and args.live
    allow = list(task.get("allow") or [])
    effective_allow = allow if armed else []
    mode = "live" if (allow and armed) else "dry_run"
    record: dict[str, Any] = {
        "task": task, "mode": mode, "allow_effective": effective_allow,
        "transport": task["transport"], "model": os.getenv("CHECKLIST_GATEWAY_MODEL"),
        "started": datetime.now(timezone.utc).isoformat(), "exception": None,
        "preflight_error": None, "result": None, "tool_calls": [], "snapshots": {},
    }
    jurisdiction, watch = task["jurisdiction"], task.get("watch") or []
    inner, llm = AgentSwitchClient(), _make_llm(task, args)
    recorder = RecordingClient(inner)
    try:
        record["preflight_error"] = await _preflight(task, llm, inner)
        if not record["preflight_error"]:
            try:
                record["snapshots"]["before"] = await snapshot.capture(inner, jurisdiction, watch)
            except Exception as problem:
                record["preflight_error"] = f"snapshot before failed: {type(problem).__name__}: {problem}"
        if not record["preflight_error"]:
            try:
                record["result"] = await run_goal(
                    task["goal"], allowed_side_effects=set(effective_allow),
                    initial_evidence={"jurisdiction": jurisdiction}, llm=llm, agentswitch=recorder)
            except Exception as problem:
                record["exception"] = {"type": type(problem).__name__, "message": str(problem),
                                       "trace": traceback.format_exc()[-2000:]}
            try:
                record["snapshots"]["after"] = await snapshot.capture(inner, jurisdiction, watch)
            except Exception as problem:
                record["snapshots"]["after_error"] = f"{type(problem).__name__}: {problem}"
        record["tool_calls"] = recorder.calls
        if isinstance(llm, ScriptedLLM):
            record["llm_calls"] = [{"index": c["index"], "fault": c.get("fault")} for c in llm.calls]
        record["finished"] = datetime.now(timezone.utc).isoformat()
        _write(run_dir / f"{task['id']}.json", record)          # raw record first, before any scoring

        results = await _verify(task, record, inner)
        outcome = score(record, results)
    finally:
        await llm.close()
        await inner.close()
    _write(run_dir / f"{task['id']}.score.json", {"task_id": task["id"], "authored_by": task["authored_by"],
                                                   "verifier_results": results, **outcome})
    return {"id": task["id"], "family": task["family"], "authored_by": task["authored_by"], "mode": mode, **outcome}


async def _verify(task: dict[str, Any], record: dict[str, Any], client: Any) -> list[dict[str, Any]]:
    snaps = record["snapshots"]
    if record["preflight_error"] or "before" not in snaps or "after" not in snaps:
        return []
    ctx = VerifyContext(task=task, record={**record, "answer": (record["result"] or {}).get("answer")},
                        before=snaps["before"], after=snaps["after"], client=client,
                        jurisdiction=task["jurisdiction"])
    results = []
    for spec in task["verifiers"]:
        params = {k: v for k, v in spec.items() if k != "type"}
        try:
            results.append(await REGISTRY[spec["type"]](ctx, params))
        except InfraError as problem:
            results.append({"claim": spec["type"], "infra_error": str(problem)})
        except Exception as problem:
            results.append({"claim": spec["type"], "infra_error": f"verifier raised {type(problem).__name__}: {problem}"})
    return results


def _print_table(rows: list[dict[str, Any]]) -> None:
    width = max([len(r["id"]) for r in rows] + [4])
    print(f"\n{'task'.ljust(width)}  {'family':9} {'by':8} {'mode':8} result")
    for r in rows:
        detail = r.get("reason") or "; ".join(r.get("failed_claims", []))
        print(f"{r['id'].ljust(width)}  {r['family']:9} {r['authored_by']:8} {r['mode']:8} "
              f"{r['state']}{'  ' + detail[:100] if detail else ''}")


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def counts(subset: list[dict[str, Any]]) -> dict[str, int]:
        return {s: sum(1 for r in subset if r["state"] == s) for s in ("pass", "fail", INFRA)}
    return {"all": counts(rows), "graded": counts([r for r in rows if r["authored_by"] == "team"]),
            "scaffold": counts([r for r in rows if r["authored_by"] == "scaffold"]),
            "failed": [r["id"] for r in rows if r["state"] == "fail"]}


async def main_async(args: argparse.Namespace) -> int:
    tasks = load_tasks(args.tasks)
    if args.family:
        tasks = [t for t in tasks if t["family"] == args.family]
    if args.task_id:
        tasks = [t for t in tasks if t["id"] == args.task_id]
    runs_dir = Path(args.runs_dir)
    run_dir = runs_dir / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for index, task in enumerate(tasks):
        armed_live = task.get("allow") and task.get("mode") == "live" and args.live
        if armed_live and not args.force_rerun and (prior := _prior_live_record(runs_dir, task["id"])):
            print(f"REFUSING to re-run one-shot live task {task['id']!r}: already ran ({prior}). "
                  "Use --force-rerun to override.")
            rows.append({"id": task["id"], "family": task["family"], "authored_by": task["authored_by"],
                         "mode": "live", "state": INFRA, "reason": "one-shot live task already ran"})
            continue
        if index and args.cooldown:
            await asyncio.sleep(args.cooldown)
        print(f"running {task['id']} ({task['transport']}, {task.get('mode', 'dry_run')})...")
        rows.append(await run_task(task, args, run_dir))
    summary = _summarize(rows)
    _write(run_dir / "summary.json", {"rows": rows, **summary})
    _print_table(rows)
    print(f"\ngraded {summary['graded']}  scaffold {summary['scaffold']}  records: {run_dir}")
    return exit_code([r["state"] for r in rows])


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the checklist agent evaluation harness.")
    parser.add_argument("--tasks", default=str(EVALS / "tasks" / "*.jsonl"), help="glob of task files")
    parser.add_argument("--family", choices=sorted(FAMILIES))
    parser.add_argument("--task-id")
    parser.add_argument("--live", action="store_true",
                        help="arm tasks whose mode is 'live' to use their mutating capabilities")
    parser.add_argument("--force-rerun", action="store_true", help="re-run a one-shot live task that already ran")
    parser.add_argument("--min-interval", type=float, default=2.0, help="min seconds between live LLM calls")
    parser.add_argument("--cooldown", type=float, default=0.0, help="seconds to wait between tasks")
    parser.add_argument("--runs-dir", default=str(EVALS / "runs"))
    raise SystemExit(asyncio.run(main_async(parser.parse_args())))


if __name__ == "__main__":
    main()
