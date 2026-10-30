import pytest
from fastapi.testclient import TestClient

from app.agent import graph as agent_graph
from app.api import routes
from app.main import app
from app.n8n.client import N8nClient
from tests.conftest import log_in
from tests.fake_n8n import FakeN8n


@pytest.fixture
def n8n(execution, workflow):
    return FakeN8n(execution, workflow)


@pytest.fixture
def api(n8n, monkeypatch):
    def no_llm(cls):
        raise AssertionError("The raw error must not call the LLM.")

    monkeypatch.setattr(agent_graph.GeminiAgent, "__new__", no_llm)
    monkeypatch.setattr(
        routes, "build_client", lambda url, key: N8nClient(url, key, transport=n8n.transport)
    )
    with TestClient(app) as client:
        log_in(client)
        client.post("/connect", json={"n8n_url": "http://n8n:5678", "api_key": "key"})
        yield client


def url(n8n):
    return f"/workflows/{n8n.workflow_id}/error"


def test_returns_the_raw_node_and_message(api, n8n):
    response = api.get(url(n8n))

    assert response.status_code == 200
    assert response.json() == {
        "execution_id": "4",
        "failed_node": "Normalize Lead",
        "error_message": "Cannot read properties of undefined (reading 'toLowerCase') [line 1]",
    }
    assert "stack" not in response.text
    assert n8n.writes() == []


def test_returns_the_newest_unrecovered_failure(api, n8n):
    n8n.add_failure("5")
    n8n.executions["5"]["retrySuccessId"] = "6"
    n8n.add_failure("3")

    assert api.get(url(n8n)).json()["execution_id"] == "4"


def test_failed_retries_are_not_the_failure(api, n8n):
    n8n.add_failure("7")
    n8n.executions["7"]["retryOf"] = "4"

    assert api.get(url(n8n)).json()["execution_id"] == "4"


def test_no_unresolved_failures_is_a_404(api, n8n):
    n8n.executions["4"]["retrySuccessId"] = "9"

    response = api.get(url(n8n))

    assert response.status_code == 404
    assert "no unresolved failures" in response.json()["detail"]


def test_execution_without_error_details_is_reported(api, n8n):
    n8n.executions["4"]["data"]["resultData"] = {}

    assert api.get(url(n8n)).status_code == 422


def test_n8n_failure_is_reported(api, n8n):
    n8n.fail_on.add("GET /executions")

    assert api.get(url(n8n)).status_code == 502
