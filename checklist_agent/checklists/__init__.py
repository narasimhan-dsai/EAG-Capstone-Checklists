"""Checklist domain: the model, the template audit, and CAPA-on-rejection.

Everything here is pure: no network, no clock, no platform client. Workers
fetch records and hand them in; these functions decide. Rules come from
``rules.yaml``, never from branches in code.
"""
from checklist_agent.checklists.audit import audit_template, audit_templates
from checklist_agent.checklists.capa import CapaPlan, plan_capa_for_rejection
from checklist_agent.checklists.models import (
    Capa,
    Finding,
    Item,
    ItemResult,
    Run,
    RunStatus,
    Template,
)
from checklist_agent.checklists.rules import Rules, load_rules
from checklist_agent.checklists.scope import (
    AuditPlan,
    OverdueReport,
    audit_scope,
    month_end,
    overdue,
    plan_audit,
)

__all__ = [
    "AuditPlan", "OverdueReport", "audit_scope", "month_end", "overdue", "plan_audit",
    "Capa", "CapaPlan", "Finding", "Item", "ItemResult", "Rules", "Run", "RunStatus",
    "Template", "audit_template", "audit_templates", "load_rules", "plan_capa_for_rejection",
]
