"""Starlette HTTP application and server entrypoint for the Qualification Agent."""

from __future__ import annotations

import logging
import os

from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from starlette.routing import Route
import uvicorn

from qualify.agent.card import build_agent_card
from qualify.agent.executor import QualifyAgentExecutor
from qualify.agent.task_store import ReopenableTaskStore
from qualify.agent.turn import GeminiChatClient
from qualify.a2ui.patcher import GeminiExtractionClient
from qualify.mcp import (
    handle_mcp_request,
    handle_oauth_auth,
    handle_oauth_callback,
    handle_oauth_exchange,
    handle_oauth_status,
    handle_oauth_token,
)
from qualify.sinks.record_store import create_default_store

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)
log = logging.getLogger(__name__)


#: Gemini Enterprise calls this agent over ``message/stream`` (SSE), not
#: ``message/send``. Observed in production on 2026-09-16:
#:
#:     A2A transport: message/stream (SSE, mid-turn push may be possible)
#:
#: That matters because it is the precondition for announcing a background
#: event — a completed OAuth sign-in, say — without waiting for the user to
#: speak. A unary request could never do it.
#:
#: The subclass that produced that log line has been removed. It overrode
#: `on_message_send_stream` and re-yielded from `super()`, which stops
#: `GeneratorExit` reaching the base generator directly. The base relies on
#: catching it to drain the queue, and on a `finally` to schedule producer
#: cleanup; wrapped, the inner generator is finalised by the garbage collector
#: instead, whenever that happens to be. The turn after the wrapped one failed
#: inside Gemini Enterprise without ever reaching this service.
#:
#: If this ever needs re-measuring, log from `on_message_send` only — it
#: returns a value, so wrapping it is safe — or read the JSON-RPC method in
#: ASGI middleware. Do not wrap the streaming generator.


def build_app():
    """Builds the Starlette application with A2A protocol routes, SharePoint MCP routes, and CORS."""
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", 8080))
    base_url = os.environ.get("AGENT_URL", f"http://localhost:{port}")
    model_name = os.environ.get("MODEL", "gemini-3.8-flash")

    # Use Gemini extraction and chat clients if credentials / environment permits
    extraction_client = None
    chat_client = None
    try:
        extraction_client = GeminiExtractionClient(model=model_name)
        chat_client = GeminiChatClient(model=model_name)
        log.info("Initialized GeminiExtractionClient and GeminiChatClient with model %s", model_name)
    except Exception as exc:
        log.warning("Gemini clients initialization deferred/failed: %s", exc)

    agent_card = build_agent_card(base_url)
    executor = QualifyAgentExecutor(
        agent_card=agent_card,
        session_store=create_default_store(),
        extraction_client=extraction_client,
        chat_client=chat_client,
    )

    handler = DefaultRequestHandler(
        agent_executor=executor,
        # A2UI buttons dispatch against the task that drew them, and every turn
        # ends `completed`. See task_store.py for why that state cannot change
        # and why the fix belongs here.
        task_store=ReopenableTaskStore(InMemoryTaskStore()),
    )
    server = A2AStarletteApplication(agent_card=agent_card, http_handler=handler)
    app = server.build()

    # Mount SharePoint MCP & OAuth 2.0 endpoints on the same server
    app.routes.extend(
        [
            Route("/mcp", handle_mcp_request, methods=["POST", "GET", "OPTIONS"]),
            Route("/auth", handle_oauth_auth, methods=["GET", "POST"]),
            Route("/auth/status", handle_oauth_status, methods=["GET"]),
            Route("/auth/exchange", handle_oauth_exchange, methods=["POST"]),
            Route("/auth/callback", handle_oauth_callback, methods=["GET", "POST"]),
            Route("/token", handle_oauth_token, methods=["POST", "GET"]),
        ]
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"https?://.*",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    log.info("Qualification Agent app built (A2A + SharePoint MCP). base_url=%s", base_url)
    return app, host, port


def serve() -> None:
    """Runs uvicorn server."""
    app, host, port = build_app()
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    serve()
