"""CLI runner: drive one checklist goal through the live graph, end to end.

Run from the repo root:

    .venv/bin/python -m checklist_agent.run "Which checklists are overdue?"
    .venv/bin/python -m checklist_agent.run \\
        "Start the monthly safety audit, tell me which checklists are overdue, and turn this process \\
description into an SOP: <description>" \\
        --allow start_monthly_safety_audit --allow process_to_sop

Nothing that changes data is authorised by default. Without ``--allow`` the agent
can only read and preview. Changes to the real tenant are permanent: this seat
has no delete.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
import uuid
from collections.abc import Callable
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

from .agentswitch import AgentSwitchClient  # noqa: E402
from .capabilities import default_registry  # noqa: E402
from .core.live_graph import GraphStore, LiveGraphExecutor  # noqa: E402
from .gateway import GatewayClient  # noqa: E402
from .planner import ChecklistPlanner  # noqa: E402
from .workers import RunContext, build_skills  # noqa: E402


async def run_goal(
    goal: str,
    *,
    allowed_side_effects: set[str] | None = None,
    initial_evidence: dict | None = None,
    data_dir: Path | None = None,
    llm=None,
    agentswitch=None,
    clock: Callable[[], date] | None = None,
) -> dict:
    """``llm`` replaces the gateway (an object with ``complete`` and ``close``);
    ``agentswitch`` replaces the client, e.g. with a recording wrapper; ``clock``
    replaces today's date. All default to the real ones."""
    data_dir = data_dir or Path(tempfile.mkdtemp(prefix="checklist-run-"))
    store = GraphStore(data_dir / "graphs")
    gateway = llm or GatewayClient()
    agentswitch = agentswitch or AgentSwitchClient()
    run_id = str(uuid.uuid4())

    ctx = RunContext(run_id=run_id, store=store, llm=gateway.complete, agentswitch=agentswitch, goal=goal,
                     **({"clock": clock} if clock else {}))
    registry = default_registry()
    planner = ChecklistPlanner(
        gateway.complete, registry, goal=goal, respond_as="text",
        allowed_side_effects=allowed_side_effects or set(),
        initial_evidence=initial_evidence or {},
    )
    executor = LiveGraphExecutor(store, planner, build_skills(ctx), max_workers=3)
    try:
        report = await executor.run(run_id)
    finally:
        await gateway.close()
        await agentswitch.close()

    snapshot = store.snapshot(run_id)
    patch_events = [
        {"sequence": e.sequence, "kind": e.kind, "reason": e.payload.get("reason"), "payload": e.payload}
        for e in store.events(run_id) if e.kind in {"graph_patched", "task_failed"}
    ]
    terminal = registry.terminal_skills("text")
    answer_node = next(
        (n for n in snapshot.nodes.values() if n["skill"] in terminal and n["state"] == "succeeded"), None)
    outcome = (answer_node.get("result") or {}) if answer_node else {}
    return {
        "run_id": run_id, "data_dir": str(data_dir), "finished": report.finished,
        "executed": report.executed, "waiting": report.waiting, "patch_events": patch_events,
        "answer": outcome.get("text") if answer_node else None,
        "declined": bool(outcome.get("declined")),
        "decline": outcome if outcome.get("declined") else None,
        "nodes": {node_id: {"skill": n["skill"], "state": n["state"], "input": n["input"],
                            "result": n.get("result")} for node_id, n in snapshot.nodes.items()},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one checklist goal through the live graph.")
    parser.add_argument("goal", help="the natural-language request")
    parser.add_argument("--allow", action="append", default=[],
                        help="a capability that changes data to authorise, e.g. --allow "
                             "start_monthly_safety_audit (repeatable). Nothing is authorised by default.")
    args = parser.parse_args()
    result = asyncio.run(run_goal(args.goal, allowed_side_effects=set(args.allow),
                                  initial_evidence={"jurisdiction": "IN"}))
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
