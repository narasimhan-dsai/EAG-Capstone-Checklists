"""Recurring templates that have no run for the current or previous period. Pure."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from checklist_agent.checklists.models import Run, Template

_QUARTER_MONTHS = 3


def period_bounds(frequency: str | None, today: date) -> tuple[date, date] | None:
    """The inclusive date range of the period containing ``today``; None for a one-off or unknown."""
    if frequency == "daily":
        return today, today
    if frequency == "weekly":
        start = today - timedelta(days=today.weekday())
        return start, start + timedelta(days=6)
    if frequency == "monthly":
        start = today.replace(day=1)
        return start, (start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    if frequency == "quarterly":
        first = today.replace(month=(today.month - 1) // _QUARTER_MONTHS * _QUARTER_MONTHS + 1, day=1)
        end_month = first.month + _QUARTER_MONTHS - 1
        last = first.replace(month=end_month, day=28) + timedelta(days=4)
        return first, last.replace(day=1) - timedelta(days=1) if end_month < 12 else date(first.year, 12, 31)
    if frequency == "yearly":
        return date(today.year, 1, 1), date(today.year, 12, 31)
    return None


@dataclass(frozen=True)
class ScheduleReport:
    as_of: date
    templates_checked: int
    gaps: tuple[dict[str, Any], ...]

    def to_dict(self, limit: int = 30) -> dict[str, Any]:
        by_frequency: dict[str, int] = {}
        by_period = {"current": 0, "previous": 0}
        for gap in self.gaps:
            by_frequency[gap["frequency"]] = by_frequency.get(gap["frequency"], 0) + 1
            by_period[gap["period"]] += 1
        return {"as_of": self.as_of.isoformat(), "templates_checked": self.templates_checked,
                "total": len(self.gaps), "by_frequency": by_frequency, "by_period": by_period,
                "rule": "an active recurring template has a gap for a period when no run of it is due in "
                        "that period; 'current' is not created yet, 'previous' was missed",
                "gaps": list(self.gaps[:limit])}


def schedule_gaps(templates: Iterable[Template], runs: Iterable[Run], today: date) -> ScheduleReport:
    due_dates: dict[str, list[date]] = {}
    for run in runs:
        if run.due_date is not None:
            due_dates.setdefault(run.template_id, []).append(run.due_date)
    checked = 0
    gaps: list[dict[str, Any]] = []
    for template in templates:
        bounds = period_bounds(template.frequency, today) if template.active else None
        if bounds is None:
            continue
        checked += 1
        previous = period_bounds(template.frequency, bounds[0] - timedelta(days=1))
        for label, (start, end) in (("previous", previous), ("current", bounds)):
            if not any(start <= due <= end for due in due_dates.get(template.id, [])):
                gaps.append({"template_id": template.id, "name": template.name,
                             "frequency": template.frequency, "period": label,
                             "period_start": start.isoformat(), "period_end": end.isoformat()})
    gaps.sort(key=lambda g: (g["period_start"], g["name"]))
    return ScheduleReport(today, checked, tuple(gaps))
