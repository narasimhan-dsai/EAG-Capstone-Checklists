from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_PATH = Path(__file__).with_name("rules.yaml")


@dataclass(frozen=True)
class Rules:
    categories: frozenset[str]
    frequencies: frozenset[str]
    odd_frequencies: dict[str, frozenset[str]] = field(default_factory=dict)
    min_items: int = 1
    followup_days: int = 7
    due_days: int = 14
    sop_categories: frozenset[str] = frozenset()
    audit_category: str = "safety"
    audit_frequencies: frozenset[str] = frozenset({"monthly", "weekly", "daily"})
    terminal_statuses: frozenset[str] = frozenset({"reviewed"})
    owner_statuses: frozenset[str] = frozenset({"draft", "in_progress", "rejected"})
    reviewer_statuses: frozenset[str] = frozenset({"submitted"})


def load_rules(path: Path | str | None = None) -> Rules:
    data = yaml.safe_load(Path(path or DEFAULT_PATH).read_text()) or {}
    capa = data.get("capa", {})
    audit = data.get("audit", {})
    overdue = data.get("overdue", {})
    return Rules(
        categories=frozenset(data.get("categories", [])),
        frequencies=frozenset(data.get("frequencies", [])),
        odd_frequencies={k: frozenset(v) for k, v in (data.get("odd_frequencies") or {}).items()},
        min_items=int(data.get("min_items", 1)),
        followup_days=int(capa.get("followup_days", 7)),
        due_days=int(capa.get("due_days", 14)),
        sop_categories=frozenset(data.get("sop_categories", [])),
        audit_category=str(audit.get("category", "safety")),
        audit_frequencies=frozenset(audit.get("frequencies", ["monthly", "weekly", "daily"])),
        terminal_statuses=frozenset(overdue.get("terminal_statuses", ["reviewed"])),
        owner_statuses=frozenset(overdue.get("owner_statuses", ["draft", "in_progress", "rejected"])),
        reviewer_statuses=frozenset(overdue.get("reviewer_statuses", ["submitted"])),
    )
