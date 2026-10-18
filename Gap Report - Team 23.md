
Benchmark: Mitti (formerly SafetyCulture), with Tulip as the AI-native reference.

### 1.⁠ ⁠What do they do that we don't?
- Recurring checklists that schedule themselves; our frequency is only a label.
- AI checklist generation from a description, a photo, or an existing SOP.
- A direct link between an SOP and the checklists built from it.
- Full CAPA tracking: owner, due date, root cause, and an effectiveness check before closure.
- Structured 8D/RCA inside the checklist workflow.
- SOP read-and-acknowledge by employees, plus periodic SOP review reminders.
- Rich photo evidence (multiple photos, annotations) and offline mobile capture.
- Checklists linked to sites and assets.
- AI summaries of inspection reports and trend analytics across runs.
- Blocker items that actually stop a checklist from being submitted.

### 2.⁠ ⁠Which gaps can an agent close with the tools our seat already has?

Agent can close (orchestration):
- Auto-create recurring runs from active templates on schedule.
- Chase overdue and stuck runs, and nudge owners.
- Draft checklist templates from published SOPs, left inactive until a human reviews them.
- Open a CAPA finding on every rejected run.
- Schedule a follow-up verification run and flag the CAPA if the same item fails again.
- Check for pending blocker items before any submit.
- Summarize runs and highlight repeat failures.
- Flag misconfigured templates: wrong category, odd frequency, missing assignee.

Platform must fix (new fields or permissions):
- Schedule fields on templates.
- A link from SOP to template.
- Owner, due date and root cause on CAPA findings.
- SOP acknowledgement.
- Asset and site links on runs.
- Server-side enforcement of blocker items.
- Permission for this seat to create corrective-action tasks.

### 3.⁠ ⁠What can an agent do that their product can't?

- Hold a CAPA open for weeks and re-check effectiveness on its own.
- Re-read shared data before acting, since other teams are editing the same records.
- Escalate across seats to an EA, admin or human when it needs data it can't access, then resume.
- Audit configuration quality, not just count completed work.
- Adapt checklists to the business's locale, e.g. an India E-Way Bill check vs. a US equivalent.
- Prepare evidence and hand the final verification to an auditor, so it never approves its own work.


---

## Update 2026-10-18: Section 2 closed on the agent side

Every gap the report said an agent could close now has a capability. Each is idempotent, re-reads before it
writes, touches only rows it can recognise as its own, and previews unless the run is authorised.

| Gap | Capability | Live evidence (India tenant) |
|---|---|---|
| Auto-create recurring runs on schedule | `create_recurring_runs`, `schedule_recurring_work` | 59 recurring templates had no run this period; only 18 can run, 41 are blocked by malformed branch rules. Created 1 run (RUN-2026-00375). The schedule is recorded as 3 **paused** tasks, so a person activates them and the platform's runtime runs them. |
| Chase overdue and stuck runs | `raise_followups` | 205 overdue runs across 19 owners. Created 1 todo and 2 batched escalations to the one assignable person: 19 submitted runs have no reviewer, 90 overdue runs have no assignee. A second pass created nothing. |
| Draft templates from SOPs | `draft_template_from_sop` | SOP-2026-00101 became an inactive template with 5 items that mirror its 5 steps. Figures not in the SOP are rejected. |
| CAPA finding on every rejected run | `open_capa_findings` | The data has no rejected runs, so none were opened live; tested against a simulated platform. |
| Follow-up verification run, flag repeats | `open_capa_findings` | Same capability; a repeat is flagged when an earlier finding exists on the same item of the same template. |
| Check pending blockers before any submit | `submit_run` | Refused to submit a run with 6 required items pending, and refuses any run it did not create. |
| Summarize runs, highlight repeat failures | six reports and `list_repeat_findings` | Repeat failures are reported from the platform's CAPA trends, with the coverage it could not count. |
| Flag misconfigured templates | `audit_templates` | 72 of 100 flagged; the branch-rule check matches the platform's own reader (57 templates, 77 items). |

### What the platform still lacks (found while closing the gaps)

- **Review-rejection CAPA needs audit-log access this seat does not have.** `capa_trends` reports
  `capa_audit_read_denied`, so only unfinished required items on rejected runs can be raised.
- **No way to create a corrective Task.** A CAPA finding links to a `Task`, and this seat has only
  `AgentTask` and `AgentTodo`. This confirms the report's permission gap.
- **Submit is not blocked by pending required items.** The platform's own preview of
  `ChecklistRun.submit_for_review` says `allowed: true, blockers: []` for an in-progress run with 6 required
  items pending. Enforcement at commit time is untested, which is why the agent keeps its own guard.
- **Scheduling is not ours to run.** A scheduled task is run by the platform's agent runtime, not by this
  agent, so the schedule is recorded paused for a person to activate.
- **One escalation per session.** The platform hands a session to a person once, so the agent opens one
  session per escalation.
- Still open from the original list: schedule fields on templates, an SOP-to-template link, SOP
  acknowledgement, and asset and site links on runs.

### Section 3, current state

Done: re-reading shared data before acting; auditing configuration quality. Partly: preparing evidence for an
auditor (the agent never approves, and `custody_chain` is unused). Not started: holding a CAPA open for weeks,
adapting to locale, and resuming after an escalation is answered.

