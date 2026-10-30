"""Deterministic parsing of n8n executions. No LLM involved."""

import json
import re
from typing import Any

from pydantic import BaseModel

SAMPLE_ITEMS = 1
MAX_SAMPLE_CHARS = 2000

# Checked before the LLM's answer: these failures must never become an automatic patch.
NEEDS_USER = re.compile(
    r"\b(401|403|502|503|504)\b|credential|unauthori[sz]ed|forbidden|expired|permission"
    r"|service unavailable|bad gateway|econnrefused|enotfound",
    re.IGNORECASE,
)


class ExtractionError(Exception):
    pass


class ExecutionContext(BaseModel):
    execution_id: str
    workflow_id: str
    workflow_name: str
    failed_node: str
    node_type: str
    parameters: dict[str, Any]
    error_message: str
    error_description: str | None
    sample_input: list[Any]
    affected_count: int = 1

    @property
    def signature(self) -> str:
        return f"{self.failed_node}|{self.error_message}"


def strip_stack(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: strip_stack(item) for key, item in value.items() if key != "stack"}
    if isinstance(value, list):
        return [strip_stack(item) for item in value]
    return value


def failed_node_name(execution: dict[str, Any]) -> str | None:
    return _result_data(execution).get("lastNodeExecuted")


def failure_summary(execution: dict[str, Any]) -> tuple[str, str]:
    """The failing node and n8n's own error message, exactly as the execution recorded them."""
    result = _result_data(execution)
    node_name = result.get("lastNodeExecuted")
    if not node_name:
        raise ExtractionError(f"Execution {execution.get('id')} has no failing node.")
    node_runs = (result.get("runData") or {}).get(node_name) or []
    error = next((run["error"] for run in node_runs if run.get("error")), None)
    message = (error or result.get("error") or {}).get("message")
    if not message:
        raise ExtractionError(f"Execution {execution.get('id')} has no error message.")
    return node_name, message


def rule_category(context: ExecutionContext) -> str | None:
    text = f"{context.error_message} {context.error_description or ''}"
    return "needs_user" if NEEDS_USER.search(text) else None


def extract_context(
    execution: dict[str, Any],
    workflow: dict[str, Any] | None = None,
    affected_count: int = 1,
) -> ExecutionContext:
    """Builds the LLM context. `workflow` is the saved workflow, if newer than the execution's."""
    result = _result_data(execution)
    run_data = result.get("runData") or {}
    source = workflow or execution.get("workflowData") or {}

    node_name = result.get("lastNodeExecuted")
    if not node_name:
        raise ExtractionError(f"Execution {execution.get('id')} has no failing node.")
    node = next((n for n in source.get("nodes", []) if n.get("name") == node_name), None)
    if node is None:
        raise ExtractionError(f'Node "{node_name}" no longer exists in the workflow.')

    node_runs = run_data.get(node_name) or []
    error = next((run["error"] for run in node_runs if run.get("error")), None)
    error = strip_stack(error or result.get("error") or {})
    if not error.get("message"):
        raise ExtractionError(f"Execution {execution.get('id')} has no error message.")

    return ExecutionContext(
        execution_id=str(execution.get("id")),
        workflow_id=str(source.get("id") or execution.get("workflowId")),
        workflow_name=source.get("name", "Unknown workflow"),
        failed_node=node_name,
        node_type=node.get("type", "unknown"),
        parameters=node.get("parameters") or {},
        error_message=error["message"],
        error_description=error.get("description"),
        sample_input=_previous_node_output(run_data, node_runs) or _trigger_input(run_data),
        affected_count=affected_count,
    )


def _result_data(execution: dict[str, Any]) -> dict[str, Any]:
    return (execution.get("data") or {}).get("resultData") or {}


def _trigger_input(run_data: dict[str, Any]) -> list[Any]:
    for runs in run_data.values():
        if runs and not runs[0].get("source"):
            return _items(runs[0])
    return []


def _previous_node_output(run_data: dict[str, Any], node_runs: list[dict[str, Any]]) -> list[Any]:
    sources = (node_runs[0].get("source") or []) if node_runs else []
    previous = sources[0].get("previousNode") if sources and sources[0] else None
    runs = run_data.get(previous) or []
    return _items(runs[0]) if runs else []


def _items(run: dict[str, Any]) -> list[Any]:
    outputs = ((run.get("data") or {}).get("main") or [[]])[0] or []
    # Webhook items carry request headers, which can hold secrets. Keep only the body.
    samples = []
    for item in outputs[:SAMPLE_ITEMS]:
        data = item.get("json") or {}
        # The wrapper stays: node code reads item.json.body.x, so the model must see that shape.
        sample = strip_stack({"body": data["body"]} if "body" in data else data)
        text = json.dumps(sample, default=str)
        samples.append(f"{text[:MAX_SAMPLE_CHARS]}... (truncated)" if len(text) > MAX_SAMPLE_CHARS else sample)
    return samples
