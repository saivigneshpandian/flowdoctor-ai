import copy
import shutil

import pytest

from app.agent.validate import ValidationError, apply_parameters, parse_parameters, validate_patch

NODE = "Normalize Lead"
GOOD_CODE = (
    "return $input.all().map(item => ({\n  json: {\n"
    "    email: (item.json.body.email ?? item.json.body.Email).toLowerCase(),\n"
    "    name: item.json.body.name\n  }\n}));"
)


def good_parameters(workflow):
    return {**workflow["nodes"][1]["parameters"], "jsCode": GOOD_CODE}


def test_accepts_a_parameter_only_patch(workflow):
    validate_patch(workflow, apply_parameters(workflow, NODE, good_parameters(workflow)), NODE)


def test_rejects_changed_node_count(workflow):
    patched = apply_parameters(workflow, NODE, good_parameters(workflow))
    patched["nodes"].pop()

    with pytest.raises(ValidationError, match="number of nodes"):
        validate_patch(workflow, patched, NODE)


def test_rejects_changed_connections(workflow):
    patched = apply_parameters(workflow, NODE, good_parameters(workflow))
    patched["connections"] = {}

    with pytest.raises(ValidationError, match="connections"):
        validate_patch(workflow, patched, NODE)


def test_rejects_invalid_json():
    with pytest.raises(ValidationError, match="not valid JSON"):
        parse_parameters('{"jsCode": ')


def test_rejects_non_object_json():
    with pytest.raises(ValidationError, match="JSON object"):
        parse_parameters("[1, 2]")


def test_rejects_change_to_another_node(workflow):
    patched = apply_parameters(workflow, NODE, good_parameters(workflow))
    patched["nodes"][2]["parameters"] = {"changed": True}

    with pytest.raises(ValidationError, match="Save Lead"):
        validate_patch(workflow, patched, NODE)


def test_rejects_change_to_node_fields_other_than_parameters(workflow):
    patched = apply_parameters(workflow, NODE, good_parameters(workflow))
    patched["nodes"][1]["credentials"] = {"api": {"id": "1"}}

    with pytest.raises(ValidationError, match="more than the parameters"):
        validate_patch(workflow, patched, NODE)


def test_rejects_missing_node(workflow):
    with pytest.raises(ValidationError, match="does not exist"):
        validate_patch(workflow, copy.deepcopy(workflow), "Ghost")


def test_rejects_no_op(workflow):
    with pytest.raises(ValidationError, match="does not change"):
        validate_patch(workflow, copy.deepcopy(workflow), NODE)


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_rejects_javascript_that_does_not_parse(workflow):
    parameters = {**good_parameters(workflow), "jsCode": "return $input.all(.map("}
    patched = apply_parameters(workflow, NODE, parameters)

    with pytest.raises(ValidationError, match="does not parse"):
        validate_patch(workflow, patched, NODE)
