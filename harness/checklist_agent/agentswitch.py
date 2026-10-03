"""The checklist_agent -> AgentSwitch seam: one tenant login, one MCP surface.

MCP is JSON-RPC 2.0 over plain HTTP: a denied or invalid call still returns
HTTP 200, with the failure in the response's ``error`` field. Checking only
``response.status_code`` reads every denial as success. :meth:`call_tool` is
the one place that check happens, so nothing downstream can forget it.

A ``tools/call`` result's ``content`` is a one-item list of
``{"type": "text", "text": "<JSON-encoded string>"}``: the payload is JSON
inside that text field. A ``<Entity>.list`` tool wraps its rows under
``data`` (with ``total``); ``.get``, ``.create``, ``.update`` and transition
tools return the record flat.

Team 23's seat is on the India (Suryodaya) tenant, so callers pass
``jurisdiction="IN"``. The US variables stay unset and that tenant is simply
"not configured".
"""
from __future__ import annotations

import asyncio
import itertools
import json
import os
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

Jurisdiction = Literal["IN", "US"]
JURISDICTIONS: tuple[Jurisdiction, ...] = ("IN", "US")

# Same markers .env.example ships as placeholders, so "configured" means the
# same thing here as it does when a human reads the file.
_PLACEHOLDER_MARKERS = ("replace-", "teamNN", "YOUR_PASSWORD")


class AgentSwitchError(RuntimeError):
    """AgentSwitch refused or failed a call, at the HTTP layer or the JSON-RPC envelope."""


class AgentSwitchToolError(AgentSwitchError):
    """A ``tools/call`` returned a JSON-RPC error envelope (HTTP 200, error inside)."""

    def __init__(self, tool: str, code: Any, message: str, data: Any = None) -> None:
        super().__init__(f"{tool}: [{code}] {message}")
        self.tool, self.code, self.message, self.data = tool, code, message, data


def _looks_configured(value: str) -> bool:
    return bool(value) and not any(marker in value for marker in _PLACEHOLDER_MARKERS)


@dataclass
class _Tenant:
    """One jurisdiction's login state. Holds a token only -- never business
    data, so nothing here can go stale the way a cached entity row could."""

    base_url: str
    email: str
    password: str
    token: str | None = None
    tools: list[dict[str, Any]] | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class AgentSwitchClient:
    """MCP client for AgentSwitch.

    One instance holds both tenants, mirroring ``checklist_agent/gateway.py``'s
    ``GatewayClient`` for the LLM gateway seam: workers call this instead of
    reaching for ``httpx`` directly, so the envelope check, auth, retry and
    tenant-routing rules live in exactly one place.
    """

    def __init__(self, *, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(timeout=30)
        self._owns_client = client is None
        self._ids = itertools.count(1)
        self._tenants: dict[Jurisdiction, _Tenant] = {}
        for jurisdiction in JURISDICTIONS:
            base_url = os.getenv(f"AGENTSWITCH_{jurisdiction}_BASE_URL", "").rstrip("/")
            email = os.getenv(f"AGENTSWITCH_{jurisdiction}_EMAIL", "")
            password = os.getenv(f"AGENTSWITCH_{jurisdiction}_PASSWORD", "")
            if base_url and _looks_configured(email) and _looks_configured(password):
                self._tenants[jurisdiction] = _Tenant(base_url, email, password)

    def configured(self, jurisdiction: Jurisdiction) -> bool:
        return jurisdiction in self._tenants

    def _tenant(self, jurisdiction: Jurisdiction) -> _Tenant:
        try:
            return self._tenants[jurisdiction]
        except KeyError:
            raise AgentSwitchError(
                f"AgentSwitch/{jurisdiction} is not configured: set "
                f"AGENTSWITCH_{jurisdiction}_BASE_URL/_EMAIL/_PASSWORD"
            ) from None

    async def _login(self, tenant: _Tenant) -> str:
        response = await self._client.post(
            f"{tenant.base_url}/api/auth/login",
            json={"email": tenant.email, "password": tenant.password},
        )
        if response.status_code != 200:
            raise AgentSwitchError(
                f"AgentSwitch login failed: {response.status_code} {response.text[:300]}"
            )
        token = response.json().get("token")
        if not token:
            raise AgentSwitchError("AgentSwitch login response carried no token")
        tenant.token = token
        return token

    async def _token(self, tenant: _Tenant) -> str:
        if tenant.token is None:
            async with tenant.lock:
                if tenant.token is None:
                    await self._login(tenant)
        assert tenant.token is not None
        return tenant.token

    async def _mcp(
        self, tenant: _Tenant, method: str, params: dict[str, Any], *, retried: bool = False
    ) -> dict[str, Any]:
        token = await self._token(tenant)
        request_id = next(self._ids)
        attempts = max(1, int(os.getenv("AGENTSWITCH_ATTEMPTS", "5")))
        # Only reads are retried. A write that reached the server before a 502 would otherwise be
        # sent twice and create a duplicate run or document.
        tool = str(params.get("name") or "")
        retryable = method != "tools/call" or tool.startswith("tools.") or tool.rsplit(".", 1)[-1] in {"list", "get"}
        response = None
        for attempt in range(attempts):
            response = await self._client.post(
                f"{tenant.base_url}/api/mcp",
                json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.status_code == 401 and not retried:
                # A session token can expire mid-run. Retried exactly once:
                # an actually-wrong credential must fail loudly, not loop.
                tenant.token = None
                return await self._mcp(tenant, method, params, retried=True)
            if response.status_code not in {429, 502, 503} or not retryable:
                break
            if attempt < attempts - 1:
                await asyncio.sleep(min(0.5 * (2 ** attempt), 4.0))
        assert response is not None
        if response.status_code >= 400:
            raise AgentSwitchError(
                f"AgentSwitch MCP transport error: {response.status_code} {response.text[:300]}"
            )
        body = response.json()
        # The whole point of this method existing: a JSON-RPC error still
        # returns HTTP 200. Only the envelope says whether the call actually
        # succeeded -- confirmed live (invalid-argument and unknown-tool
        # calls both return 200 with an "error" key, code -32602).
        if "error" in body:
            error = body["error"] or {}
            raise AgentSwitchToolError(
                str(params.get("name") or method),
                error.get("code"),
                str(error.get("message", "")),
                error.get("data"),
            )
        return body.get("result") or {}

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None, *, jurisdiction: Jurisdiction
    ) -> Any:
        """Call one MCP tool and return its parsed payload.

        Shape depends on the tool, not this method: a ``.list`` tool's
        payload is ``{"data": [...], ...}``; a ``.get``/``.create``/
        ``.update``/transition tool's payload is the record itself. Both are
        confirmed live, not assumed -- know your tool's convention rather
        than expecting this to normalize it away.
        """
        tenant = self._tenant(jurisdiction)
        result = await self._mcp(tenant, "tools/call", {"name": name, "arguments": arguments or {}})
        for block in result.get("content") or []:
            if block.get("type") == "text":
                text = block.get("text", "")
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return {"text": text}
        return result

    async def list_tools(self, *, jurisdiction: Jurisdiction, refresh: bool = False) -> list[dict[str, Any]]:
        """The seat's scoped tool catalogue, each with its JSON Schema.

        Cached after the first fetch: this is capability metadata, not
        business data, so caching it does not violate the "re-read shared
        rows before acting" rule that applies to actual checklist records.
        """
        tenant = self._tenant(jurisdiction)
        if tenant.tools is None or refresh:
            result = await self._mcp(tenant, "tools/list", {})
            tenant.tools = result.get("tools", [])
        return tenant.tools

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()
