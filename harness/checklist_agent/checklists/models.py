from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class RunStatus(StrEnum):
    DRAFT = "draft"
    IN_PROGRESS = "in_progress"
    SUBMITTED = "submitted"
    APPROVED = "approved"
    REVIEWED = "reviewed"
    REJECTED = "rejected"


@dataclass(frozen=True)
class Item:
    id: str
    text: str
    blocker: bool = False


@dataclass(frozen=True)
class Template:
    id: str
    name: str
    category: str | None = None
    frequency: str | None = None
    assignee: str | None = None
    active: bool = True
    items: tuple[Item, ...] = ()
    sop_id: str | None = None


@dataclass(frozen=True)
class ItemResult:
    item_id: str
    passed: bool | None = None  # None = not answered yet
    note: str = ""


@dataclass(frozen=True)
class Run:
    id: str
    template_id: str
    status: RunStatus | str
    owner: str | None = None
    results: tuple[ItemResult, ...] = ()
    completed_on: date | None = None
    due_date: date | None = None
    reviewer: str | None = None
    agent_created: bool = False
    name: str = ""


@dataclass(frozen=True)
class Finding:
    """One problem found by an audit."""
    subject_id: str
    code: str
    message: str


@dataclass(frozen=True)
class Capa:
    """Corrective/preventive action. Owner, due date and root cause are part of
    the record; root cause stays None until a human supplies it."""
    run_id: str
    item_id: str
    owner: str | None
    due_date: date
    root_cause: str | None = None
    repeat: bool = False
    status: str = "open"
