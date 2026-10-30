import asyncio
from typing import Any

from app import db
from app.n8n.client import N8nClient, is_unrecovered
from app.schemas import Workflow

MAX_CONCURRENT = 5


async def list_workflow_health(client: N8nClient, user_id: str) -> list[Workflow]:
    workflows = await client.list_workflows()
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
