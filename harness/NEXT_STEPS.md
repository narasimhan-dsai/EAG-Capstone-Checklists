# Next steps (as of 2026-10-03)

## Built

- Agent: `plan_safety_audit`, `list_overdue_runs`, `start_monthly_safety_audit`, `process_to_sop`,
  `answer_with_evidence`, `decline_request`, driven by `ChecklistPlanner` over the live-graph runtime.
- Harness (`evals/`): recorder, snapshots, scoring, scripted and paced LLM transport, and the verifiers
  `sop.start_safety_audit`, `sop.process_to_sop`, `overdue_matches_db`, `no_write_calls`, plus generic ones.
- 92 scaffold tests pass. They are Claude-written scaffolding and score nothing.

## Verified live (India tenant)

- **Read-only.** The three scaffold eval tasks (scripted LLM, real tenant) pass: the overdue total is 119,
  equal to the database recompute and the dashboard (97 draft, 19 submitted, 3 in progress; 100 blocked on
  the owner, 19 on the reviewer).
- **`process_to_sop` wrote one SOP.** `SOP-2026-00101` "Daily Machine Start-Up Safety Check": category
  safety, status `review`, inactive, not published, sections Purpose, Scope and Steps, carrying the agent's
  marker. The SOP total went from 100 to 101. A retry with a different LLM title finds this document by a
  hash of the description and does not create a second one.
- **`start_monthly_safety_audit` created nothing.** Of 7 in-scope templates, 3 already had a run this
  month and were left untouched (First article inspection, PPE Compliance Round, Machine Safety
  Inspection). For the other 4 (5S audit Press Shop, Machine safety checklist Tool Room, Shift start
  checklist Plant Office, Fire safety walk Accounts) the platform refused to create the run: the template's
  branch rules are malformed (see the first bug candidate). The run count stayed at 304. The agent reported
  each refusal with the platform's reason instead of working around it.

## Not verified

- The final answer step is flaky when the gateway's Gemini keys are rate-limited (`503`, RPM quota). The
  agent stops visibly. A run uses about five LLM calls. Set
  `CHECKLIST_GATEWAY_FALLBACK_PROVIDERS=nvidia,groq` (both exist on the gateway) in `.env`.
- The audit's create-then-start path has never completed live, because every create was refused. The
  re-read and start logic is covered only by the fake-platform scaffold tests.
- The `rejected` run status has not been seen in the data; the overdue rule treats it as owner-blocked.

## Next, in order

1. The team hand-writes the graded eval tasks (`"authored_by":"team"`): a live audit, a live SOP,
   refusal tasks. Claude-written tests score zero.
2. Report the bug candidates below. Each needs a clean reproduction first.
3. Decide how the audit should treat templates the platform will not run (see Decisions open).
4. After the push, register the repo on the platform with `TeamHarness.set_mine`.
5. Wire `checklists/audit.py` and `checklists/capa.py` into capabilities (the misconfiguration audit and
   CAPA-on-rejection). The branch-rule finding below is exactly what that audit should flag.

## Decisions open

- 4 of the 7 in-scope safety templates cannot produce a run today. Options: leave them reported as
  failed (current behaviour), or have the agent raise an escalation for a human to fix the template under
  Checklists > Templates > Branching. The agent must not bypass the guard with `ChecklistRun.create`.

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

## Known issue

`checklist_agent/ui/compose.py` imports `checklist_agent.workers.context`, which does not exist. It fails
if imported.
