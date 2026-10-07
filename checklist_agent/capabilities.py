"""The complete capability surface exposed to the checklist planner.

The planner in ``planner.py`` is domain-agnostic: it depends only on this
registry's shape. A model can select a capability; it cannot invent one or
bypass its validation boundary, because every task is validated here before it
can enter the live graph.

Extend by adding a ``Capability`` to ``default_registry``, never by branching
on a skill name elsewhere. A capability that changes data sets
``side_effect=True``: the planner offers it only when the run was authorised
for it (``--allow <name>``). There is deliberately no capability that
approves, rejects, publishes or deletes: this seat cannot do those.
"""
from __future__ import annotations

import re
from collections.abc import Collection
from dataclasses import dataclass, field
from typing import Any

_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


class CapabilityError(ValueError):
    """A proposed task does not satisfy the advertised capability contract."""


def _check_format(label: str, format_name: str, value: Any, known_values: Collection[str] | None) -> None:
    """Validate a declared value format. Unknown formats are a configuration bug."""
    if format_name == "id":
        # Provenance, not shape: an id must be a string the run has actually
        # seen (in the goal, the stimulus, or a succeeded outcome). A model
        # that invents one is rejected before anything reaches AgentSwitch.
        # ``None`` means the caller supplied no evidence set, so nothing to check against.
        if known_values is not None and value not in known_values:
            raise CapabilityError(
                f"{label}={value!r} does not appear in the goal or any earlier outcome; "
                "use an id taken from a completed result, not one you infer")
        return
    if format_name == "month":
        if not isinstance(value, str) or not _MONTH_RE.match(value):
            raise CapabilityError(f"{label} must be a pay month as YYYY-MM")
        return
    raise CapabilityError(f"unsupported argument format {format_name!r} on {label}")


@dataclass(frozen=True)
class Argument:
    kind: str
    description: str
    required: bool = True
    default: Any = None
    minimum: int | None = None
    maximum: int | None = None
    choices: tuple[str, ...] = ()
    format: str | None = None

    def manifest(self) -> dict[str, Any]:
        result: dict[str, Any] = {"type": self.kind, "description": self.description}
        if not self.required:
            result["required"] = False
            if self.default is not None:
                result["default"] = self.default
        if self.minimum is not None:
            result["minimum"] = self.minimum
        if self.maximum is not None:
            result["maximum"] = self.maximum
        if self.choices:
            result["enum"] = list(self.choices)
        if self.format:
            result["format"] = self.format
        return result


@dataclass(frozen=True)
class Capability:
    name: str
    description: str
    arguments: dict[str, Argument] = field(default_factory=dict)
    role: str | None = None
    side_effect: bool = False
    terminal_for: tuple[str, ...] = ()
    families: tuple[str, ...] = ()
    needs_evidence: bool = True

    def manifest(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "arguments": {name: spec.manifest() for name, spec in self.arguments.items()},
            "side_effect": self.side_effect,
            "terminal_for": list(self.terminal_for),
        }


class CapabilityRegistry:
    def __init__(self, capabilities: list[Capability]) -> None:
        self._items = {item.name: item for item in capabilities}
        if len(self._items) != len(capabilities):
            raise ValueError("capability names must be unique")

    def __contains__(self, name: str) -> bool:
        return name in self._items

    def get(self, name: str) -> Capability:
        try:
            return self._items[name]
        except KeyError as error:
            raise CapabilityError(f"unknown capability {name!r}") from error

    def manifest(self) -> list[dict[str, Any]]:
        return [item.manifest() for item in self._items.values()]

    def names(self) -> tuple[str, ...]:
        return tuple(self._items)

    def terminal_skills(self, respond_as: str) -> set[str]:
        return {item.name for item in self._items.values() if respond_as in item.terminal_for}

    def family(self, name: str) -> set[str]:
        """Every capability declaring membership of a named family."""
        return {item.name for item in self._items.values() if name in item.families}

    def validate(self, name: str, values: Any, *, known_values: Collection[str] | None = None) -> dict[str, Any]:
        capability = self.get(name)
        if not isinstance(values, dict):
            raise CapabilityError(f"arguments for {name} must be an object")
        unknown = set(values).difference(capability.arguments)
        if unknown:
            raise CapabilityError(f"unsupported arguments for {name}: {sorted(unknown)}")
        clean: dict[str, Any] = {}
        for key, spec in capability.arguments.items():
            # Models often send null or "" for an optional argument they have nothing for.
            blank = not spec.required and (values.get(key) is None
                                           or (isinstance(values.get(key), str) and not values[key].strip()))
            if key not in values or blank:
                if spec.required:
                    raise CapabilityError(f"{name} requires argument {key!r}")
                if spec.default is not None:
                    clean[key] = spec.default
                continue
            value = values[key]
            if spec.kind == "string":
                if not isinstance(value, str) or not value.strip():
                    raise CapabilityError(f"{name}.{key} must be a non-empty string")
                value = value.strip()
                if spec.maximum is not None and len(value) > spec.maximum:
                    raise CapabilityError(f"{name}.{key} exceeds {spec.maximum} characters")
            elif spec.kind == "integer":
                if isinstance(value, bool) or not isinstance(value, int):
                    raise CapabilityError(f"{name}.{key} must be an integer")
                if spec.minimum is not None and value < spec.minimum:
                    raise CapabilityError(f"{name}.{key} must be >= {spec.minimum}")
                if spec.maximum is not None and value > spec.maximum:
                    raise CapabilityError(f"{name}.{key} must be <= {spec.maximum}")
            elif spec.kind == "boolean":
                if not isinstance(value, bool):
                    raise CapabilityError(f"{name}.{key} must be a boolean")
            else:
                raise CapabilityError(f"unsupported contract type {spec.kind!r}")
            if spec.choices and value not in spec.choices:
                raise CapabilityError(f"{name}.{key} must be one of {list(spec.choices)}")
            # Value-level rules are declared on the argument, not switched on
            # the capability name -- see agentswitch's "month" format, the
            # same pattern AgentSwitch's own tool schema uses server-side.
            if spec.format:
                _check_format(f"{name}.{key}", spec.format, value, known_values)
            clean[key] = value
        return clean


def default_registry() -> CapabilityRegistry:
    def string(description: str, **kwargs: Any) -> Argument:
        return Argument("string", description, **kwargs)

    return CapabilityRegistry([
        Capability(
            "plan_safety_audit",
            "Preview the monthly safety audit without changing anything. Reads every template and "
            "run fresh and reports which active safety templates (monthly, weekly or daily) would get "
            "a new run this month, which already have a run (left untouched), which of our own draft "
            "runs would be started, and which templates have no assignee. Read-only.",
            {}, families=("evidence",),
        ),
        Capability(
            "find_schedule_gaps",
            "Find recurring checklists whose schedule has slipped. For every active template that is daily, "
            "weekly, monthly, quarterly or yearly, checks whether a run is due in the current period and in "
            "the previous one. A 'current' gap means the run is not created yet; a 'previous' gap means a "
            "period was missed. Returns the total, a split by frequency and period, and the earliest gaps. "
            "Read-only.",
            {}, families=("evidence",),
        ),
        Capability(
            "list_blocked_runs",
            "List open runs that still have blocker items pending, which the platform will not let finish. "
            "Returns the total, a split by status and the runs with the most blockers. Read-only.",
            {}, families=("evidence",),
        ),
        Capability(
            "list_review_queue",
            "List runs that were handed over and are waiting on a reviewer. Returns the total, a split by "
            "reviewer and the longest-waiting runs (waiting is counted from the run's last update). Read-only.",
            {}, families=("evidence",),
        ),
        Capability(
            "summarize_completion",
            "Summarise checklist completion across all runs the way the dashboard does: items completed "
            "over items total, with the number of reviewed and open runs and a split by category. Read-only.",
            {}, families=("evidence",),
        ),
        Capability(
            "audit_templates",
            "Audit every template for configuration problems: missing or unknown category, an odd "
            "frequency, an active template with no assignee, no items, or a branch rule that names no item "
            "(the platform refuses to create runs from such a template). Returns counts by problem and the "
            "first findings with template names. Read-only; it fixes nothing.",
            {}, families=("evidence",),
        ),
        Capability(
            "list_sops_due_review",
            "List published SOPs that are overdue for periodic review, using the review frequency set in the "
            "checklist preferences (180 days when it cannot be read). Returns the total, the frequency and "
            "where it came from, and the oldest SOPs. Read-only.",
            {}, families=("evidence",),
        ),
        Capability(
            "list_overdue_runs",
            "List overdue checklist runs. Overdue means the due date is before today and the run is not "
            "reviewed. Returns the total, a split by status, who is blocking (the owner for draft or "
            "in-progress runs, the reviewer for submitted ones), the count with no assignee, and the "
            "oldest runs. Read-only; the result states the rule used.",
            {}, families=("evidence",),
        ),
        Capability(
            "start_monthly_safety_audit",
            "Start the monthly safety audit: for every active safety template with frequency monthly, "
            "weekly or daily, create this month's run (due at month end) and start it so it is "
            "in_progress. A template that already has a run this month is skipped and that run is left "
            "untouched. Never submits a run. Reports per template: created and started, started an "
            "earlier draft of its own, skipped, or failed, and lists templates with no assignee. "
            "A change: it needs explicit authority. Use plan_safety_audit first to preview.",
            {}, side_effect=True,
        ),
        Capability(
            "process_to_sop",
            "Turn a plain-language process description into an SOP: drafts it, saves it as a draft "
            "SOPDocument and submits it for review. It can never publish it; a human reviewer does "
            "that. A change: it needs explicit authority.",
            {"process_description": string("The process description from the user's request, verbatim.",
                                           maximum=8_000)},
            side_effect=True,
        ),
        Capability(
            "answer_with_evidence",
            "Produce the final grounded text answer from everything this run has "
            "discovered so far. Use once, after enough evidence has been gathered -- not "
            "before a needed lookup has actually run.",
            {"query": string("The user's original request, verbatim.", maximum=4_000)},
            role="answer", terminal_for=("text",),
        ),
        Capability(
            "decline_request",
            "End the run with a refusal when the request needs an action NO capability here "
            "provides, for example publishing an SOP, approving or rejecting a run, deleting "
            "a record, or reading data that belongs to another app. Pick the reason code that "
            "fits. Do NOT use it when a capability exists for the request, even if it may be "
            "refused at runtime (the runtime reports those). No lookups are needed first.",
            {"reason_code": string("Why it cannot be done.",
                                   choices=("no_such_capability", "needs_human_approval",
                                            "outside_checklists_scope", "not_permitted")),
             "explanation": string("Plain-language reason, naming what was asked.", maximum=600),
             "alternative": string("What can be done instead, if anything.", required=False, maximum=300)},
            role="answer", terminal_for=("text",), needs_evidence=False,
        ),
    ])
