"""The demo account's "Simulate failure" buttons: put a known workflow back in its broken state."""

import asyncio
import copy
import logging
import math
import time
from dataclasses import dataclass
from typing import Any

from app import db
from app.n8n.client import N8nClient, is_unrecovered
from app.schemas import SimulateFailureResult
from app.services import repair
from app.services.workflows import is_ignored

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Scenario:
    node_name: str
    code: str
    payload: dict[str, Any]


# Keyed by workflow name. A workflow that isn't here has no known bug, so it can't be simulated.
SCENARIOS = {
    # The form now sends `Email`, so `body.email` is undefined.
    "Lead Capture": Scenario(
        node_name="Normalize Lead",
        code=(
            "return $input.all().map(item => ({\n"
            "  json: {\n"
            "    email: item.json.body.email.toLowerCase(),\n"
            "    name: item.json.body.name\n"
            "  }\n"
            "}));"
        ),
        payload={"Email": "test.lead@example.com", "name": "Test Lead"},
    ),
    # Orders now arrive without the `order` wrapper, so `body.order` is undefined.
    "Order Processing": Scenario(
        node_name="Calculate Total",
        code=(
            "return $input.all().map(item => {\n"
            "  const order = item.json.body.order;\n"
            "  const total = order.items.reduce((sum, i) => sum + i.price * i.qty, 0);\n"
            "  return { json: { customer: order.customer, total } };\n"
            "});"
        ),
        payload={"customer": "Meena", "items": [{"price": 299, "qty": 1}]},
    ),
}
WEBHOOK_NODE_TYPE = "n8n-nodes-base.webhook"
COOLDOWN_SECONDS = 120
# n8n can take a moment to register a webhook after activating. A 404 means nothing arrived.
WEBHOOK_ATTEMPTS = 3
WEBHOOK_RETRY_SECONDS = 1.0

# One shared demo account behind one backend process, so this lives in memory.
_last_runs: dict[str, float] = {}


class SimulationError(Exception):
    pass


class CoolingDown(Exception):
    def __init__(self, retry_after: int):
        super().__init__(f"This was run recently. Try again in {retry_after} seconds.")
        self.retry_after = retry_after


def can_simulate(workflow_name: str) -> bool:
    return workflow_name in SCENARIOS


def _reserve(workflow_id: str) -> None:
    now = time.monotonic()
    last_run = _last_runs.get(workflow_id)
    if last_run is not None and now - last_run < COOLDOWN_SECONDS:
        raise CoolingDown(math.ceil(COOLDOWN_SECONDS - (now - last_run)))
    # Set before the first await, so a double click can't get a second run in.
    _last_runs[workflow_id] = now


def _scenario(workflow: dict[str, Any]) -> Scenario:
    scenario = SCENARIOS.get(workflow.get("name", ""))
    if scenario is None or is_ignored(workflow):
        raise SimulationError(f'"{workflow.get("name")}" has no demo failure to simulate.')
    return scenario


def _webhook_path(workflow: dict[str, Any]) -> str:
    for node in workflow.get("nodes", []):
        if node.get("type") == WEBHOOK_NODE_TYPE and (node.get("parameters") or {}).get("path"):
            return node["parameters"]["path"]
    raise SimulationError(f"{workflow['name']} has no webhook to send a test request to.")


def _with_bug(workflow: dict[str, Any], scenario: Scenario) -> dict[str, Any]:
    broken = copy.deepcopy(workflow)
    for node in broken.get("nodes", []):
        if node.get("name") == scenario.node_name:
            node["parameters"] = {**(node.get("parameters") or {}), "jsCode": scenario.code}
            return broken
    raise SimulationError(f'{workflow["name"]} has no "{scenario.node_name}" node.')


def _reject_open_proposals(user_id: str, workflow_id: str) -> None:
    for proposal in db.list_open_proposals(user_id, workflow_id):
        try:
            repair.reject(proposal["id"], user_id)
        except repair.ProposalStateError:
            logger.info("Proposal %s was decided while simulating a failure.", proposal["id"])


async def _send_test_request(client: N8nClient, path: str, payload: dict[str, Any]) -> None:
    for attempt in range(WEBHOOK_ATTEMPTS):
        status = await client.trigger_webhook(path, payload)
        if status != 404:
            break
        if attempt < WEBHOOK_ATTEMPTS - 1:
            await asyncio.sleep(WEBHOOK_RETRY_SECONDS)
    # A 5xx is fine: a webhook set to answer from its last node reports the failure this causes.
    if 400 <= status < 500:
        raise SimulationError(f"n8n refused the test request (HTTP {status}).")


async def simulate_failure(
    user_id: str, workflow_id: str, client: N8nClient
) -> SimulateFailureResult:
    _reserve(workflow_id)
    try:
        # Everything that can be checked is checked first, so a bad setup changes nothing.
        workflow = await client.get_workflow(workflow_id)
        scenario = _scenario(workflow)
        path = _webhook_path(workflow)
        broken = _with_bug(workflow, scenario)

        _reject_open_proposals(user_id, workflow_id)
        await client.update_workflow(workflow_id, broken)
        await client.activate_workflow(workflow_id)

        failed = await client.list_executions(workflow_id, status="error")
        failed_count = sum(1 for execution in failed if is_unrecovered(execution))
        await _send_test_request(client, path, scenario.payload)
    except Exception:
        # A run that didn't reach n8n's webhook costs nothing, so it can be tried again straight away.
        _last_runs.pop(workflow_id, None)
        raise
    return SimulateFailureResult(
        workflow_id=workflow_id,
        workflow_name=workflow["name"],
        failed_count=failed_count,
        cooldown_seconds=COOLDOWN_SECONDS,
    )
