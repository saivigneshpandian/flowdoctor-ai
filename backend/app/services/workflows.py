import asyncio
from typing import Any

from app import db
from app.config import get_settings
from app.n8n.client import N8nClient, is_unrecovered
from app.schemas import Workflow

MAX_CONCURRENT = 5


def is_ignored(workflow: dict[str, Any]) -> bool:
    ignore_tag = get_settings().ignore_tag.strip().casefold()
    if not ignore_tag:
        return False
    tags = workflow.get("tags") or []
    return any(
        isinstance(tag, dict) and str(tag.get("name", "")).strip().casefold() == ignore_tag
        for tag in tags
    )


async def list_visible_workflows(client: N8nClient) -> list[dict[str, Any]]:
    return [workflow for workflow in await client.list_workflows() if not is_ignored(workflow)]


async def list_workflow_health(client: N8nClient, user_id: str) -> list[Workflow]:
    workflows = await list_visible_workflows(client)
    pending = db.open_proposal_workflow_ids(user_id)
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    async def health(workflow: dict[str, Any]) -> Workflow:
        workflow_id = str(workflow["id"])
        async with semaphore:
            failed = await client.list_executions(workflow_id, status="error")
            latest = await client.list_executions(workflow_id, limit=1)
        failed_count = sum(1 for execution in failed if is_unrecovered(execution))
        if workflow_id in pending:
            status = "pending"
        elif failed_count > 0:
            status = "failing"
        else:
            status = "healthy"
        return Workflow(
            id=workflow_id,
            name=workflow.get("name", "Untitled"),
            status=status,
            last_execution_at=latest[0].get("startedAt") if latest else None,
            failed_count=failed_count,
        )

    return list(await asyncio.gather(*(health(workflow) for workflow in workflows)))
