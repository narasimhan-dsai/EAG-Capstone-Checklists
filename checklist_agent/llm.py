"""The language model the agent talks to.

Two seams with one shape, ``async complete(prompt, system, *, session=None, request=None)``:

* :class:`checklist_agent.gateway.GatewayClient`, the local LLM gateway that owns provider keys; and
* :class:`OpenAIClient`, the platform's own OpenAI-compatible model, which the harness runner provides
  through ``OPENAI_BASE_URL``, ``OPENAI_API_KEY`` and ``OPENAI_MODEL``. No key of our own is involved.

:func:`make_llm` picks the platform's model when its variables are set and the gateway otherwise.
"""
from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

import httpx

DEFAULT_MODEL = "agentswitch-default"
# A reasoning model spends part of this thinking before it writes; too small a budget returns empty text.
_MAX_TOKENS = 4096
_MAX_TOKENS_CAP = 16384
_RETRY_STATUS = (429, 500, 502, 503, 504)


class OpenAIClient:
    """A thin chat transport over any OpenAI-compatible server. Reads only the reply text, the served model
    name and the token counts, so it works with models that do not implement every OpenAI feature."""

    def __init__(self, client: Any | None = None, model: str | None = None, *, attempts: int = 4,
                 base_delay: float = 1.0) -> None:
        if client is None:
            from openai import (  # picks up OPENAI_BASE_URL and OPENAI_API_KEY by itself
                AsyncOpenAI,
                OpenAIError,
            )

            try:
                client = AsyncOpenAI(max_retries=0, timeout=120)    # retries are made here, where they can be seen
            except OpenAIError as problem:
                raise RuntimeError(f"model endpoint is not configured: {problem}") from problem
        self._client = client
        self.model = model or os.getenv("OPENAI_MODEL", DEFAULT_MODEL)
        self.attempts, self.base_delay = max(1, attempts), base_delay

    async def complete(self, prompt: str, system: str, *, session: str | None = None,
                       request: dict[str, Any] | None = None) -> dict[str, Any]:
        """One completion. A busy server is retried with a growing pause. A reply that is empty because the
        model spent its whole budget thinking is asked for again with twice the room, up to a cap. Every
        failure ends as ``RuntimeError("model endpoint ...")``, which scoring treats as infrastructure."""
        from openai import APIConnectionError, APIStatusError, APITimeoutError

        messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
        tokens = min(int((request or {}).get("max_tokens") or _MAX_TOKENS), _MAX_TOKENS_CAP)
        last = "no attempt was made"
        for attempt in range(self.attempts):
            try:
                reply = await self._client.chat.completions.create(
                    model=self.model, temperature=0, max_tokens=tokens, messages=messages)
                choice = reply.choices[0] if reply.choices else None
                text = (choice.message.content or "") if choice else ""
                finish = getattr(choice, "finish_reason", None)
                if not text.strip():
                    print(f"model endpoint returned empty text (finish_reason={finish})", file=sys.stderr, flush=True)
                    if finish == "length" and tokens < _MAX_TOKENS_CAP and attempt < self.attempts - 1:
                        tokens = min(tokens * 2, _MAX_TOKENS_CAP)
                        continue
                usage = getattr(reply, "usage", None)
                return {"text": text, "provider": "openai-compatible",
                        "model": getattr(reply, "model", None) or self.model,
                        "input_tokens": getattr(usage, "prompt_tokens", 0) or 0,
                        "output_tokens": getattr(usage, "completion_tokens", 0) or 0,
                        "finish_reason": finish}
            except APIStatusError as problem:
                if problem.status_code not in _RETRY_STATUS:
                    raise RuntimeError(f"model endpoint returned {problem.status_code}: {problem.message}") from problem
                last = f"model endpoint returned {problem.status_code}: {problem.message}"
            except APITimeoutError as problem:
                last = f"model endpoint TimeoutError: {problem}"
            except (APIConnectionError, httpx.HTTPError) as problem:
                last = f"model endpoint ConnectError: unreachable ({type(problem).__name__})"
            if attempt < self.attempts - 1:
                await asyncio.sleep(self.base_delay * 2 ** attempt)
        raise RuntimeError(last)

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
