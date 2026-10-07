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
  checklists/        pure rules, models, audit scope, reports, overdue logic (rules.yaml holds the rules)
  agentswitch.py     MCP client (checks the JSON-RPC error envelope on every call)
  capabilities.py    the capability manifest the planner sees
  planner.py         capability-driven planner (validates before anything runs)
  workers.py         one guarded worker per capability
  verify.py          checks every model answer against the data it was given
  run.py             CLI: one goal through the live graph
  core/live_graph/   the durable task-graph executor the planner drives
  gateway.py         the seam to the LLM gateway (it owns the provider keys)
harness/             the evaluation harness: tasks, recorder, snapshots, verifiers, runner
tests/               scaffold tests for both packages
pyproject.toml  uv.lock  .env.example
Gap Report - Team 23.md
```

## Quickstart

```bash
uv sync
cp .env.example .env     # fill in the three AGENTSWITCH_IN_* values; the gateway is at GLC_BASE_URL
.venv/bin/python -m checklist_agent.run "Which checklists are overdue?"
.venv/bin/python -m harness.runner     # the eval tasks (the live ones need the LLM gateway)
```

**`--allow` and `--live` make permanent changes.** This seat has no delete, and rows it creates are
visible to every team. Without `--allow`, the agent can only read and preview:

```bash
.venv/bin/python -m checklist_agent.run "Start the monthly safety audit ..." \
    --allow start_monthly_safety_audit --allow process_to_sop
```

## What the agent can answer

| Request | Capability | AgentSwitch tools | Changes data | Checked by |
|---|---|---|---|---|
| Preview the audit | `plan_safety_audit` | `ChecklistTemplate.list`, `ChecklistRun.list` | no | |
| Start the monthly safety audit | `start_monthly_safety_audit` | `ChecklistTemplate.make.ChecklistRun`, `ChecklistRun.get`, `ChecklistRun.start` | yes | `sop.start_safety_audit` |
| Which checklists are overdue | `list_overdue_runs` | `ChecklistRun.list` | no | `overdue_matches_db` |
| Turn a process into an SOP | `process_to_sop` | `SOPDocument.list`, `.create`, `.submit_for_review`, `.get` | yes | `sop.process_to_sop` |
| Which recurring checklists missed their schedule | `find_schedule_gaps` | `ChecklistTemplate.list`, `ChecklistRun.list` | no | unit tests |
| Which runs are blocked by blocker items | `list_blocked_runs` | `ChecklistRun.list` | no | `db_recompute` |
| What is waiting on a reviewer | `list_review_queue` | `ChecklistRun.list` | no | `db_recompute` |
| How complete are our checklists | `summarize_completion` | `ChecklistRun.list` | no | `db_recompute` |
| Which templates are misconfigured | `audit_templates` | `ChecklistTemplate.list` | no | |
| Which SOPs are due for review | `list_sops_due_review` | `SOPDocument.list`, `ChecklistPreferences.list` | no | `db_recompute` |
| Publish an SOP, approve or reject a run, delete a record, read another team's data | `decline_request` | none | no | `refusal` |

The last row is a refusal. Re-checked 2026-10-07: this seat's tool list has no publish, approve, reject or
delete tool, and rows report `delete: false`, so the agent declines. Another team's data returns a 403.

### Rules

- **Audit scope:** active templates with category `safety` and frequency `monthly`, `weekly` or `daily`.
  The rule lives in `checklist_agent/checklists/rules.yaml`.
- **Start means:** create this month's run (due at month end), then call `ChecklistRun.start`, so it ends
  `in_progress`. The agent never submits a run.
- **Existing runs:** a template that already has a run this month is left untouched and reported. The
  agent starts only runs it created itself, identified by `[checklist_agent]` in the run's `notes`.
- **Overdue:** `due_date` before today and status not `reviewed` (the dashboard rule), reported with a
  split by status and by who is blocking (owner for draft or in-progress, reviewer for submitted).
- **SOP:** the agent saves a draft and submits it for review. It cannot publish.
- **Dry run:** a capability that changes data is offered to the planner only when named with `--allow`.
  Without it the agent previews with `plan_safety_audit` and says what it would do.

### Read-only reports

- **Schedule gaps:** an active daily, weekly, monthly, quarterly or yearly template has a gap for a
  period when no run of it is due in that period. Checked for the current period (not created yet) and
  the previous one (missed). One-off templates are not checked.
- **Blocked runs:** open runs (not `reviewed`) with at least one blocker item still pending.
- **Review queue:** `submitted` runs, grouped by reviewer. Waiting time counts from the run's last update.
- **Completion:** items completed over items total across all runs, the way the dashboard counts it.
- **Template audit:** missing or unknown category, odd frequency, active template with no assignee, no
  items, or a branch rule that names no item of the template. The branch-rule check is a heuristic: it
  flagged all 4 templates the platform refused and none of 11 templates known to be fine.
- **SOPs due for review:** published SOPs older than the review frequency from the checklist
  preferences (180 days if unreadable), counted from the publish date.

### Every model answer is verified

`checklist_agent/verify.py` checks the final answer against the evidence the agent gathered. A figure,
date or record id that is not in the evidence is rejected. The model gets one correction attempt naming the
problem, and if it still fails the user gets a plain rendering of the data, never an invented figure. The
SOP draft is held to the same rule against the user's description. Reformatted dates ("7 Oct 2026"),
typographic hyphens and timestamps are handled, and a rounded figure (67 for 66.7) is accepted. Names and
wording are not checked, only numbers, dates and ids.

## Status (2026-10-07)

**Built**
- The twelve capabilities above, driven by `ChecklistPlanner` over the live-graph runtime.
- The evaluation harness: recorder, snapshots, scoring, scripted and paced LLM transport, and the
  verifiers `sop.start_safety_audit`, `sop.process_to_sop`, `overdue_matches_db`, `db_recompute`,
  `no_write_calls`, plus generic ones.
- 123 scaffold tests pass. They are Claude-written scaffolding and score nothing.

**Verified live (India tenant)**
- **Overdue:** the total of 119 equals a database recompute and the dashboard (97 draft, 19 submitted, 3 in
  progress; 100 blocked on the owner, 19 on the reviewer).
- **`process_to_sop` wrote one SOP.** `SOP-2026-00101` "Daily Machine Start-Up Safety Check": category
  safety, status `review`, inactive, not published, with Purpose, Scope and Steps sections and the agent's
  marker. The SOP total went from 100 to 101. A retry with a different LLM title finds this document by a
  hash of the description and does not create a second one.
- **`start_monthly_safety_audit` created nothing.** Of 7 in-scope templates, 3 already had a run this
  month and were left untouched (First article inspection, PPE Compliance Round, Machine Safety
  Inspection). For the other 4 (5S audit Press Shop, Machine safety checklist Tool Room, Shift start
  checklist Plant Office, Fire safety walk Accounts) the platform refused to create the run because the
  template's branch rules are malformed. The run count stayed at 304. The agent reported each refusal with
  the platform's reason instead of working around it.
- **The six read-only reports** (324 runs, 101 SOPs, 100 templates):
  - Completion: 1,065 of 1,596 items (66.7%). The 1,065 equals the dashboard's figure from 2026-10-03; the
    total grew as 20 runs were added since.
  - Blocked runs: 93, all drafts. Review queue: 21 submitted runs, and only 2 have a reviewer assigned, so
    19 have nobody to review them.
  - Schedule gaps: 120 across 72 recurring templates. Template audit: 72 of 100 flagged, 77 branch-rule
    findings. SOPs due for review: 0 (all 34 published SOPs date from 2026-09-12).
- **Six real-LLM tasks passed independent verification** (gateway provider `nvidia`): overdue, review queue
  and blockers, completion, template audit, SOPs due for review, and a refusal. The harness recomputed
  every figure from the database and re-checked every number in the answer. All five written answers were
  the model's own words (attempt 1 or 2); none needed the data fallback.

**Not verified**
- The shared gateway is often rate-limited or timing out (Gemini upstream `503`s, `nvidia` bursts). The
  agent stops visibly and the harness scores it `infra_error`, not a failure. The 3B local model (`ollama`)
  cannot plan tool calls. A run uses about five LLM calls. Set
  `CHECKLIST_GATEWAY_FALLBACK_PROVIDERS=nvidia,groq` (both exist on the gateway) in `.env`.
- The audit's create-then-start path has never completed live, because every create was refused. The
  re-read and start logic is covered only by the fake-platform scaffold tests.
- The `rejected` run status has not been seen in the data; the overdue rule treats it as owner-blocked.

## Next steps

1. The team hand-writes the graded eval tasks (`"authored_by":"team"`): a live audit, a live SOP, refusal
   tasks. Claude-written tests score zero.
2. Report the bug candidates below. Each needs a clean reproduction first.
3. Decide how the audit should treat templates the platform will not run (see below).
4. Register the repo on the platform with `TeamHarness.set_mine`.
5. Wire `checklists/capa.py` into a capability (CAPA on rejected runs).

**Decision open:** 4 of the 7 in-scope safety templates cannot produce a run today. Options: leave them
reported as failed (current behaviour), or have the agent raise an escalation for a human to fix the
template under Checklists > Templates > Branching. The agent must not bypass the guard with
`ChecklistRun.create`.

## Bug candidates

- **Seeded templates carry malformed branch rules, so runs cannot be created from them.** Reproduce:
  `tools/call` `ChecklistTemplate.make.ChecklistRun` with `{"id": "bcdd014e-49a0-4689-b71d-56665543b831"}`
  ("Fire safety walk — Accounts"). Result: JSON-RPC error `-32602`, "This template has 2 branch rule(s) a
  run could not execute, so no run was created: item 1767 (Machinist Square 74mm (Set)): malformed_rule;
  item 298 (Vernier Caliper 346mm (Pair)): malformed_rule." The items' `branch_rule` values are item
  names (`"Lathe Dog 1391"`, `"Machinist Square 1391"`), not rules. A refused call writes nothing.
- `SOPDocument.approval_status` is null on the 100 seeded rows, although null is not an allowed value; a
  row created through the tool gets `not_required`.
- A draft SOP has `published_at` set (SOP-2026-00100 is `draft`, inactive, `published_at: 2026-07-27`).
- Boolean fields such as `is_active` and `passed` come back as `1` and `0` although the tool schemas
  declare them boolean.

## Tests

The tests in this repo written by Claude are implementation scaffolding and are marked as such. The
graded tests and eval tasks are written by the team, by hand.
