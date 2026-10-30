"""Applies approved fixes. Deterministic: no LLM runs on this path."""

import asyncio
import logging
import time
from typing import Any

from app import db
from app.agent.validate import ValidationError, apply_parameters, validate_patch
from app.n8n.client import N8nClient, N8nError
from app.schemas import RepairResult

logger = logging.getLogger(__name__)

POLL_TIMEOUT = 30.0
POLL_INTERVAL = 1.0
TERMINAL_STATUSES = {"success", "error", "crashed", "canceled"}


class ProposalNotFound(Exception):
    pass


class ProposalStateError(Exception):
    pass


async def approve(proposal_id: str, user_id: str, client: N8nClient) -> RepairResult:
    proposal = _claim(proposal_id, user_id, "patch")
    backup = proposal["backup_workflow"]
    workflow_id = proposal["workflow_id"]

    try:
        current = await client.get_workflow(workflow_id)
    except N8nError as exc:
        db.set_proposal_status(proposal_id, "open")
        return _failed(f"{exc.message} Nothing was changed.", "not_run")
    if not _same_definition(current, backup):
        db.set_proposal_status(proposal_id, "failed")
        return _failed(
            "The workflow was edited after this fix was proposed, so it was not applied. "
            "Nothing was changed. Ask for a new diagnosis.",
            "not_run",
        )

    try:
        patched = apply_parameters(backup, proposal["node_name"], proposal["parameters"])
        validate_patch(backup, patched, proposal["node_name"])
        await client.update_workflow(workflow_id, patched)
        # An inactive workflow has no live version to update, and approving a fix must not turn it on.
        if backup.get("active") is not False:
            await client.activate_workflow(workflow_id)
    except (N8nError, ValidationError) as exc:
        detail = exc.message if isinstance(exc, N8nError) else str(exc)
        db.set_proposal_status(proposal_id, "failed")
        rollback = await _rollback(client, proposal)
        return _failed(f"The fix could not be applied: {detail} {rollback}", "not_run")

    recovered, total, execution_status = await _retry_and_verify(
        client, proposal["affected_execution_ids"]
    )
    if recovered < total:
        db.set_proposal_status(proposal_id, "failed")
        rollback = await _rollback(client, proposal)
        return RepairResult(
            repair_status="failed",
            execution_status=execution_status,
            recovered_count=recovered,
            verified=False,
            message=f"{recovered} of {total} executions recovered. {rollback}",
        )

    db.set_proposal_status(proposal_id, "approved")
    db.resolve_incident(proposal["incident_id"], "approved")
    return _success(recovered, total, execution_status, "Fix applied.")


async def complete_manual(proposal_id: str, user_id: str, client: N8nClient) -> RepairResult:
    proposal = _claim(proposal_id, user_id, "manual")
    recovered, total, execution_status = await _retry_and_verify(
        client, proposal["affected_execution_ids"]
    )
    if recovered < total:
        # The manual step may be unfinished, so the user can try again.
        db.set_proposal_status(proposal_id, "open")
        return RepairResult(
            repair_status="failed",
            execution_status=execution_status,
            recovered_count=recovered,
            verified=False,
            message=f"{recovered} of {total} executions recovered. Check the steps and try again.",
        )

    db.set_proposal_status(proposal_id, "completed")
    db.resolve_incident(proposal["incident_id"], "manual")
    return _success(recovered, total, execution_status, "Manual fix confirmed.")


def reject(proposal_id: str, user_id: str) -> None:
    proposal = _claim(proposal_id, user_id, None)
    db.set_proposal_status(proposal_id, "rejected")
    db.resolve_incident(proposal["incident_id"], "rejected")


def _claim(proposal_id: str, user_id: str, kind: str | None) -> dict[str, Any]:
    # Another user's proposal looks exactly like one that doesn't exist.
    proposal = db.get_proposal(proposal_id, user_id)
    if proposal is None:
        raise ProposalNotFound(f"Proposal {proposal_id} was not found.")
    if kind and proposal["kind"] != kind:
        action = "approved" if kind == "patch" else "marked as done"
        raise ProposalStateError(f"This {proposal['kind']} proposal can't be {action}.")
    if not db.claim_proposal(proposal_id):
        raise ProposalStateError(f"This proposal is already {db.get_proposal(proposal_id, user_id)['status']}.")
    return proposal


def _same_definition(current: dict[str, Any], backup: dict[str, Any]) -> bool:
    return current.get("nodes") == backup.get("nodes") and current.get(
        "connections"
    ) == backup.get("connections")


async def _rollback(client: N8nClient, proposal: dict[str, Any]) -> str:
    backup = proposal["backup_workflow"]
    try:
        await client.update_workflow(proposal["workflow_id"], backup)
        if backup.get("active") is not False:
            await client.activate_workflow(proposal["workflow_id"])
    except N8nError as exc:
        logger.error("Rollback failed for proposal %s: %s", proposal["id"], exc.message)
        return (
            f"ROLLBACK FAILED: {exc.message} The workflow may still contain the patch, "
            "so check it in n8n."
        )
    return "The original workflow was restored."


async def _retry_and_verify(
    client: N8nClient, execution_ids: list[str]
) -> tuple[int, int, str]:
    """Returns (recovered, attempted, execution_status), all as reported by n8n."""
    statuses: list[str] = []
    new_ids: list[str] = []
    for execution_id in execution_ids:
        try:
            summary = await client.get_execution(execution_id, include_data=False)
            if summary.get("retrySuccessId"):
                continue  # Already recovered: rerunning would process the data twice.
            new_ids.append(await client.retry_execution(execution_id))
        except N8nError as exc:
            logger.warning("Retry of execution %s failed: %s", execution_id, exc.message)
            statuses.append("retry_failed")

    statuses += await asyncio.gather(*(_poll(client, new_id) for new_id in new_ids))
    recovered = statuses.count("success")
    return recovered, len(statuses), _summarize(statuses)


async def _poll(client: N8nClient, execution_id: str) -> str:
    deadline = time.monotonic() + POLL_TIMEOUT
    while True:
        try:
            execution = await client.get_execution(execution_id, include_data=False)
            status = execution.get("status")
            if status in TERMINAL_STATUSES:
                return status
        except N8nError as exc:
            # A new execution can 404 briefly, and a blip shouldn't fail a good repair.
            logger.info("Polling execution %s: %s", execution_id, exc.message)
        if time.monotonic() >= deadline:
            return "timeout"
        await asyncio.sleep(POLL_INTERVAL)


def _summarize(statuses: list[str]) -> str:
    if not statuses:
        return "not_run"
    unique = sorted(set(statuses))
    if len(unique) == 1:
        return unique[0]
    return "mixed: " + ", ".join(f"{statuses.count(s)} {s}" for s in unique)


def _success(recovered: int, total: int, execution_status: str, prefix: str) -> RepairResult:
    if total == 0:
        return RepairResult(
            repair_status="success",
            execution_status=execution_status,
            recovered_count=0,
            verified=False,
            message=f"{prefix} No failed executions were left to rerun, so it is not verified.",
        )
    return RepairResult(
        repair_status="success",
        execution_status=execution_status,
        recovered_count=recovered,
        verified=True,
        message=f"{prefix} All {recovered} failed executions recovered.",
    )


def _failed(message: str, execution_status: str) -> RepairResult:
    return RepairResult(
        repair_status="failed",
        execution_status=execution_status,
        recovered_count=0,
        verified=False,
        message=message,
    )
