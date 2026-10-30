from typing import Any

import httpx

TIMEOUT = httpx.Timeout(15.0, connect=5.0)
MAX_PAGE_SIZE = 250


class N8nError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.message = message
        self.status = status


def normalize_base_url(url: str) -> str:
    base = url.strip().rstrip("/")
    return base.removesuffix("/api/v1")


def is_unrecovered(execution: dict[str, Any]) -> bool:
    """An original failure with no successful retry. Failed retries aren't separate failures."""
    return not execution.get("retrySuccessId") and not execution.get("retryOf")


class N8nClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._http = httpx.AsyncClient(
            base_url=f"{normalize_base_url(base_url)}/api/v1",
            headers={"X-N8N-API-KEY": api_key},
            timeout=TIMEOUT,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        action = f"{method} {path}"
        try:
            response = await self._http.request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            raise N8nError(f"n8n timed out on {action}.") from exc
        except httpx.HTTPError as exc:
            raise N8nError(f"Could not reach n8n on {action}.") from exc

        if response.status_code in (401, 403):
            raise N8nError("n8n rejected the API key.", response.status_code)
        if response.is_error:
            raise N8nError(
                f"n8n returned HTTP {response.status_code} on {action}: {_error_text(response)}",
                response.status_code,
            )
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise N8nError(f"n8n returned an unreadable response on {action}.") from exc

    async def list_workflows(self) -> list[dict[str, Any]]:
        return await self._paged("/workflows", {"limit": MAX_PAGE_SIZE})

    async def get_workflow(self, workflow_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/workflows/{workflow_id}")

    async def list_executions(
        self,
        workflow_id: str,
        status: str | None = None,
        limit: int = MAX_PAGE_SIZE,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"workflowId": workflow_id, "limit": limit}
        if status:
            params["status"] = status
        data = await self._request("GET", "/executions", params=params)
        return data.get("data", [])

    async def get_execution(self, execution_id: str, include_data: bool = True) -> dict[str, Any]:
        params = {"includeData": "true"} if include_data else None
        return await self._request("GET", f"/executions/{execution_id}", params=params)

    async def update_workflow(self, workflow_id: str, workflow: dict[str, Any]) -> None:
        settings = workflow.get("settings") or {}
        body = {
            "name": workflow["name"],
            "nodes": workflow["nodes"],
            "connections": workflow["connections"],
            "settings": settings,
        }
        try:
            await self._request("PUT", f"/workflows/{workflow_id}", json=body)
        except N8nError as exc:
            if exc.status != 400 or "settings" not in exc.message.lower():
                raise
            order = settings.get("executionOrder")
            body["settings"] = {"executionOrder": order} if order else {}
            await self._request("PUT", f"/workflows/{workflow_id}", json=body)

    async def activate_workflow(self, workflow_id: str) -> None:
        await self._request("POST", f"/workflows/{workflow_id}/activate")

    async def retry_execution(self, execution_id: str) -> str:
        data = await self._request(
            "POST", f"/executions/{execution_id}/retry", json={"loadWorkflow": True}
        )
        new_id = data.get("id") if isinstance(data, dict) else data
        if new_id is None:
            raise N8nError(f"n8n did not return a new execution id for retry of {execution_id}.")
        return str(new_id)

    async def _paged(self, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        cursor = None
        while True:
            page = {**params, **({"cursor": cursor} if cursor else {})}
            data = await self._request("GET", path, params=page)
            if not isinstance(data, dict):
                raise N8nError(f"n8n returned an unexpected response on GET {path}.")
            items.extend(data.get("data", []))
            cursor = data.get("nextCursor")
            if not cursor:
                return items


def _error_text(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:200]
    message = body.get("message") if isinstance(body, dict) else None
    return str(message or body)[:200]
