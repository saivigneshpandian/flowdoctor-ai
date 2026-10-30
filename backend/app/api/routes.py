import hmac
import logging
from typing import Any, AsyncIterator

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel

from app import auth, db, telegram
from app.agent.extract import ExtractionError, failure_summary
from app.agent.graph import run_agent
from app.config import get_settings
from app.n8n.client import N8nClient, N8nError, is_unrecovered
from app.schemas import (
    ChatRequest,
    ChatResponse,
    ConnectRequest,
    ConnectResponse,
    Credentials,
    ErrorMessage,
    Incident,
    Me,
    OkResponse,
    RepairResult,
    SignupRequest,
    Thread,
    ThreadCreate,
    ThreadDetail,
    ThreadMessage,
    Workflow,
    WorkflowError,
)
from app.services import repair, telegram_bot
from app.services.workflows import list_workflow_health

logger = logging.getLogger(__name__)

router = APIRouter()

NOT_CONNECTED = "n8n is not connected yet. Connect your instance first."
NOT_DIAGNOSED = "{name} failed, but FlowDoctor could not diagnose it automatically.\nOpen FlowDoctor to check."


def build_client(n8n_url: str, api_key: str) -> N8nClient:
    return N8nClient(n8n_url, api_key)


async def connected_client(
    user: dict[str, Any] = Depends(auth.current_user),
) -> AsyncIterator[N8nClient]:
    settings = auth.load_connection(user["id"])
    if settings is None:
        raise HTTPException(400, NOT_CONNECTED)
    client = build_client(settings["n8n_url"], settings["api_key"])
    try:
        yield client
    finally:
        await client.aclose()


@router.get("/health")
async def health() -> dict[str, bool]:
    return {"ok": True}


def _me(user: dict[str, Any], request: Request) -> Me:
    base = get_settings().public_api_url or str(request.base_url)
    return Me(
        email=user["email"],
        webhook_url=f"{base.rstrip('/')}/webhook/n8n-error/{user['webhook_token']}",
        has_connection=auth.load_connection(user["id"]) is not None,
    )


# These are plain functions so FastAPI runs them on a worker thread: argon2 is deliberately slow.
@router.post("/auth/signup", response_model=Me)
def signup(body: SignupRequest, request: Request, response: Response) -> Me:
    auth.check_invite_code(body.invite_code)
    user = auth.register(body.email, body.password)
    auth.set_session_cookie(response, user["id"])
    return _me(user, request)


@router.post("/auth/login", response_model=Me)
def login(body: Credentials, request: Request, response: Response) -> Me:
    user = auth.authenticate(body.email, body.password)
    if user is None:
        raise HTTPException(401, "Incorrect email or password.")
    auth.set_session_cookie(response, user["id"])
    return _me(user, request)


@router.post("/auth/logout", response_model=OkResponse)
def logout(response: Response) -> OkResponse:
    auth.clear_session_cookie(response)
    return OkResponse(ok=True)


@router.get("/auth/me", response_model=Me)
def me(request: Request, user: dict[str, Any] = Depends(auth.current_user)) -> Me:
    return _me(user, request)


@router.post("/connect", response_model=ConnectResponse, response_model_exclude_none=True)
async def connect(
    body: ConnectRequest, user: dict[str, Any] = Depends(auth.current_user)
) -> ConnectResponse:
    try:
        client = build_client(body.n8n_url, body.api_key)
    except httpx.InvalidURL:
        return ConnectResponse(ok=False, error="That n8n URL is not valid.")
    try:
        await client.list_workflows()
    except N8nError as exc:
        return ConnectResponse(ok=False, error=exc.message)
    finally:
        await client.aclose()
    auth.save_connection(user["id"], body.n8n_url, body.api_key, body.telegram_chat_id or None)
    return ConnectResponse(ok=True)


@router.get("/workflows", response_model=list[Workflow])
async def workflows(
    user: dict[str, Any] = Depends(auth.current_user),
    client: N8nClient = Depends(connected_client),
) -> list[Workflow]:
    return await list_workflow_health(client, user["id"])


@router.get("/workflows/{workflow_id}/error", response_model=WorkflowError)
async def workflow_error(
    workflow_id: str, client: N8nClient = Depends(connected_client)
) -> WorkflowError:
    """The latest unresolved failure, read straight from n8n. No LLM is involved."""
    failed = await client.list_executions(workflow_id, status="error")
    latest = next((e for e in failed if is_unrecovered(e)), None)
    if latest is None:
        raise HTTPException(404, "This workflow has no unresolved failures.")
    execution_id = str(latest["id"])
    try:
        node, message = failure_summary(await client.get_execution(execution_id))
    except ExtractionError as exc:
        raise HTTPException(422, str(exc)) from exc
    return WorkflowError(execution_id=execution_id, failed_node=node, error_message=message)


def _own_thread(thread_id: str, user: dict[str, Any]) -> dict[str, Any]:
    # Another user's thread looks exactly like one that doesn't exist.
    thread = db.get_thread(thread_id, user["id"])
    if thread is None:
        raise HTTPException(404, "Chat not found.")
    return thread


@router.get("/threads", response_model=list[Thread])
async def list_threads(user: dict[str, Any] = Depends(auth.current_user)) -> list[dict[str, Any]]:
    return db.list_threads(user["id"])


@router.post("/threads", response_model=Thread)
async def create_thread(
    body: ThreadCreate, user: dict[str, Any] = Depends(auth.current_user)
) -> dict[str, Any]:
    """Opens a workflow's one thread, or a blank chat when no workflow is given."""
    if body.workflow_id:
        return db.get_or_create_workflow_thread(user["id"], body.workflow_id, body.workflow_name)
    return db.get_or_create_blank_thread(user["id"])


@router.get("/threads/{thread_id}", response_model=ThreadDetail)
async def get_thread(
    thread_id: str, user: dict[str, Any] = Depends(auth.current_user)
) -> ThreadDetail:
    thread = _own_thread(thread_id, user)
    messages = [
        ThreadMessage(
            id=row["id"],
            role=row["role"],
            content=row["payload"]["content"] if row["role"] == "user" else None,
            message=row["payload"] if row["role"] == "agent" else None,
            created_at=row["created_at"],
        )
        for row in db.list_thread_messages(thread_id)
    ]
    return ThreadDetail(
        thread=Thread(**thread),
        messages=messages,
        proposal_states=db.thread_proposal_states(thread_id, user["id"]),
    )


@router.post("/chat", response_model=ChatResponse)
async def chat(body: ChatRequest, user: dict[str, Any] = Depends(auth.current_user)) -> ChatResponse:
    thread = _own_thread(body.thread_id, user) if body.thread_id else None
    settings = auth.load_connection(user["id"])
    if settings is None:
        return ChatResponse(messages=[ErrorMessage(message=NOT_CONNECTED)])

    # A thread decides its own workflow, so a request can't aim one thread at another workflow.
    workflow_id = thread["workflow_id"] if thread else body.workflow_id
    if thread is None:
        thread = (
            db.get_or_create_workflow_thread(user["id"], workflow_id)
            if workflow_id
            else db.get_or_create_blank_thread(user["id"])
        )

    client = build_client(settings["n8n_url"], settings["api_key"])
    try:
        state = await run_agent(
            client, user["id"], body.message, workflow_id, thread_id=thread["id"]
        )
    finally:
        await client.aclose()

    # Naming a workflow in a general chat moves the exchange to that workflow's thread.
    named_workflow = state.get("workflow_id")
    if not thread["workflow_id"] and named_workflow:
        thread = db.get_or_create_workflow_thread(
            user["id"], named_workflow, state.get("workflow_name")
        )

    messages = state.get("messages") or []
    db.append_thread_messages(
        thread["id"],
        [("user", {"content": body.message}), *(("agent", message) for message in messages)],
    )
    return ChatResponse(messages=messages, thread_id=thread["id"])


@router.post("/fix/{proposal_id}/approve", response_model=RepairResult)
async def approve(
    proposal_id: str,
    user: dict[str, Any] = Depends(auth.current_user),
    client: N8nClient = Depends(connected_client),
) -> RepairResult:
    result = await repair.approve(proposal_id, user["id"], client)
    return repair.record_result(proposal_id, user["id"], result)


@router.post("/fix/{proposal_id}/reject", response_model=OkResponse)
async def reject(proposal_id: str, user: dict[str, Any] = Depends(auth.current_user)) -> OkResponse:
    repair.reject(proposal_id, user["id"])
    return OkResponse(ok=True)


@router.post("/fix/{proposal_id}/done", response_model=RepairResult)
async def done(
    proposal_id: str,
    user: dict[str, Any] = Depends(auth.current_user),
    client: N8nClient = Depends(connected_client),
) -> RepairResult:
    result = await repair.complete_manual(proposal_id, user["id"], client)
    return repair.record_result(proposal_id, user["id"], result)


@router.get("/incidents", response_model=list[Incident])
async def incidents(user: dict[str, Any] = Depends(auth.current_user)) -> list[dict[str, Any]]:
    return db.list_incidents(user["id"])


class WebhookExecution(BaseModel):
    id: str | int | None = None


class WebhookWorkflow(BaseModel):
    id: str | int | None = None
    name: str | None = None


class WebhookPayload(BaseModel):
    execution: WebhookExecution | None = None
    workflow: WebhookWorkflow | None = None
    error: Any = None


# The random token in the URL is the only credential, so treat the URL as a secret.
@router.post("/webhook/n8n-error/{token}")
async def n8n_error(
    token: str, payload: WebhookPayload, background: BackgroundTasks
) -> dict[str, bool]:
    user = db.get_user_by_webhook_token(token)
    if user is None:
        raise HTTPException(404, "Unknown webhook URL.")
    # Diagnosis can take a while, and n8n's Error Trigger workflow shouldn't wait on it.
    background.add_task(process_failure, user["id"], payload)
    return {"ok": True}


async def process_failure(user_id: str, payload: WebhookPayload) -> None:
    workflow_name = (payload.workflow.name if payload.workflow else None) or "A workflow"
    workflow_id = payload.workflow.id if payload.workflow else None
    execution_id = payload.execution.id if payload.execution else None

    settings = auth.load_connection(user_id)
    chat_id = settings.get("telegram_chat_id") if settings else None
    if settings is None or execution_id is None or workflow_id is None:
        await telegram.send_message(NOT_DIAGNOSED.format(name=workflow_name), chat_id)
        return

    client = build_client(settings["n8n_url"], settings["api_key"])
    try:
        state = await run_agent(
            client, user_id, workflow_id=str(workflow_id), webhook_execution_id=str(execution_id)
        )
    except Exception:
        logger.exception("Webhook diagnosis crashed for execution %s", execution_id)
        state = {"failed": True}
    finally:
        await client.aclose()

    if state.get("created"):
        context = state["context"]
        thread = db.get_or_create_workflow_thread(
            user_id, context.workflow_id, context.workflow_name
        )
        db.append_thread_messages(
            thread["id"], [("agent", message) for message in state.get("messages") or []]
        )
        proposal = next(
            (m for m in state.get("messages") or [] if m.get("type") == "proposal"), None
        )
        text = telegram.format_alert(
            context.workflow_name,
            context.failed_node,
            state["diagnosis"].root_cause,
            proposal["explanation"] if proposal else None,
        )
        message_id = await telegram.send_alert(
            text, chat_id, proposal["proposal_id"] if proposal else None
        )
        # Replies find their fix through the alert they answer, manual fixes included.
        saved = next(
            (m["proposal_id"] for m in state.get("messages") or [] if m.get("proposal_id")), None
        )
        if message_id and saved and chat_id:
            db.set_proposal_alert(saved, chat_id, message_id)
        return
    # A retry or a repeat of an open incident stays quiet; a failed diagnosis still gets an alert.
    has_error = any(m.get("type") == "error" for m in state.get("messages") or [])
    if state.get("failed") or has_error:
        await telegram.send_message(NOT_DIAGNOSED.format(name=workflow_name), chat_id)


def require_telegram_secret(
    x_telegram_bot_api_secret_token: str = Header(default=""),
) -> None:
    secret = get_settings().telegram_webhook_secret
    if not secret:
        raise HTTPException(503, "Telegram replies aren't configured.")
    if not hmac.compare_digest(x_telegram_bot_api_secret_token.encode(), secret.encode()):
        raise HTTPException(401, "Invalid secret.")


@router.post("/telegram/webhook", dependencies=[Depends(require_telegram_secret)])
async def telegram_webhook(update: dict[str, Any], background: BackgroundTasks) -> dict[str, bool]:
    # Unused while the server polls, and off unless TELEGRAM_WEBHOOK_SECRET is set.
    # A repair can take a while, and Telegram resends updates it doesn't get a quick answer to.
    background.add_task(process_telegram_update, update)
    return {"ok": True}


async def process_telegram_update(update: dict[str, Any]) -> None:
    try:
        await telegram_bot.handle_update(update, build_client)
    except Exception:
        logger.exception("Telegram update failed")
