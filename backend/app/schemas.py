from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field

Category = Literal["auto_fixable", "needs_user"]
FixStatus = Literal["approved", "rejected", "open", "manual"]


class Credentials(BaseModel):
    email: str
    password: str


class SignupRequest(Credentials):
    invite_code: str = ""


class Me(BaseModel):
    email: str
    webhook_url: str
    has_connection: bool
    is_demo: bool = False


class SimulateFailureResult(BaseModel):
    workflow_id: str
    workflow_name: str
    # Unresolved failures before the test lead, so the frontend can tell when a new one appears.
    failed_count: int
    cooldown_seconds: int


class ConnectRequest(BaseModel):
    n8n_url: str
    api_key: str
    telegram_chat_id: str | None = None


class ConnectResponse(BaseModel):
    ok: bool
    error: str | None = None


class Workflow(BaseModel):
    id: str
    name: str
    status: Literal["healthy", "failing", "pending"]
    last_execution_at: str | None
    failed_count: int
    # Only ever true for the demo account, on a workflow with a known demo bug.
    can_simulate: bool = False


class WorkflowError(BaseModel):
    execution_id: str
    failed_node: str
    error_message: str


class ChatRequest(BaseModel):
    message: str
    workflow_id: str | None = None
    thread_id: str | None = None


class ThreadCreate(BaseModel):
    workflow_id: str | None = None
    workflow_name: str | None = None


class Thread(BaseModel):
    id: str
    title: str
    workflow_id: str | None
    workflow_name: str | None
    updated_at: str
    pending: bool = False


class ThreadMessage(BaseModel):
    id: int
    role: Literal["user", "agent"]
    content: str | None = None
    message: Any = None
    created_at: str


class ThreadDetail(BaseModel):
    thread: Thread
    messages: list[ThreadMessage]
    # Proposal id to backend status, so cards show their real state after a reload.
    proposal_states: dict[str, str]


class RepairResult(BaseModel):
    repair_status: Literal["success", "failed"]
    execution_status: str
    recovered_count: int
    verified: bool
    message: str


class OkResponse(BaseModel):
    ok: bool


class TextMessage(BaseModel):
    type: Literal["text"] = "text"
    content: str


class DiagnosisMessage(BaseModel):
    type: Literal["diagnosis"] = "diagnosis"
    workflow_name: str
    failed_node: str
    root_cause: str
    category: Category
    impact: str
    confidence: float
    recommendation: str


class ProposalMessage(BaseModel):
    type: Literal["proposal"] = "proposal"
    proposal_id: str
    workflow_name: str
    node_name: str
    before: Any
    after: Any
    explanation: str


class InstructionsMessage(BaseModel):
    type: Literal["instructions"] = "instructions"
    proposal_id: str
    title: str
    steps: list[str]


class ResultMessage(RepairResult):
    type: Literal["result"] = "result"


class ErrorMessage(BaseModel):
    type: Literal["error"] = "error"
    message: str


AgentMessage = Annotated[
    Union[
        TextMessage,
        DiagnosisMessage,
        ProposalMessage,
        InstructionsMessage,
        ResultMessage,
        ErrorMessage,
    ],
    Field(discriminator="type"),
]


class ChatResponse(BaseModel):
    messages: list[AgentMessage]
    # Where this exchange was saved. Naming a workflow in a general chat moves it to that workflow's thread.
    thread_id: str | None = None


class Incident(BaseModel):
    id: str
    workflow_name: str
    failed_node: str
    root_cause: str
    category: Category
    fix_status: FixStatus
    created_at: str
    resolved_at: str | None


# Structured outputs requested from the LLM.
class Diagnosis(BaseModel):
    root_cause: str = Field(description="Plain-language cause of the failure, one or two sentences.")
    category: Category
    impact: str = Field(description="What the business lost, using the affected execution count.")
    confidence: float = Field(description="Confidence in this diagnosis, from 0 to 1.")
    recommendation: str = Field(description="The recommended fix, one or two sentences.")


class PatchProposal(BaseModel):
    # A JSON string, because a free-form object can't be expressed in Gemini's response schema.
    parameters_json: str = Field(
        description="The complete new parameters object for the failing node, as a JSON string."
    )
    explanation: str = Field(description="What changed and why, in plain language.")


class ManualSteps(BaseModel):
    title: str
    steps: list[str] = Field(description="Exact steps for the user, in order, without numbering.")
