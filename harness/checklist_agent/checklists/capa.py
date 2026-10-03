"""Open a CAPA on every rejected run, and schedule a follow-up verification.

The agent prepares the CAPA and the follow-up; it never closes one. Root cause
is left empty for a human, and a repeat failure of the same item is flagged
rather than silently opened again.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta

from checklist_agent.checklists.models import Capa, Run, RunStatus, Template
from checklist_agent.checklists.rules import Rules, load_rules


@dataclass(frozen=True)
class CapaPlan:
    capas: tuple[Capa, ...]
    followup_template_id: str | None
    followup_due: date | None


def _failed_items(run: Run, template: Template) -> list[str]:
    results = {r.item_id: r for r in run.results}
    failed = [i.id for i in template.items if results.get(i.id) and results[i.id].passed is False]
    # A rejected run with no explicit failure still needs one CAPA, on the run itself.
    return failed or [""]


def plan_capa_for_rejection(
    run: Run,
    template: Template,
    *,
    today: date,
    existing: Iterable[Capa] = (),
    rules: Rules | None = None,
) -> CapaPlan:
    """Return the CAPAs to open for a rejected run. Non-rejected runs yield none."""
    rules = rules or load_rules()
    if run.status is not RunStatus.REJECTED:
        return CapaPlan((), None, None)

    prior = list(existing)
    capas = []
    for item_id in _failed_items(run, template):
        previous = [c for c in prior if c.item_id == item_id and c.run_id != run.id]
        if any(c.run_id == run.id and c.item_id == item_id for c in prior):
            continue  # already opened for this run: planning is idempotent
        capas.append(Capa(
            run_id=run.id,
            item_id=item_id,
            owner=run.owner or template.assignee,
            due_date=today + timedelta(days=rules.due_days),
            repeat=bool(previous),
        ))
    if not capas:
        return CapaPlan((), None, None)
    return CapaPlan(tuple(capas), template.id, today + timedelta(days=rules.followup_days))
