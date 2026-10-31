import copy
import json
from typing import Any

import httpx


class FakeN8n:
    """An in-memory n8n behind httpx.MockTransport, with the real API's response shapes."""

    def __init__(self, execution: dict[str, Any], workflow: dict[str, Any]):
        self.workflow_id = workflow["id"]
        self.workflow = copy.deepcopy(workflow)
        self.execution = execution
        self.executions = {
            str(execution["id"]): {**execution, "retryOf": None, "retrySuccessId": None}
        }
        self.calls: list[tuple[str, str]] = []
        self.puts: list[dict[str, Any]] = []
        self.retry_outcome = "success"
        self.fail_on: set[str] = set()
        # Shown in the workflow list only. Their executions aren't modelled.
        self.extra_workflows: list[dict[str, Any]] = []
        self.webhooks: list[dict[str, Any]] = []
        self.webhook_statuses: list[int] = []
        self.failures_from_webhooks = 0
        self._next_id = 100

    def add_failure(self, execution_id: str) -> None:
        self.executions[execution_id] = {
            **self.execution,
            "id": execution_id,
            "retryOf": None,
            "retrySuccessId": None,
        }

    def writes(self) -> list[tuple[str, str]]:
        return [call for call in self.calls if call[0] in ("PUT", "POST")]

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        method = request.method
        path = request.url.path.removeprefix("/api/v1")
        self.calls.append((method, path))
        if f"{method} {path}" in self.fail_on:
            return httpx.Response(500, json={"message": "boom"})

        if method == "POST" and path.startswith("/webhook/"):
            return self._webhook(request)

        if method == "GET" and path == "/workflows":
            return httpx.Response(200, json={"data": [self.workflow, *self.extra_workflows], "nextCursor": None})
        if method == "GET" and path == f"/workflows/{self.workflow_id}":
            return httpx.Response(200, json=self.workflow)
        if method == "PUT" and path == f"/workflows/{self.workflow_id}":
            body = json.loads(request.content)
            self.puts.append(body)
            self.workflow = {**self.workflow, **body}
            return httpx.Response(200, json=self.workflow)
        if method == "POST" and path == f"/workflows/{self.workflow_id}/activate":
            return httpx.Response(200, json=self.workflow)
        if method == "GET" and path == "/executions":
            items = list(self.executions.values())
            status = request.url.params.get("status")
            if status:
                items = [e for e in items if e["status"] == status]
            items.sort(key=lambda e: int(e["id"]), reverse=True)
            limit = int(request.url.params.get("limit", 250))
            summaries = [{k: v for k, v in e.items() if k != "data"} for e in items[:limit]]
            return httpx.Response(200, json={"data": summaries, "nextCursor": None})
        if method == "POST" and path.endswith("/retry"):
            return self._retry(path.split("/")[2], json.loads(request.content))
        if method == "GET" and path.startswith("/executions/"):
            found = self.executions.get(path.split("/")[2])
            if found is None:
                return httpx.Response(404, json={"message": "not found"})
            return httpx.Response(200, json=found)
        return httpx.Response(404, json={"message": f"unexpected {method} {path}"})

    def _webhook(self, request: httpx.Request) -> httpx.Response:
        self.webhooks.append(
            {"body": request.content, "api_key": request.headers.get("X-N8N-API-KEY")}
        )
        status = self.webhook_statuses.pop(0) if self.webhook_statuses else 200
        if status != 404:
            # A delivered lead runs the workflow, which fails on the capital E.
            self.failures_from_webhooks += 1
            self.add_failure(str(self._next_id))
            self._next_id += 1
        return httpx.Response(status, json={"message": "Workflow was started"})

    def _retry(self, execution_id: str, body: dict[str, Any]) -> httpx.Response:
        assert body == {"loadWorkflow": True}
        new_id = str(self._next_id)
        self._next_id += 1
        self.executions[new_id] = {
            "id": new_id,
            "status": self.retry_outcome,
            "retryOf": execution_id,
            "retrySuccessId": None,
            "startedAt": "2026-10-30T11:00:00.000Z",
            "workflowId": self.workflow_id,
        }
        if self.retry_outcome == "success":
            self.executions[execution_id]["retrySuccessId"] = new_id
        return httpx.Response(200, json={"id": new_id})
