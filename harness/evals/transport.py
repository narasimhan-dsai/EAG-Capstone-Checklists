"""Scripted LLM transport: replay preset replies, inject gateway faults.

``ScriptedLLM`` has the same ``complete`` / ``close`` surface as
``checklist_agent.gateway.GatewayClient``, so it drops in wherever the agent
takes an ``llm`` callable. The agent's real path (planner validation, the
worker guards, live AgentSwitch calls) runs unchanged; only the model is
replaced. That makes a run repeatable, free of quota, and able to simulate
the failures the live gateway produces.

A script is a JSON file::

    {"name": "...", "steps": [
        {"reply": {"add": [...], "finish": false, "reason": "..."}},
        {"reply": {"ready": true, "missing": [], "reason": "..."}},
        {"reply": "plain text, used as-is"},
        {"fault": "503"}
    ]}

Steps are consumed in call order: planner call, then (when the planner adds
the terminal capability) the evidence review, then the answer worker's call.
A ``reply`` object is serialised to JSON text; a string is passed through.
Faults raise the same exception text the real client raises, so failure
handling is exercised rather than mimicked.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any


class ScriptExhausted(RuntimeError):
    """The agent made more LLM calls than the script provides."""


class ScriptedLLM:
    def __init__(self, steps: list[dict[str, Any]], *, name: str = "scripted") -> None:
        for index, step in enumerate(steps):
            if not isinstance(step, dict) or ("reply" in step) == ("fault" in step):
                raise ValueError(f"step {index}: exactly one of 'reply' or 'fault' is required")
        self.name = name
        self.steps = steps
        self.calls: list[dict[str, Any]] = []

    @classmethod
    def from_file(cls, path: str | Path) -> "ScriptedLLM":
        data = json.loads(Path(path).read_text())
        return cls(data["steps"], name=data.get("name", Path(path).stem))

    @classmethod
    def with_fault(cls, fault: str, *, at_call: int = 0, steps: list[dict[str, Any]] | None = None) -> "ScriptedLLM":
        """Copy of a script (or an empty one) with a fault injected at a call index."""
        base = list(steps or [])
        base[at_call:at_call + 1] = [{"fault": fault}]
        return cls(base, name=f"fault_{fault}_at_{at_call}")

    async def complete(self, prompt: str, system: str, *, session: str | None = None,
                       request: dict[str, Any] | None = None) -> dict[str, Any]:
        index = len(self.calls)
        self.calls.append({"index": index, "prompt": prompt, "system": system})
        if index >= len(self.steps):
            raise ScriptExhausted(f"script {self.name!r} has {len(self.steps)} steps; call {index} has none")
        step = self.steps[index]
        if "fault" in step:
            self.calls[-1]["fault"] = step["fault"]
            raise _fault(step["fault"])
        reply = step["reply"]
        text = reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)
        return {"text": text, "provider": "scripted", "model": self.name,
                "input_tokens": 0, "output_tokens": 0}

    @property
    def unused_steps(self) -> int:
        return max(0, len(self.steps) - len(self.calls))

    async def close(self) -> None:
        return None


class PacedLLM:
    """Live gateway with a minimum gap between calls, to stay inside the
    gateway's per-minute quota across a multi-step run."""

    def __init__(self, gateway: Any, *, min_interval: float = 0.0) -> None:
        self.gateway, self.min_interval = gateway, min_interval
        self._last = 0.0
        self._lock = asyncio.Lock()

    async def complete(self, prompt: str, system: str, **kwargs: Any) -> dict[str, Any]:
        async with self._lock:
            wait = self._last + self.min_interval - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                return await self.gateway.complete(prompt, system, **kwargs)
            finally:
                self._last = time.monotonic()

    async def health(self) -> dict[str, Any]:
        return await self.gateway.health()

    async def close(self) -> None:
        await self.gateway.close()


def _fault(kind: str) -> Exception:
    if kind in {"429", "502", "503"}:
        # Same shape GatewayClient.chat raises after exhausting its retries.
        return RuntimeError(f"gateway /v1/chat returned {kind}: scripted fault (RPM quota burned)")
    if kind == "timeout":
        return asyncio.TimeoutError("scripted fault: gateway call timed out")
    raise ValueError(f"unknown fault kind {kind!r}; use 429, 502, 503 or timeout")
