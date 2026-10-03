"""Record every AgentSwitch call the agent makes, outside the agent's own journal.

``RecordingClient`` wraps :class:`checklist_agent.agentswitch.AgentSwitchClient`
and is handed to ``run_goal`` in its place, so agent code is unchanged. The
verifiers ``tool_called``, ``tool_not_called`` and ``no_mutation`` read this
log rather than trusting the graph journal the agent itself wrote.
"""
from __future__ import annotations

import time
from typing import Any

_READ_SUFFIXES = {"list", "get"}


def is_mutating_tool(name: str) -> bool:
    """Conservative: anything that is not a plain ``.list`` / ``.get`` counts as mutating."""
    return name.rsplit(".", 1)[-1] not in _READ_SUFFIXES


class RecordingClient:
    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.calls: list[dict[str, Any]] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None, *, jurisdiction: str) -> Any:
        entry = self._start("mcp", name, arguments or {}, jurisdiction, is_mutating_tool(name))
        return await self._finish(entry, self._inner.call_tool(name, arguments, jurisdiction=jurisdiction))

    async def rest(self, method: str, path: str, *, jurisdiction: str, json_body: dict[str, Any] | None = None) -> Any:
        entry = self._start("rest", f"{method} {path}", json_body or {}, jurisdiction, method != "GET")
        return await self._finish(entry, self._inner.rest(method, path, jurisdiction=jurisdiction, json_body=json_body))

    async def close(self) -> None:
        """No-op: the runner still needs the connection to snapshot after the run."""

    def _start(self, via: str, tool: str, args: dict[str, Any], jurisdiction: str, mutating: bool) -> dict[str, Any]:
        entry = {"seq": len(self.calls), "via": via, "tool": tool, "args": args,
                 "jurisdiction": jurisdiction, "mutating": mutating, "at": time.time(),
                 "ok": None, "result": None, "error": None}
        self.calls.append(entry)
        return entry

    @staticmethod
    async def _finish(entry: dict[str, Any], awaitable: Any) -> Any:
        try:
            result = await awaitable
        except Exception as problem:
            entry.update(ok=False, error={"type": type(problem).__name__, "message": str(problem),
                                          "code": getattr(problem, "code", None)})
            raise
        entry.update(ok=True, result=result)
        return result
