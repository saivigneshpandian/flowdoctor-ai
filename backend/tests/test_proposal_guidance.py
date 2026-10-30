import json
import shutil
import subprocess

import pytest

from app.agent import prompts
from app.agent.validate import apply_parameters, validate_patch

NODE = "Normalize Lead"
EXPECTED_FIX = """return $input.all().map(item => {
  const email = item.json.body.email ?? item.json.body.Email;
  if (!email) throw new Error('Lead is missing an email');
  return {
    json: {
      email: email.toLowerCase(),
      name: item.json.body.name
    }
  };
});"""

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def run_code_node(code, body):
    """Runs Code node source the way n8n does: a function body with $input in scope."""
    script = (
        f"const $input = {{ all: () => [{{ json: {{ body: {json.dumps(body)} }} }}] }};\n"
        f"const run = () => {{\n{code}\n}};\n"
        "console.log(JSON.stringify(run()));"
    )
    return subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)


def test_guidance_requires_both_names_and_a_loud_failure():
    prompt = prompts.PROPOSE_PATCH.format(context="{}", diagnosis="{}", validation_error="")

    assert "const email = item.json.body.email ?? item.json.body.Email;" in prompt
    assert "if (!email) throw new Error('Lead is missing an email');" in prompt
    assert "email.toLowerCase()" in prompt
    assert "fail loudly" in prompt


def test_expected_fix_passes_validation(workflow):
    parameters = {**workflow["nodes"][1]["parameters"], "jsCode": EXPECTED_FIX}

    validate_patch(workflow, apply_parameters(workflow, NODE, parameters), NODE)


@needs_node
@pytest.mark.parametrize("field", ["email", "Email"])
def test_expected_fix_handles_both_field_names(field):
    result = run_code_node(EXPECTED_FIX, {field: "Priya@Test.com", "name": "Priya"})

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)[0]["json"] == {"email": "priya@test.com", "name": "Priya"}


@needs_node
@pytest.mark.parametrize("body", [{"name": "Priya"}, {"email": "", "name": "Priya"}])
def test_expected_fix_fails_loudly_when_email_is_missing(body):
    result = run_code_node(EXPECTED_FIX, body)

    assert result.returncode != 0
    assert "Lead is missing an email" in result.stderr
