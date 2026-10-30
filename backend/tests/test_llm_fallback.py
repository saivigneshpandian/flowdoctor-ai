import json
import logging

import httpx
import langchain_groq
import pytest

from app.agent import prompts
from app.agent.graph import GeminiAgent, LLMError, run_agent
from app.config import get_settings
from app.n8n.client import N8nClient
from app.schemas import Diagnosis, ManualSteps, PatchProposal
from tests.conftest import TEST_USER
from tests.fake_n8n import FakeN8n

GEMINI_KEY = "AQ.gemini-secret"
GROQ_KEY = "gsk_groq-secret"
GROQ_MODEL = "groq-test-model"
GEMINI_ANSWER = Diagnosis(
    root_cause="from gemini", category="auto_fixable", impact="i", confidence=0.5, recommendation="x"
)
GROQ_ANSWER = GEMINI_ANSWER.model_copy(update={"root_cause": "from groq"})


class ApiFailure(Exception):
    def __init__(self, code, status="STATUS", message="failed"):
        super().__init__(message)
        self.code = code
        self.status = status


class FakeProvider:
    """Records each request and plays back scripted outcomes."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.schemas = []
        self.requests = []

    def with_structured_output(self, schema):
        self.schemas.append(schema)
        return self

    async def ainvoke(self, messages):
        self.requests.append(messages)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def configure(monkeypatch, groq_key=GROQ_KEY, groq_model=GROQ_MODEL):
    monkeypatch.setenv("GEMINI_API_KEY", GEMINI_KEY)
    monkeypatch.setenv("GROQ_API_KEY", groq_key)
    monkeypatch.setenv("GROQ_MODEL", groq_model)
    get_settings.cache_clear()
    monkeypatch.setattr(GeminiAgent, "_thinking_levels", {})


def providers(monkeypatch, agent, gemini, groq):
    fake_gemini, fake_groq = FakeProvider(gemini), FakeProvider(groq)
    monkeypatch.setattr(agent, "_build_model", lambda level: fake_gemini)
    monkeypatch.setattr(agent, "_build_groq_model", lambda: fake_groq)
    return fake_gemini, fake_groq


def timeout_wrapped_by_sdk():
    try:
        try:
            raise httpx.ReadTimeout("slow")
        except httpx.ReadTimeout as inner:
            raise RuntimeError("wrapped") from inner
    except RuntimeError as outer:
        return outer


@pytest.mark.parametrize(
    "failure",
    [
        ApiFailure(429, "RESOURCE_EXHAUSTED"),
        ApiFailure(503, "UNAVAILABLE"),
        ApiFailure(504, "DEADLINE_EXCEEDED"),
        httpx.ReadTimeout("slow"),
        TimeoutError(),
        timeout_wrapped_by_sdk(),
    ],
    ids=["429", "503", "504", "httpx-timeout", "timeout-error", "wrapped-timeout"],
)
async def test_falls_back_to_groq_once(monkeypatch, failure):
    configure(monkeypatch)
    agent = GeminiAgent()
    gemini, groq = providers(monkeypatch, agent, [failure], [GROQ_ANSWER])

    assert await agent._ask(Diagnosis, "the prompt") == GROQ_ANSWER
    assert len(gemini.requests) == 1
    assert len(groq.requests) == 1


async def test_groq_gets_the_same_request_and_schema(monkeypatch):
    configure(monkeypatch)
    agent = GeminiAgent()
    gemini, groq = providers(monkeypatch, agent, [ApiFailure(503)], [GROQ_ANSWER])

    await agent._ask(Diagnosis, "the prompt")

    assert groq.requests == gemini.requests
    assert groq.schemas == gemini.schemas == [Diagnosis]
    assert groq.requests[0] == [("system", prompts.SYSTEM), ("human", "the prompt")]


@pytest.mark.parametrize(
    "failure",
    [ApiFailure(400), ApiFailure(401), ApiFailure(404), ValueError("bad output")],
    ids=["400", "401", "404", "other"],
)
async def test_other_errors_do_not_fall_back(monkeypatch, failure):
    configure(monkeypatch)
    agent = GeminiAgent()
    gemini, groq = providers(monkeypatch, agent, [failure], [GROQ_ANSWER])

    with pytest.raises(LLMError):
        await agent._ask(Diagnosis, "p")
    assert len(gemini.requests) == 1
    assert groq.requests == []


async def test_gemini_success_never_calls_groq(monkeypatch, caplog):
    configure(monkeypatch)
    agent = GeminiAgent()
    _, groq = providers(monkeypatch, agent, [GEMINI_ANSWER], [])

    with caplog.at_level(logging.INFO):
        assert await agent._ask(Diagnosis, "p") == GEMINI_ANSWER

    assert groq.requests == []
    assert "LLM call ok: provider=gemini" in caplog.text
    assert "provider=groq" not in caplog.text


async def test_groq_failure_is_reported_and_not_retried(monkeypatch):
    configure(monkeypatch)
    agent = GeminiAgent()
    gemini, groq = providers(monkeypatch, agent, [ApiFailure(503)], [ApiFailure(500), GROQ_ANSWER])

    with pytest.raises(LLMError, match="Groq fallback failed"):
        await agent._ask(Diagnosis, "p")
    assert len(gemini.requests) == len(groq.requests) == 1


@pytest.mark.parametrize(
    ("key", "model"), [("", ""), (GROQ_KEY, ""), ("", GROQ_MODEL)], ids=["none", "no-model", "no-key"]
)
async def test_without_full_groq_config_gemini_retries_itself(monkeypatch, key, model):
    configure(monkeypatch, groq_key=key, groq_model=model)
    agent = GeminiAgent()
    gemini, groq = providers(monkeypatch, agent, [ApiFailure(503), GEMINI_ANSWER], [])

    assert await agent._ask(Diagnosis, "p") == GEMINI_ANSWER
    assert len(gemini.requests) == 2
    assert groq.requests == []


async def test_logs_show_the_provider_and_never_the_keys(monkeypatch, caplog):
    configure(monkeypatch)
    agent = GeminiAgent()
    failure = ApiFailure(429, "RESOURCE_EXHAUSTED", f"quota for {GEMINI_KEY} and {GROQ_KEY}")
    providers(monkeypatch, agent, [failure], [GROQ_ANSWER])

    with caplog.at_level(logging.INFO):
        await agent._ask(Diagnosis, "p")

    assert "LLM call failed: provider=gemini" in caplog.text
    assert "code=429 status=RESOURCE_EXHAUSTED" in caplog.text
    assert f"LLM call ok: provider=groq model={GROQ_MODEL}" in caplog.text
    assert GEMINI_KEY not in caplog.text
    assert GROQ_KEY not in caplog.text


@pytest.mark.parametrize(
    ("schema", "arguments"),
    [
        (
            Diagnosis,
            {
                "root_cause": "r",
                "category": "auto_fixable",
                "impact": "i",
                "confidence": 0.9,
                "recommendation": "x",
            },
        ),
        (PatchProposal, {"parameters_json": "{}", "explanation": "e"}),
        (ManualSteps, {"title": "t", "steps": ["one"]}),
    ],
)
async def test_real_groq_client_parses_every_schema(monkeypatch, schema, arguments):
    """The real ChatGroq against a mocked HTTP layer, so the schemas and parsing are exercised."""
    configure(monkeypatch)
    sent = []

    def handler(request):
        sent.append(request)
        return httpx.Response(
            200,
            json={
                "id": "1",
                "object": "chat.completion",
                "created": 1,
                "model": GROQ_MODEL,
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": schema.__name__,
                                        "arguments": json.dumps(arguments),
                                    },
                                }
                            ],
                        },
                    }
                ],
            },
        )

    real = langchain_groq.ChatGroq
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(
        langchain_groq, "ChatGroq", lambda **kwargs: real(**kwargs, http_async_client=http_client)
    )
    agent = GeminiAgent()
    monkeypatch.setattr(agent, "_build_model", lambda level: FakeProvider([ApiFailure(503)]))

    answer = await agent._ask(schema, "the prompt")

    assert answer == schema(**arguments)
    body = json.loads(sent[0].content)
    assert body["model"] == GROQ_MODEL
    assert body["messages"][0]["content"] == prompts.SYSTEM
    assert body["tools"][0]["function"]["name"] == schema.__name__
    assert len(sent) == 1


async def test_agent_builds_a_proposal_when_gemini_is_rate_limited(monkeypatch, execution, workflow):
    """A full diagnosis through the graph, served by Groq. n8n is never written to."""
    configure(monkeypatch)
    fake_n8n = FakeN8n(execution, workflow)
    client = N8nClient("http://n8n:5678", "key", transport=fake_n8n.transport)
    code = (
        "return $input.all().map(item => {\n"
        "  const email = item.json.body.email ?? item.json.body.Email;\n"
        "  if (!email) throw new Error('Lead is missing an email');\n"
        "  return { json: { email: email.toLowerCase(), name: item.json.body.name } };\n"
        "});"
    )
    patch = PatchProposal(parameters_json=json.dumps({"jsCode": code}), explanation="e")
    gemini = FakeProvider([ApiFailure(429), ApiFailure(429)])
    groq = FakeProvider([GROQ_ANSWER, patch])
    monkeypatch.setattr(GeminiAgent, "_build_model", lambda self, level: gemini)
    monkeypatch.setattr(GeminiAgent, "_build_groq_model", lambda self: groq)

    state = await run_agent(client, TEST_USER, "Why did my lead capture workflow fail?")

    assert [m["type"] for m in state["messages"]] == ["diagnosis", "proposal"]
    assert state["messages"][0]["root_cause"] == "from groq"
    assert fake_n8n.writes() == []
