"""The demo account's "Simulate a failure" button: puts Lead Capture back in its broken state."""

import asyncio
import copy
import logging
import math
import time
from typing import Any

from app import db
from app.n8n.client import N8nClient, is_unrecovered
from app.schemas import SimulateFailureResult
from app.services import repair

logger = logging.getLogger(__name__)

WORKFLOW_NAME = "Lead Capture"
NODE_NAME = "Normalize Lead"
WEBHOOK_NODE_TYPE = "n8n-nodes-base.webhook"
# The bug from SPEC.md: the form now sends `Email`, so `body.email` is undefined.
BUGGY_CODE = (
    "return $input.all().map(item => ({\n"
    "  json: {\n"
    "    email: item.json.body.email.toLowerCase(),\n"
    "    name: item.json.body.name\n"
    "  }\n"
    "}));"
)
TEST_LEAD = {"Email": "test.lead@example.com", "name": "Test Lead"}
COOLDOWN_SECONDS = 120
# n8n can take a moment to register a webhook after activating. A 404 means no lead arrived.
WEBHOOK_ATTEMPTS = 3
WEBHOOK_RETRY_SECONDS = 1.0

# One shared demo account behind one backend process, so this lives in memory.
_last_run: float | None = None


class SimulationError(Exception):
    pass


class CoolingDown(Exception):
    def __init__(self, retry_after: int):
        super().__init__(f"This was run recently. Try again in {retry_after} seconds.")
        self.retry_after = retry_after


def _reserve() -> None:
    global _last_run
    now = time.monotonic()
    if _last_run is not None and now - _last_run < COOLDOWN_SECONDS:
        raise CoolingDown(math.ceil(COOLDOWN_SECONDS - (now - _last_run)))
    # Set before the first await, so a double click can't get a second run in.
    _last_run = now


async def _find_workflow(client: N8nClient) -> dict[str, Any]:
    matches = [w for w in await client.list_workflows() if w.get("name") == WORKFLOW_NAME]
    if len(matches) != 1:
        raise SimulationError(f'Expected one workflow named "{WORKFLOW_NAME}", found {len(matches)}.')
    return await client.get_workflow(str(matches[0]["id"]))


def _webhook_path(workflow: dict[str, Any]) -> str:
    for node in workflow.get("nodes", []):
        if node.get("type") == WEBHOOK_NODE_TYPE and (node.get("parameters") or {}).get("path"):
            return node["parameters"]["path"]
    raise SimulationError(f"{WORKFLOW_NAME} has no webhook to send a test lead to.")


def _with_bug(workflow: dict[str, Any]) -> dict[str, Any]:
    broken = copy.deepcopy(workflow)
    for node in broken.get("nodes", []):
        if node.get("name") == NODE_NAME:
            node["parameters"] = {**(node.get("parameters") or {}), "jsCode": BUGGY_CODE}
            return broken
    raise SimulationError(f'{WORKFLOW_NAME} has no "{NODE_NAME}" node.')


def _reject_open_proposals(user_id: str, workflow_id: str) -> None:
    for proposal in db.list_open_proposals(user_id, workflow_id):
        try:
            repair.reject(proposal["id"], user_id)
        except repair.ProposalStateError:
            logger.info("Proposal %s was decided while simulating a failure.", proposal["id"])


async def _send_test_lead(client: N8nClient, path: str) -> None:
    for attempt in range(WEBHOOK_ATTEMPTS):
        status = await client.trigger_webhook(path, TEST_LEAD)
        if status != 404:
            break
        if attempt < WEBHOOK_ATTEMPTS - 1:
            await asyncio.sleep(WEBHOOK_RETRY_SECONDS)
    # A 5xx is fine: a webhook set to answer from its last node reports the failure this causes.
    if 400 <= status < 500:
        raise SimulationError(f"n8n refused the test lead (HTTP {status}).")


async def simulate_failure(user_id: str, client: N8nClient) -> SimulateFailureResult:
    global _last_run
    _reserve()
    try:
        # Everything that can be checked is checked first, so a bad setup changes nothing.
        workflow = await _find_workflow(client)
        workflow_id = str(workflow["id"])
        path = _webhook_path(workflow)
        broken = _with_bug(workflow)

        _reject_open_proposals(user_id, workflow_id)
        await client.update_workflow(workflow_id, broken)
        await client.activate_workflow(workflow_id)

        failed = await client.list_executions(workflow_id, status="error")
        failed_count = sum(1 for execution in failed if is_unrecovered(execution))
        await _send_test_lead(client, path)
    except Exception:
        # A run that didn't reach n8n's webhook costs nothing, so it can be tried again straight away.
        _last_run = None
        raise
    return SimulateFailureResult(
        workflow_id=workflow_id,
        workflow_name=WORKFLOW_NAME,
        failed_count=failed_count,
        cooldown_seconds=COOLDOWN_SECONDS,
    )
