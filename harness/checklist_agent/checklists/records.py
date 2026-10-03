"""Platform rows -> domain models. Pure; no network."""
from __future__ import annotations

import hashlib
from datetime import date
from typing import Any

from checklist_agent.checklists.models import Item, Run, RunStatus, Template

# Written into `notes` on runs and `content` on SOPs the agent creates, so a
# later read can tell its rows from everybody else's.
AGENT_MARKER = "[checklist_agent]"


def _day(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def template_from_row(row: dict[str, Any]) -> Template:
    items = tuple(Item(id=str(index), text=str(item.get("name") or ""), blocker=bool(item.get("is_blocker")))
                  for index, item in enumerate(row.get("items") or []))
    return Template(
        id=row["id"], name=row.get("name") or "", category=row.get("category") or None,
        frequency=row.get("frequency") or None, assignee=row.get("default_assignee_id") or None,
        active=bool(row.get("is_active")), items=items,
    )


def run_from_row(row: dict[str, Any]) -> Run:
    status: RunStatus | str = str(row.get("status") or "")
    try:
        status = RunStatus(status)
    except ValueError:
        pass  # a state this code does not know is kept as the raw string
    return Run(
        id=row["id"], template_id=row.get("template_id") or "", status=status,
        owner=row.get("assigned_to_id") or None, due_date=_day(row.get("due_date")),
        reviewer=row.get("reviewer_id") or None, name=row.get("name") or "",
        agent_created=AGENT_MARKER in str(row.get("notes") or ""),
    )


def description_key(description: str) -> str:
    """A stable key for a process description, so a retry finds its own SOP whatever title the LLM picks."""
    normalised = " ".join(description.split()).lower()
    return hashlib.sha256(normalised.encode()).hexdigest()[:12]
