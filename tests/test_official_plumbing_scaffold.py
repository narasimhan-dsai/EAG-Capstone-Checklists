"""Implementation scaffolding. NOT the team's graded tests."""
from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from checklist_agent.agentswitch import AgentSwitchClient, AgentSwitchError
from checklist_agent.gateway import GatewayClient
from checklist_agent.llm import OpenAIClient, make_llm


# --- the platform's model: OpenAI-compatible, configured only by environment variables
class FakeCompletions:
    def __init__(self, content="hello", model="served-model"):
        self.calls, self.content, self.model = [], content, model

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(model=self.model, usage=SimpleNamespace(prompt_tokens=11, completion_tokens=3),
                               choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))])


def fake_client(**kwargs):
    completions = FakeCompletions(**kwargs)
    return SimpleNamespace(chat=SimpleNamespace(completions=completions), close=lambda: None), completions


async def test_a_completion_sends_system_and_user_messages_and_returns_the_gateways_shape():
    client, completions = fake_client()
    llm = OpenAIClient(client=client, model="m1")
    out = await llm.complete("the question", "the rules", request={"max_tokens": 321, "provider": "ignored"})
    [call] = completions.calls
    assert call["model"] == "m1" and call["temperature"] == 0 and call["max_tokens"] == 321
    assert call["messages"] == [{"role": "system", "content": "the rules"}, {"role": "user", "content": "the question"}]
    assert out == {"text": "hello", "provider": "openai-compatible", "model": "served-model",
                   "input_tokens": 11, "output_tokens": 3}


async def test_an_empty_or_missing_reply_becomes_empty_text_not_a_crash():
    client, _ = fake_client(content=None)
    assert (await OpenAIClient(client=client, model="m").complete("q", "s"))["text"] == ""


def test_the_model_name_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "platform-model")
    assert OpenAIClient(client=fake_client()[0]).model == "platform-model"
    monkeypatch.delenv("OPENAI_MODEL")
    assert OpenAIClient(client=fake_client()[0]).model == "agentswitch-default"


def test_the_platform_model_is_used_when_its_variables_are_set_and_the_gateway_otherwise(monkeypatch):
    for name in ("OPENAI_BASE_URL", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    assert isinstance(make_llm(), GatewayClient)
    monkeypatch.setenv("OPENAI_BASE_URL", "http://model.invalid/v1")
    assert isinstance(make_llm(), GatewayClient)                    # a base URL alone is not enough
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    assert isinstance(make_llm(), OpenAIClient)


# --- the seat token: no password, no login
def token_env(monkeypatch, instance="suryodaya"):
    monkeypatch.setenv("AGENTSWITCH_BASE_URL", "https://as.invalid/")
    monkeypatch.setenv("AGENTSWITCH_TOKEN", "seat-token")
    monkeypatch.setenv("AGENTSWITCH_INSTANCE", instance)
    for side in ("IN", "US"):                       # the real .env may be loaded by another test's import
        for part in ("BASE_URL", "EMAIL", "PASSWORD"):
            monkeypatch.delenv(f"AGENTSWITCH_{side}_{part}", raising=False)


def client_for(handler):
    return AgentSwitchClient(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


async def test_the_seat_token_is_sent_as_a_bearer_and_there_is_never_a_login(monkeypatch):
    token_env(monkeypatch)
    seen = []

    def handler(request):
        seen.append((request.url.path, request.headers.get("authorization")))
        body = {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": json.dumps({"ok": 1})}]}}
        return httpx.Response(200, json=body)

    client = client_for(handler)
    assert client.configured("IN") and not client.configured("US")
    assert await client.call_tool("X.get", {"id": "1"}, jurisdiction="IN") == {"ok": 1}
    assert seen == [("/api/mcp", "Bearer seat-token")]


async def test_the_keystone_instance_maps_to_the_us_slot(monkeypatch):
    token_env(monkeypatch, instance="keystone")
    client = client_for(lambda request: httpx.Response(200, json={}))
    assert client.configured("US") and not client.configured("IN")


async def test_a_refused_seat_token_says_so_instead_of_trying_to_log_in(monkeypatch):
    token_env(monkeypatch)
    paths = []

    def handler(request):
        paths.append(request.url.path)
        return httpx.Response(401, json={"detail": "expired"})

    with pytest.raises(AgentSwitchError) as caught:
        await client_for(handler).call_tool("X.get", {"id": "1"}, jurisdiction="IN")
    assert "seat token was refused" in str(caught.value) and "/api/auth/login" not in paths
