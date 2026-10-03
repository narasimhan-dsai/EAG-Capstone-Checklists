"""Implementation scaffolding. NOT the team's graded tests."""
from __future__ import annotations

import json

import httpx
import pytest

from checklist_agent.agentswitch import AgentSwitchClient, AgentSwitchToolError


def env(monkeypatch):
    monkeypatch.setenv("AGENTSWITCH_IN_BASE_URL", "http://as")
    monkeypatch.setenv("AGENTSWITCH_IN_EMAIL", "t@x.in")
    monkeypatch.setenv("AGENTSWITCH_IN_PASSWORD", "pw")
    for name in ("AGENTSWITCH_US_BASE_URL", "AGENTSWITCH_US_EMAIL", "AGENTSWITCH_US_PASSWORD"):
        monkeypatch.delenv(name, raising=False)


def client_for(handler):
    return AgentSwitchClient(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def text_result(payload):
    return {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": json.dumps(payload)}]}}


async def test_us_is_not_configured_and_in_is(monkeypatch):
    env(monkeypatch)
    client = client_for(lambda request: httpx.Response(200, json={}))
    assert client.configured("IN") and not client.configured("US")


async def test_a_json_rpc_error_with_http_200_raises(monkeypatch):
    env(monkeypatch)

    def handler(request):
        if request.url.path == "/api/auth/login":
            return httpx.Response(200, json={"token": "t1"})
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1,
                                         "error": {"code": -32602, "message": "bad argument"}})

    with pytest.raises(AgentSwitchToolError) as caught:
        await client_for(handler).call_tool("ChecklistRun.get", {"id": "x"}, jurisdiction="IN")
    assert caught.value.code == -32602 and "bad argument" in caught.value.message


async def test_payload_is_parsed_from_the_text_block(monkeypatch):
    env(monkeypatch)

    def handler(request):
        if request.url.path == "/api/auth/login":
            return httpx.Response(200, json={"token": "t1"})
        return httpx.Response(200, json=text_result({"data": [{"id": "a"}], "total": 1}))

    result = await client_for(handler).call_tool("ChecklistRun.list", {}, jurisdiction="IN")
    assert result == {"data": [{"id": "a"}], "total": 1}


async def test_an_expired_token_triggers_exactly_one_relogin(monkeypatch):
    env(monkeypatch)
    state = {"logins": 0, "mcp": 0}

    def handler(request):
        if request.url.path == "/api/auth/login":
            state["logins"] += 1
            return httpx.Response(200, json={"token": f"t{state['logins']}"})
        state["mcp"] += 1
        if state["mcp"] == 1:
            return httpx.Response(401, json={"detail": "expired"})
        return httpx.Response(200, json=text_result({"ok": True}))

    result = await client_for(handler).call_tool("X.get", {"id": "1"}, jurisdiction="IN")
    assert result == {"ok": True} and state["logins"] == 2
