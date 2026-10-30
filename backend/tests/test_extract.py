import json

import pytest

from app.agent.extract import ExtractionError, extract_context, rule_category, strip_stack


def test_parses_the_real_failure(execution):
    context = extract_context(execution)

    assert context.failed_node == "Normalize Lead"
    assert context.node_type == "n8n-nodes-base.code"
    assert "toLowerCase" in context.error_message
    assert context.error_description == "TypeError"
    assert context.workflow_name == "Lead Capture"
    assert "body.email.toLowerCase()" in context.parameters["jsCode"]


def test_input_body_has_capitalized_email(execution):
    context = extract_context(execution)

    assert context.sample_input == [{"body": {"Email": "Priya@Test.com", "name": "Priya"}}]


def test_oversized_sample_is_truncated(execution):
    run = execution["data"]["resultData"]["runData"]["Lead Form Webhook"][0]
    run["data"]["main"][0][0]["json"]["body"] = {"notes": "x" * 5000}

    sample = extract_context(execution).sample_input[0]

    assert isinstance(sample, str) and sample.endswith("(truncated)")
    assert len(sample) < 2100


def test_llm_context_has_no_stack_or_headers(execution):
    dumped = extract_context(execution).model_dump_json()

    assert '"stack"' not in dumped
    assert "user-agent" not in dumped


def test_strip_stack_is_recursive(execution):
    assert '"stack"' not in json.dumps(strip_stack(execution))


def test_saved_workflow_takes_precedence_over_snapshot(execution, workflow):
    workflow["nodes"][1]["parameters"] = {"jsCode": "return [];"}

    context = extract_context(execution, workflow)

    assert context.parameters == {"jsCode": "return [];"}


def test_missing_node_is_an_error(execution, workflow):
    workflow["nodes"] = workflow["nodes"][:1]

    with pytest.raises(ExtractionError, match="no longer exists"):
        extract_context(execution, workflow)


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Request failed with status code 401", "needs_user"),
        ("The credentials have expired", "needs_user"),
        ("Service unavailable", "needs_user"),
        ("Cannot read properties of undefined (reading 'toLowerCase')", None),
        ("Request timed out after 30000ms", None),
    ],
)
def test_rule_category(execution, message, expected):
    context = extract_context(execution).model_copy(
        update={"error_message": message, "error_description": None}
    )

    assert rule_category(context) == expected
