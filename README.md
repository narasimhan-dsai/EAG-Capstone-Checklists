# Team 23: Checklists and SOPs agent

An EAG capstone project. Team 23 owns the Checklists and SOPs seat on AgentSwitch, a shared
schema-driven business platform. The agent answers:

> "Start the monthly safety audit, tell me which checklists are overdue, and turn this process
> description into an SOP."

It runs on your machine against the real India (Suryodaya) tenant, over MCP, and is judged against the
database, not against how its reply reads.

## Layout

```
checklist_agent/     the agent
  checklists/        pure rules, models, audit scope, overdue logic (rules.yaml holds the rules)
  agentswitch.py     MCP client (checks the JSON-RPC error envelope on every call)
  capabilities.py    the capability manifest the planner sees
  planner.py         capability-driven planner (validates before anything runs)
  workers.py         one guarded worker per capability
  run.py             CLI: one goal through the live graph
  core/live_graph/   the durable task-graph executor the planner drives
  gateway.py         the seam to the LLM gateway (it owns the provider keys)
harness/             the evaluation harness: tasks, recorder, snapshots, verifiers, runner
tests/               scaffold tests for both packages
pyproject.toml  uv.lock  .env.example
WORKFLOWS.md         the requests the agent handles, by capability
NEXT_STEPS.md        current status and the next steps
docs/superpowers/    design spec and implementation plan
Gap Report - Team 23.md
```

## Quickstart

```bash
uv sync
cp .env.example .env     # fill in the three AGENTSWITCH_IN_* values; the gateway is at GLC_BASE_URL
.venv/bin/python -m checklist_agent.run "Which checklists are overdue?"
.venv/bin/python -m harness.runner     # the scaffold eval tasks, read-only
```

**`--allow` and `--live` make permanent changes.** This seat has no delete, and rows it creates are
visible to every team. Without `--allow`, the agent can only read and preview:

```bash
.venv/bin/python -m checklist_agent.run "Start the monthly safety audit ..." \
    --allow start_monthly_safety_audit --allow process_to_sop
```

## Tests

The tests in this repo written by Claude are implementation scaffolding and are marked as such. The
graded tests and eval tasks are written by the team, by hand.
