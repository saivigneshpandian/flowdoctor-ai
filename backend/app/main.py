import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app import auth, telegram
from app.api.routes import process_telegram_update, router
from app.n8n.client import N8nError
from app.services import telegram_bot
from app.services.repair import ProposalNotFound, ProposalStateError

logging.basicConfig(level=logging.INFO)
# httpx logs full request URLs at INFO, and Telegram URLs contain the bot token.
logging.getLogger("httpx").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    auth.bootstrap()
    poller = None
    if telegram.polling_enabled():
        poller = asyncio.create_task(telegram_bot.poll_updates(process_telegram_update))
        logging.getLogger(__name__).info("Telegram polling started.")
    yield
    if poller:
        poller.cancel()
        await asyncio.gather(poller, return_exceptions=True)


class NoStoreResponses:
    """Stops any proxy or CDN in front of the API from caching one user's data for another."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_no_store(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["Cache-Control"] = "no-store"
            await send(message)

        await self.app(scope, receive, send_no_store)


app = FastAPI(title="FlowDoctor AI", lifespan=lifespan)
app.add_middleware(NoStoreResponses)
app.include_router(router)


@app.exception_handler(N8nError)
async def handle_n8n_error(request: Request, exc: N8nError) -> JSONResponse:
    return JSONResponse(status_code=502, content={"detail": exc.message})


@app.exception_handler(ProposalNotFound)
async def handle_not_found(request: Request, exc: ProposalNotFound) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(ProposalStateError)
async def handle_state_error(request: Request, exc: ProposalStateError) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc)})
