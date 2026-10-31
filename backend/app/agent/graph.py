import asyncio
import difflib
import logging
import re
import time
import uuid
from typing import Any, Literal, TypedDict

import httpx
from langgraph.graph import END, StateGraph

from app import db
from app.agent import prompts
from app.agent.extract import (
    ExecutionContext,
    ExtractionError,
    extract_context,
    failed_node_name,
    rule_category,
)
from app.agent.validate import ValidationError, apply_parameters, parse_parameters, validate_patch
from app.config import get_settings
from app.n8n.client import N8nClient, N8nError, is_unrecovered
from app.schemas import (
    Diagnosis,
    DiagnosisMessage,
    ErrorMessage,
    InstructionsMessage,
    ManualSteps,
    PatchProposal,
    ProposalMessage,
    ResultMessage,
    TextMessage,
)
from app.services import repair
from app.services.workflows import list_visible_workflows, list_workflow_health

logger = logging.getLogger(__name__)

MAX_AFFECTED = 25
MAX_CONCURRENT = 5
MAX_PROPOSE_ATTEMPTS = 2
LLM_TIMEOUT = 60
RETRYABLE_CODES = {503, 504}
FALLBACK_CODES = {429, 503, 504}

APPROVE = re.compile(
    r"^(?:(?:(?:yes|yeah|yep|yup|ok|okay|sure|sounds good|looks good|lgtm) )?"
    r"(?:approve(?: (?:it|this|that|the fix|the repair))?"
    r"|go ahead(?: and (?:fix|apply|approve) (?:it|this|that))?"
    r"|(?:fix|do|apply|ship) it|apply the fix)"
    r"|yes|yeah|yep|yup|looks good|sounds good|lgtm)$"
)
REJECT = re.compile(
    r"^(?:no|nope|nah|(?:(?:no|nope|nah) )?(?:reject(?: (?:it|this|that|the fix|the repair))?"
    r"|decline(?: it)?|cancel(?: it)?|(?:dont|do not) (?:fix|apply|approve)(?: (?:it|this|that))?"
    r"|leave it|hold off|not now))$"
)
DONE = re.compile(
    r"^(?:(?:ok|okay|yes|alright) )?(?:(?:im |i am |all )?(?:done|finished)"
    r"|i (?:did|fixed|finished|completed) (?:it|this|that)"
    r"|(?:ive|i have) (?:done|fixed|finished|completed) (?:it|this|that))$"
)
STATUS = re.compile(
    r"\b(status|overview|health|healthy|failing|broken|which workflows|all workflows|any failures)\b",
    re.IGNORECASE,
)
FAILURE_WORDS = re.compile(
    r"\b(fail|failed|failing|failure|error|broke|broken|wrong|why)\b", re.IGNORECASE
)
NAME_STOPWORDS = {"workflow", "the", "a", "an", "of", "and", "to"}


class LLMError(Exception):
    pass


def _error_details(exc: Exception) -> tuple[int | None, str | None, str]:
    """(HTTP code, API status, message). The LangChain wrapper keeps the SDK error as its cause."""
    source = exc if hasattr(exc, "code") else exc.__cause__ or exc
    return getattr(source, "code", None), getattr(source, "status", None), str(exc)


def _is_timeout(exc: BaseException | None) -> bool:
    # SDK and LangChain wrappers hide the original timeout, so the whole chain is checked.
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
            return True
        if "timeout" in type(exc).__name__.lower():
            return True
        exc = exc.__cause__ or exc.__context__
    return False


def _should_fall_back(code: int | None, exc: Exception) -> bool:
    return code in FALLBACK_CODES or _is_timeout(exc)


def _describe(exc: Exception) -> str:
    code, _, _ = _error_details(exc)
    return f"{type(exc).__name__}, HTTP {code}" if code else type(exc).__name__


class GeminiAgent:
    """The only code that calls the LLM: Gemini first, then Groq if Gemini is unavailable.

    It diagnoses and proposes; it never touches n8n.
    """

    # Lowest thinking level found to work, per model. Support varies: 3.8 rejects "minimal".
    _thinking_levels: dict[str, str] = {}

    def __init__(self) -> None:
        settings = get_settings()
        if not settings.gemini_api_key:
            raise LLMError("GEMINI_API_KEY is not set, so the AI agent can't run a diagnosis.")
        self._model_name = settings.gemini_model
        self._api_key = settings.gemini_api_key
        self._groq_api_key = settings.groq_api_key
        self._groq_model = settings.groq_model

    def _build_model(self, thinking_level: str) -> Any:
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            model=self._model_name,
            google_api_key=self._api_key,
            temperature=0,
            timeout=LLM_TIMEOUT,
            max_retries=1,  # A single attempt in the SDK; _ask owns the retry policy.
            thinking_level=thinking_level,
        )

    def _build_groq_model(self) -> Any:
        from langchain_groq import ChatGroq

        return ChatGroq(
            model=self._groq_model,
            api_key=self._groq_api_key,
            temperature=0,
            timeout=LLM_TIMEOUT,
            max_retries=0,  # The fallback is a single attempt.
        )

    def _log_failure(self, provider: str, model: str, schema: type, started: float, exc: Exception) -> None:
        code, status, message = _error_details(exc)
        for secret in (self._api_key, self._groq_api_key):
            if secret:
                message = message.replace(secret, "[redacted]")
        logger.warning(
            "LLM call failed: provider=%s model=%s schema=%s elapsed=%.2fs "
            "type=%s code=%s status=%s message=%s",
            provider,
            model,
            schema.__name__,
            time.monotonic() - started,
            type(exc).__name__,
            code,
            status,
            message,
        )

    def _log_success(self, provider: str, model: str, schema: type, started: float) -> None:
        logger.info(
            "LLM call ok: provider=%s model=%s schema=%s elapsed=%.2fs",
            provider,
            model,
            schema.__name__,
            time.monotonic() - started,
        )

    async def _ask(self, schema: type, prompt: str) -> Any:
        messages = [("system", prompts.SYSTEM), ("human", prompt)]
        retried = False
        while True:
            level = self._thinking_levels.get(self._model_name, "minimal")
            started = time.monotonic()
            try:
                answer = await (
                    self._build_model(level).with_structured_output(schema).ainvoke(messages)
                )
            except Exception as exc:
                self._log_failure("gemini", self._model_name, schema, started, exc)
                code, _, message = _error_details(exc)
                if code == 400 and level == "minimal" and "thinking level" in message.lower():
                    self._thinking_levels[self._model_name] = "low"
                    continue
                if self._groq_api_key and self._groq_model and _should_fall_back(code, exc):
                    return await self._ask_groq(schema, messages)
                if code in RETRYABLE_CODES and not retried:
                    retried = True
                    continue
                raise LLMError(f"The AI model call failed ({_describe(exc)}).") from exc

            self._log_success("gemini", self._model_name, schema, started)
            if answer is None:
                raise LLMError("The AI model returned no structured answer.")
            return answer

    async def _ask_groq(self, schema: type, messages: list[tuple[str, str]]) -> Any:
        started = time.monotonic()
        try:
            answer = await self._build_groq_model().with_structured_output(schema).ainvoke(messages)
        except Exception as exc:
            self._log_failure("groq", self._groq_model, schema, started, exc)
            raise LLMError(
                f"Gemini is unavailable and the Groq fallback failed ({_describe(exc)})."
            ) from exc
        self._log_success("groq", self._groq_model, schema, started)
        if answer is None:
            raise LLMError("The Groq fallback returned no structured answer.")
        return answer

    async def diagnose(self, context: ExecutionContext) -> Diagnosis:
        return await self._ask(Diagnosis, prompts.DIAGNOSE.format(context=_context_json(context)))

    async def propose_patch(
        self, context: ExecutionContext, diagnosis: Diagnosis, validation_error: str | None
    ) -> PatchProposal:
        retry = (
            prompts.VALIDATION_RETRY.format(error=validation_error) if validation_error else ""
        )
        return await self._ask(
            PatchProposal,
            prompts.PROPOSE_PATCH.format(
                context=_context_json(context),
                diagnosis=diagnosis.model_dump_json(),
                validation_error=retry,
            ),
        )

    async def propose_steps(self, context: ExecutionContext, diagnosis: Diagnosis) -> ManualSteps:
        return await self._ask(
            ManualSteps,
            prompts.PROPOSE_STEPS.format(
                context=_context_json(context),
                diagnosis=diagnosis.model_dump_json(),
            ),
        )


class AgentState(TypedDict, total=False):
    thread_id: str | None
    workflow_name: str | None
    message: str
    workflow_id: str | None
    webhook_execution_id: str | None
    route: str
    decision: str
    finished: bool
    messages: list[dict[str, Any]]
    workflow: dict[str, Any]
    context: ExecutionContext
    affected_ids: list[str]
    diagnosis: Diagnosis
    patch: PatchProposal
    steps: ManualSteps
    parameters: dict[str, Any]
    attempts: int
    validation_error: str | None
    created: bool
    skipped: bool
    failed: bool


def _context_json(context: ExecutionContext) -> str:
    return context.model_dump_json(exclude={"execution_id", "workflow_id"})


def _text(content: str) -> dict[str, Any]:
    return TextMessage(content=content).model_dump()


def _error(message: str) -> dict[str, Any]:
    return ErrorMessage(message=message).model_dump()


def parse_decision(message: str) -> Literal["approve", "reject", "done"] | None:
    """Whole short phrases only, so "no idea why it failed" can never reject or approve a fix."""
    if "?" in message:
        return None
    phrase = message.lower()
    phrase = re.sub(r"[’']", "", phrase)
    phrase = re.sub(r"[^a-z\s]", " ", phrase)
    phrase = re.sub(r"\b(please|thanks|thank you)\b", " ", phrase)
    phrase = re.sub(r"\s+", " ", phrase).strip()
    if REJECT.match(phrase):
        return "reject"
    if APPROVE.match(phrase):
        return "approve"
    if DONE.match(phrase):
        return "done"
    return None


def match_workflows(message: str, workflows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fuzzy-matches workflow names against the message. More than one result means ambiguous."""
    words = re.findall(r"[a-z0-9]+", message.lower())
    scored = []
    for workflow in workflows:
        tokens = re.findall(r"[a-z0-9]+", workflow.get("name", "").lower())
        tokens = [token for token in tokens if token not in NAME_STOPWORDS]
        if not tokens:
            continue
        hits = sum(1 for token in tokens if difflib.get_close_matches(token, words, n=1, cutoff=0.8))
        scored.append((hits / len(tokens), workflow))
    best = max((score for score, _ in scored), default=0)
    if best < 0.5:
        return []
    return [workflow for score, workflow in scored if score == best]


def _names(workflows: list[dict[str, Any]]) -> str:
    return ", ".join(f'"{workflow.get("name")}"' for workflow in workflows)


def build_graph(client: N8nClient, user_id: str, llm_factory: Any = GeminiAgent) -> Any:
    async def router(state: AgentState) -> AgentState:
        if state.get("webhook_execution_id"):
            return {"route": "fetch"}

        message = state["message"]
        workflow_id = state.get("workflow_id")
        decision = parse_decision(message)
        if decision:
            return {"route": "decide", "decision": decision}

        workflows = await list_visible_workflows(client)
        named = match_workflows(message, workflows)
        if STATUS.search(message) and not named:
            return {"route": "status"}

        if workflow_id:
            if not any(str(w["id"]) == workflow_id for w in workflows):
                return _finish(_error(f"Workflow {workflow_id} was not found in n8n."))
            return {"route": "fetch"}
        if len(named) == 1:
            return {
                "route": "fetch",
                "workflow_id": str(named[0]["id"]),
                "workflow_name": named[0].get("name"),
            }
        if len(named) > 1:
            return _finish(_text(f"Which workflow do you mean: {_names(named)}?"))

        if FAILURE_WORDS.search(message):
            health = await list_workflow_health(client, user_id)
            failing = [w for w in health if w.failed_count > 0]
            if len(failing) == 1:
                return {"route": "fetch", "workflow_id": failing[0].id, "workflow_name": failing[0].name}
            if failing:
                names = ", ".join(f'"{w.name}"' for w in failing)
                return _finish(_text(f"Which workflow do you mean: {names}?"))
            return _finish(_text("No workflows have unresolved failures right now."))
        return _finish(_text(f"Which workflow should I look at? You have: {_names(workflows)}."))

    async def status(state: AgentState) -> AgentState:
        health = await list_workflow_health(client, user_id)
        if not health:
            return {"messages": [_text("There are no workflows in this n8n instance.")]}
        failing = [w for w in health if w.status == "failing"]
        pending = [w for w in health if w.status == "pending"]
        healthy = len(health) - len(failing) - len(pending)
        lines = [
            f"{len(health)} workflows: {healthy} healthy, {len(failing)} failing, "
            f"{len(pending)} waiting on a fix."
        ]
        lines += [f'"{w.name}" has {w.failed_count} failed runs.' for w in failing + pending]
        return {"messages": [_text(" ".join(lines))]}

    async def decide(state: AgentState) -> AgentState:
        decision = state["decision"]
        proposal, problem = _select_proposal(user_id, state.get("workflow_id"), state.get("thread_id"))
        if proposal is None:
            return {"messages": [_text(problem)]}
        if decision == "approve" and proposal["kind"] == "manual":
            return {"messages": [_text("This one needs your action. Follow the steps, then say done.")]}
        if decision == "done" and proposal["kind"] == "patch":
            return {"messages": [_text("This fix is waiting for your approval. Say approve or reject.")]}

        try:
            if decision == "reject":
                repair.reject(proposal["id"], user_id)
                return {"messages": [_text("Fix rejected. Nothing was changed.")]}
            if decision == "approve":
                result = await repair.approve(proposal["id"], user_id, client)
            else:
                result = await repair.complete_manual(proposal["id"], user_id, client)
        except repair.ProposalStateError as exc:
            return {"messages": [_error(str(exc))]}
        return {"messages": [ResultMessage(**result.model_dump()).model_dump()]}

    async def fetch(state: AgentState) -> AgentState:
        workflow_id = state["workflow_id"]
        target_id = state.get("webhook_execution_id")
        target = None
        if target_id:
            target = await client.get_execution(target_id)
            # A retry fails with its own Error Trigger; the original failure is already handled.
            if not is_unrecovered(target):
                return {"finished": True, "skipped": True, "messages": []}

        listed = await client.list_executions(workflow_id, status="error")
        failed = [str(e["id"]) for e in listed if is_unrecovered(e)]
        if target_id and target_id not in failed:
            failed.insert(0, target_id)
        if not failed:
            return _finish(_text("This workflow has no unresolved failures."))

        latest = target or await client.get_execution(failed[0])
        workflow = await client.get_workflow(workflow_id)
        node_name = failed_node_name(latest)
        semaphore = asyncio.Semaphore(MAX_CONCURRENT)

        async def load(execution_id: str) -> dict[str, Any] | None:
            async with semaphore:
                try:
                    return await client.get_execution(execution_id)
                except N8nError as exc:
                    logger.warning("Skipping execution %s: %s", execution_id, exc.message)
                    return None

        others = await asyncio.gather(
            *(load(i) for i in failed[:MAX_AFFECTED] if i != str(latest["id"]))
        )
        # Only runs that failed at the same node can be recovered by this fix.
        affected = [str(latest["id"])] + [
            str(e["id"]) for e in others if e and failed_node_name(e) == node_name
        ]

        try:
            context = extract_context(latest, workflow, affected_count=len(affected))
        except ExtractionError as exc:
            return _finish(_error(str(exc)))

        for proposal in db.list_open_proposals(user_id, workflow_id):
            unchanged = proposal["backup_workflow"].get("nodes") == workflow.get("nodes")
            if proposal["signature"] == context.signature and unchanged:
                db.set_proposal_affected(proposal["id"], affected)
                return {"finished": True, "messages": proposal["messages"]}

        return {"workflow": workflow, "context": context, "affected_ids": affected, "attempts": 0}

    async def diagnose(state: AgentState) -> AgentState:
        context = state["context"]
        answer = await llm_factory().diagnose(context)
        category = rule_category(context) or answer.category
        confidence = min(max(answer.confidence, 0.0), 1.0)
        return {"diagnosis": answer.model_copy(update={"category": category, "confidence": confidence})}

    async def propose(state: AgentState) -> AgentState:
        llm = llm_factory()
        if state["diagnosis"].category == "needs_user":
            return {"steps": await llm.propose_steps(state["context"], state["diagnosis"])}
        patch = await llm.propose_patch(
            state["context"], state["diagnosis"], state.get("validation_error")
        )
        return {"patch": patch, "attempts": state.get("attempts", 0) + 1}

    async def validate(state: AgentState) -> AgentState:
        if state["diagnosis"].category == "needs_user":
            if not state["steps"].steps:
                return _finish(_diagnosis_message(state), _error("The agent returned no steps."))
            return {"validation_error": None}
        context = state["context"]
        try:
            parameters = parse_parameters(state["patch"].parameters_json)
            patched = apply_parameters(state["workflow"], context.failed_node, parameters)
            validate_patch(state["workflow"], patched, context.failed_node)
        except ValidationError as exc:
            if state["attempts"] < MAX_PROPOSE_ATTEMPTS:
                return {"validation_error": str(exc)}
            return _finish(
                _diagnosis_message(state),
                _error(f"The proposed fix failed validation, so nothing was saved: {exc}"),
            )
        return {"parameters": parameters, "validation_error": None}

    async def save(state: AgentState) -> AgentState:
        context = state["context"]
        diagnosis = state["diagnosis"]
        workflow = state["workflow"]
        for stale in db.list_open_proposals(user_id, context.workflow_id):
            db.set_proposal_status(stale["id"], "superseded")

        incident = db.find_open_incident(user_id, context.workflow_id, context.signature)
        if incident:
            incident_id = incident["id"]
            db.update_incident_diagnosis(incident_id, diagnosis.root_cause, diagnosis.category)
        else:
            incident_id = db.create_incident(
                user_id,
                context.workflow_id,
                context.workflow_name,
                context.failed_node,
                diagnosis.root_cause,
                diagnosis.category,
                context.signature,
            )

        # A workflow's cards live in its own thread, wherever the question was asked.
        thread = db.get_or_create_workflow_thread(
            user_id, context.workflow_id, context.workflow_name
        )
        proposal_id = f"prop_{uuid.uuid4().hex[:12]}"
        if diagnosis.category == "needs_user":
            kind = "manual"
            action = InstructionsMessage(
                proposal_id=proposal_id,
                title=state["steps"].title,
                steps=state["steps"].steps,
            )
        else:
            kind = "patch"
            node = next(n for n in workflow["nodes"] if n["name"] == context.failed_node)
            action = ProposalMessage(
                proposal_id=proposal_id,
                workflow_name=context.workflow_name,
                node_name=context.failed_node,
                before=_node_view(node, node.get("parameters") or {}),
                after=_node_view(node, state["parameters"]),
                explanation=state["patch"].explanation,
            )
        messages = [_diagnosis_message(state), action.model_dump()]
        db.create_proposal(
            user_id,
            proposal_id,
            incident_id,
            context.workflow_id,
            context.workflow_name,
            context.failed_node,
            kind,
            context.signature,
            state.get("parameters"),
            workflow,
            state["affected_ids"],
            messages,
            thread_id=thread["id"],
        )
        return {"messages": messages, "created": True}

    def after_router(state: AgentState) -> str:
        return END if state.get("finished") else state["route"]

    def after_fetch(state: AgentState) -> str:
        return END if state.get("finished") else "diagnose"

    def after_validate(state: AgentState) -> str:
        if state.get("finished"):
            return END
        return "propose" if state.get("validation_error") else "save"

    graph = StateGraph(AgentState)
    graph.add_node("router", router)
    graph.add_node("status", status)
    graph.add_node("decide", decide)
    graph.add_node("fetch", fetch)
    graph.add_node("diagnose", diagnose)
    graph.add_node("propose", propose)
    graph.add_node("validate", validate)
    graph.add_node("save", save)
    graph.set_entry_point("router")
    graph.add_conditional_edges("router", after_router, ["status", "decide", "fetch", END])
    graph.add_conditional_edges("fetch", after_fetch, ["diagnose", END])
    graph.add_edge("diagnose", "propose")
    graph.add_edge("propose", "validate")
    graph.add_conditional_edges("validate", after_validate, ["propose", "save", END])
    graph.add_edge("status", END)
    graph.add_edge("decide", END)
    graph.add_edge("save", END)
    return graph.compile()


def _finish(*messages: dict[str, Any]) -> AgentState:
    return {"finished": True, "messages": list(messages)}


def _node_view(node: dict[str, Any], parameters: dict[str, Any]) -> dict[str, Any]:
    return {"name": node.get("name"), "type": node.get("type"), "parameters": parameters}


def _diagnosis_message(state: AgentState) -> dict[str, Any]:
    context = state["context"]
    return DiagnosisMessage(
        workflow_name=context.workflow_name,
        failed_node=context.failed_node,
        **state["diagnosis"].model_dump(),
    ).model_dump()


def _select_proposal(
    user_id: str, workflow_id: str | None, thread_id: str | None
) -> tuple[dict[str, Any] | None, str]:
    # A yes or no in one chat must never reach a fix that's waiting in another.
    proposals = db.list_open_proposals(user_id, workflow_id, thread_id)
    if not proposals:
        where = "in this chat" if thread_id else "right now"
        return None, f"There is no fix waiting for a decision {where}."
    if len(proposals) > 1:
        # Never guess which workflow a yes or no applies to.
        names = ", ".join(sorted({f'"{p["workflow_name"]}"' for p in proposals}))
        return None, f"More than one fix is waiting. Select a workflow first: {names}."
    return proposals[0], ""


async def run_agent(
    client: N8nClient,
    user_id: str,
    message: str = "",
    workflow_id: str | None = None,
    webhook_execution_id: str | None = None,
    thread_id: str | None = None,
    llm_factory: Any = GeminiAgent,
) -> AgentState:
    """Runs the graph. Failures come back as an error message, never as invented results."""
    state: AgentState = {
        "message": message,
        "workflow_id": workflow_id,
        "webhook_execution_id": webhook_execution_id,
        "thread_id": thread_id,
    }
    try:
        return await build_graph(client, user_id, llm_factory).ainvoke(state)
    except N8nError as exc:
        return {"failed": True, "messages": [_error(exc.message)]}
    except LLMError as exc:
        return {"failed": True, "messages": [_error(str(exc))]}
