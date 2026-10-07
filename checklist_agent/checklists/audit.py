"""Flag misconfigured templates: bad category, odd frequency, missing assignee."""
from __future__ import annotations

from collections.abc import Iterable

from checklist_agent.checklists.models import Finding, Template
from checklist_agent.checklists.rules import Rules, load_rules


def audit_template(template: Template, rules: Rules | None = None) -> list[Finding]:
    rules = rules or load_rules()
    found: list[Finding] = []

    def flag(code: str, message: str) -> None:
        found.append(Finding(template.id, code, message))

    if not template.category:
        flag("missing_category", "template has no category")
    elif template.category not in rules.categories:
        flag("wrong_category", f"category {template.category!r} is not one of {sorted(rules.categories)}")

    if not template.frequency:
        flag("missing_frequency", "template has no frequency")
    elif template.frequency not in rules.frequencies:
        flag("odd_frequency", f"frequency {template.frequency!r} is not recognised")
    elif template.frequency in rules.odd_frequencies.get(template.category or "", frozenset()):
        flag("odd_frequency", f"{template.frequency} is unusual for a {template.category} checklist")

    if template.active and not template.assignee:
        flag("missing_assignee", "active template has no assignee")
    if len(template.items) < rules.min_items:
        flag("no_items", "template has no checklist items")
    names = {item.text for item in template.items}
    for item in template.items:
        if item.branch_rule and item.branch_rule not in names:
            flag("unresolved_branch_rule", f"item {item.text!r} has a branch rule that names no item of "
                 f"this template ({item.branch_rule!r}); the platform refuses to create runs from it")
    return found


def audit_templates(templates: Iterable[Template], rules: Rules | None = None) -> list[Finding]:
    rules = rules or load_rules()
    return [f for t in templates for f in audit_template(t, rules)]
