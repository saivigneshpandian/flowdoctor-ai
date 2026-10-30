import json

import httpx
import pytest

from app.n8n.client import N8nClient, N8nError, is_unrecovered, normalize_base_url

API_KEY = "secret-key"


def client_for(handler):
    return N8nClient("http://n8n:5678/", API_KEY, transport=httpx.MockTransport(handler))


async def test_sends_auth_header_and_follows_cursor():
    seen = []

    def handler(request):
        seen.append(request)
        if "cursor" in request.url.params:
            return httpx.Response(200, json={"data": [{"id": "2"}], "nextCursor": None})
        return httpx.Response(200, json={"data": [{"id": "1"}], "nextCursor": "next"})

    workflows = await client_for(handler).list_workflows()

    assert [w["id"] for w in workflows] == ["1", "2"]
    assert seen[0].headers["X-N8N-API-KEY"] == API_KEY
    assert seen[0].url.path == "/api/v1/workflows"


async def test_list_executions_filters():
    def handler(request):
        assert request.url.params["status"] == "error"
        assert request.url.params["workflowId"] == "wf"
        assert request.url.params["limit"] == "5"
        return httpx.Response(200, json={"data": [{"id": "1"}]})

    assert await client_for(handler).list_executions("wf", "error", 5) == [{"id": "1"}]


async def test_get_execution_includes_data():
    def handler(request):
        assert request.url.params["includeData"] == "true"
        return httpx.Response(200, json={"id": "4"})

    assert (await client_for(handler).get_execution("4"))["id"] == "4"


async def test_update_sends_only_allowed_fields(workflow):
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={})

    await client_for(handler).update_workflow("wf", {**workflow, "versionId": "v"})

    assert set(bodies[0]) == {"name", "nodes", "connections", "settings"}


async def test_update_retries_with_execution_order_when_settings_rejected(workflow):
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        if len(bodies) == 1:
            return httpx.Response(
                400, json={"message": "request/body/settings must NOT have additional properties"}
            )
        return httpx.Response(200, json={})

    await client_for(handler).update_workflow("wf", workflow)

    assert len(bodies) == 2
    assert bodies[1]["settings"] == {"executionOrder": "v1"}


async def test_update_does_not_retry_other_errors(workflow):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(400, json={"message": "nodes are invalid"})

    with pytest.raises(N8nError, match="nodes are invalid"):
        await client_for(handler).update_workflow("wf", workflow)
    assert len(calls) == 1


async def test_retry_sends_load_workflow():
    def handler(request):
        assert request.url.path == "/api/v1/executions/4/retry"
        assert json.loads(request.content) == {"loadWorkflow": True}
        return httpx.Response(200, json={"id": 9})

    assert await client_for(handler).retry_execution("4") == "9"


async def test_retry_without_new_id_is_an_error():
    client = client_for(lambda request: httpx.Response(200, json={}))

    with pytest.raises(N8nError, match="did not return"):
        await client.retry_execution("4")


async def test_auth_failure_is_clear_and_does_not_leak_the_key():
    client = client_for(lambda request: httpx.Response(401, json={"message": "unauthorized"}))

    with pytest.raises(N8nError) as caught:
        await client.list_workflows()
    assert "rejected the API key" in caught.value.message
    assert API_KEY not in caught.value.message


async def test_timeout_and_network_errors_are_clear():
    def timeout(request):
        raise httpx.ReadTimeout("slow", request=request)

    def down(request):
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(N8nError, match="timed out"):
        await client_for(timeout).list_workflows()
    with pytest.raises(N8nError, match="Could not reach"):
        await client_for(down).list_workflows()


def test_normalize_base_url():
    assert normalize_base_url(" http://n8n:5678/api/v1/ ") == "http://n8n:5678"


def test_is_unrecovered():
    assert is_unrecovered({"retrySuccessId": None, "retryOf": None})
    assert not is_unrecovered({"retrySuccessId": "9", "retryOf": None})
    assert not is_unrecovered({"retrySuccessId": None, "retryOf": "4"})
