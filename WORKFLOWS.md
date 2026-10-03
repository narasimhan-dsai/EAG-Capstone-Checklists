# Seat 23 workflows

The seat's request: "Start the monthly safety audit, tell me which checklists are overdue, and turn this
process description into an SOP."

| Request | Capability | AgentSwitch tools | Changes data | Predicate | Status |
|---|---|---|---|---|---|
| Preview the audit | `plan_safety_audit` | `ChecklistTemplate.list`, `ChecklistRun.list` | no | none | built, run live (read-only) |
| Start the monthly safety audit | `start_monthly_safety_audit` | `ChecklistTemplate.make.ChecklistRun`, `ChecklistRun.get`, `ChecklistRun.start` | yes | `sop.start_safety_audit` (harness verifier) | run live: 3 skipped, 4 refused by the platform (malformed template branch rules), 0 created |
| Which checklists are overdue | `list_overdue_runs` | `ChecklistRun.list` | no | `overdue_matches_db` (harness verifier) | built, run live (read-only) |
| Turn a process into an SOP | `process_to_sop` | `SOPDocument.list`, `SOPDocument.create`, `SOPDocument.submit_for_review`, `SOPDocument.get` | yes | `sop.process_to_sop` (harness verifier) | run live: SOP-2026-00101 created and in review |
| Publish an SOP, approve a run, delete a record | `decline_request` | none | no | `refusal` | built |

## Rules

- **Audit scope:** active templates with category `safety` and frequency `monthly`, `weekly` or `daily`.
  The rule lives in `checklist_agent/checklists/rules.yaml`.
- **Start means:** create this month's run (due at month end), then call `ChecklistRun.start`, so it ends
  `in_progress`. The agent never submits a run.
- **Existing runs:** a template that already has a run this month is left untouched and reported. The
  agent starts only runs it created itself, identified by `[checklist_agent]` in the run's `notes`.
- **Overdue:** `due_date` before today and status not `reviewed` (the dashboard rule), reported with a
  split by status and by who is blocking (owner for draft or in-progress, reviewer for submitted).
- **SOP:** the agent saves a draft and submits it for review. It cannot publish: this seat has no publish
  tool.
- **Dry run:** a capability that changes data is offered to the planner only when named with `--allow`.
  Without it the agent previews with `plan_safety_audit` and says what it would do.
