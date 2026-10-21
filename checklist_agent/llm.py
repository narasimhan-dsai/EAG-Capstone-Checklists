"""The language model the agent talks to.

Two seams with one shape, ``async complete(prompt, system, *, session=None, request=None)``:

* :class:`checklist_agent.gateway.GatewayClient`, the local LLM gateway that owns provider keys; and
* :class:`OpenAIClient`, the platform's own OpenAI-compatible model, which the harness runner provides
  through ``OPENAI_BASE_URL``, ``OPENAI_API_KEY`` and ``OPENAI_MODEL``. No key of our own is involved.

:func:`make_llm` picks the platform's model when its variables are set and the gateway otherwise.
"""
from __future__ import annotations

import os
from typing import Any

DEFAULT_MODEL = "agentswitch-default"
# A reasoning model spends part of this thinking before it writes; too small a budget returns empty text.
_MAX_TOKENS = 4096


class OpenAIClient:
    """A thin chat transport over any OpenAI-compatible server. Reads only the reply text, the served model
    name and the token counts, so it works with models that do not implement every OpenAI feature."""

    def __init__(self, client: Any | None = None, model: str | None = None) -> None:
        if client is None:
            from openai import AsyncOpenAI  # picks up OPENAI_BASE_URL and OPENAI_API_KEY by itself

            client = AsyncOpenAI(max_retries=4, timeout=120)
        self._client = client
        self.model = model or os.getenv("OPENAI_MODEL", DEFAULT_MODEL)

    async def complete(self, prompt: str, system: str, *, session: str | None = None,
                       request: dict[str, Any] | None = None) -> dict[str, Any]:
        reply = await self._client.chat.completions.create(
            model=self.model, temperature=0, max_tokens=int((request or {}).get("max_tokens") or _MAX_TOKENS),
            messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}])
        usage = getattr(reply, "usage", None)
        return {"text": reply.choices[0].message.content or "", "provider": "openai-compatible",
                "model": getattr(reply, "model", None) or self.model,
                "input_tokens": getattr(usage, "prompt_tokens", 0) or 0,
                "output_tokens": getattr(usage, "completion_tokens", 0) or 0,
                "finish_reason": getattr(reply.choices[0], "finish_reason", None)}

    async def health(self) -> dict[str, Any]:
        """No cheap ping works on every compatible server; a bad endpoint shows up on the first completion."""
        return {"ok": True}

    async def close(self) -> None:
        closer = getattr(self._client, "close", None)
        if closer is not None:
            result = closer()
            if hasattr(result, "__await__"):
                await result


def make_llm() -> Any:
    """The platform's model when both of its variables are set, else the local gateway."""
    if os.getenv("OPENAI_BASE_URL") and os.getenv("OPENAI_API_KEY"):
        return OpenAIClient()
    from .gateway import GatewayClient

    return GatewayClient()
