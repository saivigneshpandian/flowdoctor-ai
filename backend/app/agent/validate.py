"""Deterministic checks on an LLM-proposed patch. No LLM involved."""

import copy
import json
import os
import shutil
import subprocess
import tempfile
from typing import Any

CODE_NODE_TYPE = "n8n-nodes-base.code"
NODE_CHECK_TIMEOUT = 10


class ValidationError(Exception):
    pass


def parse_parameters(text: str) -> dict[str, Any]:
    try:
        parameters = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"parameters are not valid JSON: {exc}") from exc
    if not isinstance(parameters, dict):
        raise ValidationError("parameters must be a JSON object.")
    return parameters


def apply_parameters(
    workflow: dict[str, Any], node_name: str, parameters: dict[str, Any]
) -> dict[str, Any]:
    patched = copy.deepcopy(workflow)
    for node in patched.get("nodes", []):
        if node.get("name") == node_name:
            node["parameters"] = copy.deepcopy(parameters)
    return patched


def validate_patch(
    original: dict[str, Any], patched: dict[str, Any], node_name: str
) -> None:
    """Raises ValidationError unless the failing node's parameters are the only difference."""
    original_nodes = original.get("nodes", [])
    patched_nodes = patched.get("nodes", [])

    if len(original_nodes) != len(patched_nodes):
        raise ValidationError("the patch changes the number of nodes.")
    if original.get("connections") != patched.get("connections"):
        raise ValidationError("the patch changes the workflow connections.")
    for key in ("name", "settings"):
        if original.get(key) != patched.get(key):
            raise ValidationError(f"the patch changes the workflow {key}.")

    target = None
    for before, after in zip(original_nodes, patched_nodes):
        if before.get("name") != node_name:
            if before != after:
                raise ValidationError(f'the patch changes node "{before.get("name")}".')
            continue
        target = (before, after)
    if target is None:
        raise ValidationError(f'node "{node_name}" does not exist in the workflow.')

    before, after = target
    if {**before, "parameters": None} != {**after, "parameters": None}:
        raise ValidationError(f'the patch changes more than the parameters of "{node_name}".')
    if before.get("parameters") == after.get("parameters"):
        raise ValidationError("the patch does not change anything.")
    if after.get("type") == CODE_NODE_TYPE:
        check_javascript(after.get("parameters") or {})


def check_javascript(parameters: dict[str, Any]) -> None:
    code = parameters.get("jsCode")
    node = shutil.which("node")
    if not isinstance(code, str) or node is None:
        return

    # n8n runs Code nodes as a function body, so a top-level `return` is legal.
    wrapped = f"async function __n8n__() {{\n{code}\n}}\n"
    handle, path = tempfile.mkstemp(suffix=".js")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            file.write(wrapped)
        result = subprocess.run(
            [node, "--check", path],
            capture_output=True,
            text=True,
            timeout=NODE_CHECK_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return
    finally:
        os.unlink(path)
    if result.returncode != 0:
        detail = next(
            (line for line in result.stderr.splitlines() if "Error" in line),
            "syntax error",
        )
        raise ValidationError(f"jsCode does not parse: {detail}")
