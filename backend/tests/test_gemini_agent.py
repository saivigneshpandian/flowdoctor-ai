import logging
import os

import pytest

from app.agent.graph import GeminiAgent, LLMError
from app.config import get_settings
from app.schemas import Diagnosis

KEY = "AQ.secret-key-value"
ANSWER = Diagnosis(
    root_cause="r", category="auto_fixable", impact="i", confidence=0.5, recommendation="x"
)


class ApiFailure(Exception):
    def __init__(self, code, status, message="failed"):
        super().__init__(message)
        self.code = code
        self.status = status


class FakeModel:
    def __init__(self, outcomes, levels, level):
        self.outcomes = outcomes
        self.levels = levels
        self.level = level

    def with_structured_output(self, schema):
        return self

    async def ainvoke(self, messages):
        self.levels.append(self.level)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture
def agent(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", KEY)
    get_settings.cache_clear()
    monkeypatch.setattr(GeminiAgent, "_thinking_levels", {})
    return GeminiAgent()


def script(monkeypatch, agent, outcomes):
    levels = []
    monkeypatch.setattr(agent, "_build_model", lambda level: FakeModel(outcomes, levels, level))
    return levels


@pytest.mark.parametrize("code", [503, 504])
async def test_retries_once_on_503_and_504(agent, monkeypatch, code):
    levels = script(monkeypatch, agent, [ApiFailure(code, "UNAVAILABLE"), ANSWER])

    assert await agent._ask(Diagnosis, "p") == ANSWER
    assert levels == ["minimal", "minimal"]


async def test_gives_up_after_one_retry(agent, monkeypatch):
    levels = script(
        monkeypatch, agent, [ApiFailure(504, "DEADLINE_EXCEEDED"), ApiFailure(504, "DEADLINE_EXCEEDED")]
    )

    with pytest.raises(LLMError, match="HTTP 504"):
        await agent._ask(Diagnosis, "p")
    assert len(levels) == 2


async def test_does_not_retry_other_errors(agent, monkeypatch):
    levels = script(monkeypatch, agent, [ApiFailure(404, "NOT_FOUND"), ANSWER])

    with pytest.raises(LLMError):
        await agent._ask(Diagnosis, "p")
    assert len(levels) == 1


async def test_falls_back_to_low_when_minimal_is_unsupported_and_remembers(agent, monkeypatch):
    unsupported = ApiFailure(400, "INVALID_ARGUMENT", "Thinking level MINIMAL is not supported")
    levels = script(monkeypatch, agent, [unsupported, ANSWER, ANSWER])

    await agent._ask(Diagnosis, "p")
    await agent._ask(Diagnosis, "p")

    assert levels == ["minimal", "low", "low"]


async def test_failure_log_has_full_details_but_never_the_key(agent, monkeypatch, caplog):
    failure = ApiFailure(404, "NOT_FOUND", f"model missing for key {KEY}")
    script(monkeypatch, agent, [failure])

    with caplog.at_level(logging.INFO), pytest.raises(LLMError):
        await agent._ask(Diagnosis, "p")

    assert get_settings().gemini_model in caplog.text
    assert "elapsed=" in caplog.text
    assert "type=ApiFailure code=404 status=NOT_FOUND" in caplog.text
    assert "model missing" in caplog.text
    assert KEY not in caplog.text


async def test_success_log_has_model_and_elapsed(agent, monkeypatch, caplog):
    script(monkeypatch, agent, [ANSWER])

    with caplog.at_level(logging.INFO):
        await agent._ask(Diagnosis, "p")

    assert "LLM call ok: provider=gemini" in caplog.text
    assert "elapsed=" in caplog.text


def test_real_client_accepts_the_settings(agent):
    model = agent._build_model("minimal")

    assert model.reasoning_effort == "minimal"
    assert model.timeout == 60
    assert KEY not in repr(model)
    assert os.environ["GEMINI_API_KEY"] == KEY
