"""S17Code — the budget-aware agent runtime.

One importable package. The live graph, scoped memory and A2A boundary come from
Session 13; the generative-UI layer from Session 14; ``economics`` and
``telemetry`` are this session's work. There is no second package, and nothing is
nested inside a previous session's namespace.

    checklist_agent.core.live_graph   executor, durable event journal, patches
    checklist_agent.core.memory       typed scoped memory, semantic chunking
    checklist_agent.core.a2a          the agent-to-agent boundary
    checklist_agent.ui                catalog, validator, surface, AG-UI stream, HITL
    checklist_agent.economics         budget, tiers, policy, the hard controller
    checklist_agent.telemetry         the same journal, exported as OTel spans
"""

__version__ = "0.1.0"
