"""Read-only summaries of runs and SOPs. Pure: every function takes rows already fetched."""
from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from typing import Any

from checklist_agent.checklists.models import Run, Sop
from checklist_agent.checklists.rules import Rules, load_rules

_NO_REVIEWER = "(no reviewer)"
_NO_CATEGORY = "(none)"


def _is_open(run: Run, rules: Rules) -> bool:
    return str(run.status) not in rules.terminal_statuses


def _day(stamp: str | None) -> date | None:
    try:
        return date.fromisoformat(str(stamp)[:10]) if stamp else None
    except ValueError:
        return None


def blocked_runs(runs: Iterable[Run], today: date, rules: Rules | None = None, limit: int = 25) -> dict[str, Any]:
    """Open runs with at least one blocker item still pending: the platform will not let these finish."""
    rules = rules or load_rules()
    blocked = [r for r in runs if _is_open(r, rules) and r.blockers_pending > 0]
    blocked.sort(key=lambda r: (-r.blockers_pending, r.due_date or date.max, r.id))
    by_status: dict[str, int] = {}
    for r in blocked:
        by_status[str(r.status)] = by_status.get(str(r.status), 0) + 1
    return {"as_of": today.isoformat(), "total": len(blocked), "by_status": by_status,
            "runs": [{"id": r.id, "name": r.name, "status": str(r.status), "owner": r.owner_name,
                      "due_date": r.due_date.isoformat() if r.due_date else None,
                      "blockers_pending": r.blockers_pending} for r in blocked[:limit]]}


def review_queue(runs: Iterable[Run], today: date, rules: Rules | None = None, limit: int = 25) -> dict[str, Any]:
    """Runs handed over and waiting on a reviewer. ``days_waiting`` counts from the run's last update."""
    rules = rules or load_rules()
    waiting = [r for r in runs if str(r.status) in rules.reviewer_statuses]
    by_reviewer: dict[str, int] = {}
    rows = []
    for r in waiting:
        name = r.reviewer_name or _NO_REVIEWER
        by_reviewer[name] = by_reviewer.get(name, 0) + 1
        since = _day(r.updated_at)
        rows.append({"id": r.id, "name": r.name, "reviewer": name,
                     "due_date": r.due_date.isoformat() if r.due_date else None,
                     "days_waiting": (today - since).days if since else None})
    rows.sort(key=lambda row: (-(row["days_waiting"] if row["days_waiting"] is not None else -1), row["id"]))
    return {"as_of": today.isoformat(), "total": len(waiting), "by_reviewer": by_reviewer,
            "waiting_since": "the run's last update", "oldest": rows[:limit]}


def completion_summary(runs: Iterable[Run], rules: Rules | None = None) -> dict[str, Any]:
    """Item completion across all runs, the way the dashboard counts it (items done over items total)."""
    rules = rules or load_rules()
    done = total = 0.0
    reviewed = open_ = 0
    categories: dict[str, dict[str, float]] = {}
    for r in runs:
        done += r.completed_items
        total += r.total_items
        if _is_open(r, rules):
            open_ += 1
        else:
            reviewed += 1
        bucket = categories.setdefault(r.category or _NO_CATEGORY, {"runs": 0, "done": 0.0, "total": 0.0})
        bucket["runs"] += 1
        bucket["done"] += r.completed_items
        bucket["total"] += r.total_items

    def pct(numerator: float, denominator: float) -> float:
        return round(numerator * 100 / denominator, 1) if denominator else 0.0

    return {"items_completed": int(done), "items_total": int(total), "items_remaining": int(total - done),
            "completion_pct": pct(done, total), "runs_reviewed": reviewed, "runs_open": open_,
            "by_category": {name: {"runs": int(b["runs"]), "items_completed": int(b["done"]),
                                   "items_total": int(b["total"]), "completion_pct": pct(b["done"], b["total"])}
                            for name, b in sorted(categories.items())}}


def sops_due_review(sops: Iterable[Sop], review_days: int, today: date, limit: int = 25) -> dict[str, Any]:
    """Published SOPs not reviewed within the review frequency, counting from the publish date
    (or the last update when there is no publish date)."""
    due = []
    for sop in sops:
        since = sop.published_at or sop.updated_at
        if sop.status != "published" or since is None:
            continue
        age = (today - since).days
        if age > review_days:
            due.append({"id": sop.id, "name": sop.name, "number": sop.number, "since": since.isoformat(),
                        "days_since": age})
    due.sort(key=lambda s: (-s["days_since"], s["id"]))
    return {"as_of": today.isoformat(), "review_days": review_days, "total": len(due), "sops": due[:limit]}
