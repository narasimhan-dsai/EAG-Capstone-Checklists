"""The Seat 23 agent: checklists and SOPs on AgentSwitch.

    checklist_agent.core.live_graph   executor, durable event journal, patches
    checklist_agent.checklists        pure rules, models, audit scope, overdue logic
    checklist_agent.agentswitch       MCP client
    checklist_agent.capabilities      what the planner may choose from
    checklist_agent.planner           validates a model's proposal before anything runs
    checklist_agent.workers           one guarded worker per capability
    checklist_agent.gateway           the seam to the LLM gateway
"""

__version__ = "0.1.0"
