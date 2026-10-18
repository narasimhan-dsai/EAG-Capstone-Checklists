"""Planning for the actions that change data. Pure: rows in, decisions out; nothing here calls the platform."""
from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from typing import Any

from checklist_agent.checklists.models import Run, Template
from checklist_agent.checklists.rules import Rules, load_rules
from checklist_agent.checklists.schedule import schedule_gaps
from checklist_agent.verify import ungrounded

ITEM_TYPES = frozenset({"check", "text", "number", "photo", "select", "measurement"})
MAX_ITEMS = 20


def recurring_candidates(templates: Iterable[Template], runs: Iterable[Run], today: date,
                         category: str | None = None) -> list[dict[str, Any]]:
    """Active recurring templates with no run due in the current period, earliest period end first."""
    templates = list(templates)
    by_id = {t.id: t for t in templates}
    gaps = [g for g in schedule_gaps(templates, runs, today).gaps
            if g["period"] == "current" and (category is None or by_id[g["template_id"]].category == category)]
    gaps.sort(key=lambda g: (g["period_end"], g["name"]))
    return gaps


def _row(run: Run, today: date) -> dict[str, Any]:
    return {"id": run.id, "number": run.number, "name": run.name, "status": str(run.status),
            "due_date": run.due_date.isoformat() if run.due_date else None,
            "days_late": (today - run.due_date).days if run.due_date else None}


def followups(runs: Iterable[Run], today: date, rules: Rules | None = None) -> dict[str, Any]:
    """Who to chase (overdue runs by owner) and what only a human can fix (no assignee, no reviewer)."""
    rules = rules or load_rules()
    runs = list(runs)
    late = sorted((r for r in runs if r.due_date is not None and r.due_date < today
                   and str(r.status) not in rules.terminal_statuses), key=lambda r: (r.due_date, r.id))
    by_owner: dict[str, list[dict[str, Any]]] = {}
    no_assignee: list[dict[str, Any]] = []
    for run in late:
        if run.owner:
            by_owner.setdefault(run.owner_name or run.owner, []).append(_row(run, today))
        else:
            no_assignee.append(_row(run, today))
    no_reviewer = [_row(r, today) for r in runs if str(r.status) in rules.reviewer_statuses and not r.reviewer]
    return {"by_owner": by_owner, "no_assignee": no_assignee, "no_reviewer": no_reviewer}


def validate_template_draft(data: dict[str, Any], source_text: str) -> list[dict[str, Any]]:
    """The items a model proposed for a checklist, checked. Raises ValueError for anything unusable,
    including a figure or id the source text never stated."""
    items = data.get("items")
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_ITEMS:
        raise ValueError(f"items must be a list of 1 to {MAX_ITEMS} entries")
    clean: list[dict[str, Any]] = []
    for index, item in enumerate(items, 1):
        if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not item["name"].strip():
            raise ValueError(f"item {index} needs a non-empty name")
        name = item["name"].strip()
        kind = item.get("item_type", "check")
        instructions = item.get("instructions")
        if len(name) > 160 or kind not in ITEM_TYPES:
            raise ValueError(f"item {index} has an unusable name or type")
        if instructions is not None and (not isinstance(instructions, str) or len(instructions) > 400):
            raise ValueError(f"item {index} has unusable instructions")
        row: dict[str, Any] = {"order": index, "name": name, "item_type": kind}
        if instructions and instructions.strip():
            row["instructions"] = instructions.strip()
        clean.append(row)
    written = " ".join(f"{i['name']} {i.get('instructions', '')}" for i in clean)
    invented = ungrounded(written, [], source_text)
    if invented["numbers"] or invented["ids"]:
        raise ValueError(f"the draft states figures that are not in the SOP: {invented['numbers'] + invented['ids']}")
    return clean


def incomplete_required_items(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Required items still open: the nonconformance candidates when a run is rejected."""
    out = []
    for item in items:
        if item.get("is_required") and not item.get("completed") and not item.get("branch_skipped"):
            out.append({"key": f"item:{item['order']:g}", "order": item["order"], "name": item.get("name", "")})
    return out
