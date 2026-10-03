"""Which templates the monthly safety audit covers, and what is overdue. Pure."""
from __future__ import annotations

from calendar import monthrange
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Any

from checklist_agent.checklists.models import Run, RunStatus, Template
from checklist_agent.checklists.rules import Rules, load_rules


def month_end(today: date) -> date:
    return today.replace(day=monthrange(today.year, today.month)[1])


def _in_month(due: date | None, today: date) -> bool:
    return due is not None and (due.year, due.month) == (today.year, today.month)


def audit_scope(templates: Iterable[Template], rules: Rules | None = None) -> list[Template]:
    rules = rules or load_rules()
    return [t for t in templates
            if t.active and t.category == rules.audit_category and t.frequency in rules.audit_frequencies]


@dataclass(frozen=True)
class AuditPlan:
    month: str
    in_scope: int
    create: tuple[Template, ...]
    resume: tuple[tuple[Template, str], ...]   # our own draft run: start it, do not recreate
    skip: tuple[tuple[Template, str], ...]     # someone else's run (or one already started): leave it
    needs_owner: tuple[str, ...]               # template ids that will have no assignee
    rule: str

    def to_dict(self) -> dict[str, Any]:
        def brief(t: Template) -> dict[str, Any]:
            return {"template_id": t.id, "name": t.name, "frequency": t.frequency,
                    "has_assignee": bool(t.assignee)}
        return {
            "month": self.month, "rule": self.rule, "in_scope": self.in_scope,
            "would_create": [brief(t) for t in self.create],
            "would_start_existing_draft": [{**brief(t), "run_id": rid} for t, rid in self.resume],
            "skip_existing": [{**brief(t), "run_id": rid} for t, rid in self.skip],
            "needs_owner": list(self.needs_owner),
        }


def plan_audit(templates: Iterable[Template], runs: Iterable[Run], today: date,
               rules: Rules | None = None) -> AuditPlan:
    rules = rules or load_rules()
    scope = audit_scope(templates, rules)
    this_month: dict[str, list[Run]] = {}
    for run in runs:
        if _in_month(run.due_date, today):
            this_month.setdefault(run.template_id, []).append(run)
    create: list[Template] = []
    resume: list[tuple[Template, str]] = []
    skip: list[tuple[Template, str]] = []
    for template in scope:
        existing = this_month.get(template.id, [])
        ours_draft = [r for r in existing if r.agent_created and r.status == RunStatus.DRAFT]
        if ours_draft:
            resume.append((template, ours_draft[0].id))
        elif existing:
            skip.append((template, existing[0].id))
        else:
            create.append(template)
    owners_needed = [t for t in (*create, *(t for t, _ in resume)) if not t.assignee]
    rule = (f"active {rules.audit_category} templates with frequency in "
            f"{sorted(rules.audit_frequencies)}")
    return AuditPlan(f"{today.year}-{today.month:02d}", len(scope), tuple(create), tuple(resume),
                     tuple(skip), tuple(t.id for t in owners_needed), rule)


@dataclass(frozen=True)
class OverdueReport:
    rule: str
    as_of: date
    total: int
    by_status: dict[str, int]
    blocked_on: dict[str, int]
    unassigned: int
    oldest: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {"rule": self.rule, "as_of": self.as_of.isoformat(), "total": self.total,
                "by_status": self.by_status, "blocked_on": self.blocked_on,
                "unassigned": self.unassigned, "oldest": list(self.oldest)}


def overdue(runs: Iterable[Run], today: date, rules: Rules | None = None, oldest: int = 25) -> OverdueReport:
    rules = rules or load_rules()
    late = [r for r in runs
            if r.due_date is not None and r.due_date < today and str(r.status) not in rules.terminal_statuses]
    late.sort(key=lambda r: (r.due_date, r.id))
    by_status: dict[str, int] = {}
    blocked = {"owner": 0, "reviewer": 0, "other": 0}
    rows = []
    for r in late:
        status = str(r.status)
        by_status[status] = by_status.get(status, 0) + 1
        who = ("owner" if status in rules.owner_statuses
               else "reviewer" if status in rules.reviewer_statuses else "other")
        blocked[who] += 1
        rows.append({"id": r.id, "name": r.name, "due_date": r.due_date.isoformat(), "status": status,
                     "days_late": (today - r.due_date).days, "blocked_on": who, "assigned": bool(r.owner)})
    rule = f"due_date before today and status not in {sorted(rules.terminal_statuses)}"
    return OverdueReport(rule, today, len(late), by_status, blocked,
                         sum(1 for r in late if not r.owner), tuple(rows[:oldest]))
