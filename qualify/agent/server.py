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


class _TransportLoggingRequestHandler(DefaultRequestHandler):
    """Records which A2A transport Gemini Enterprise actually uses.

    This decides whether the agent can ever announce a background event, such
    as a completed OAuth sign-in, without the user speaking first:

    * ``message/send`` — one request, one response body. The turn is over before
      anything else can happen. Pushing a later message is impossible.
    * ``message/stream`` — SSE. The agent may hold the task open and emit
      further events, so a "you're connected" bubble could appear on its own.

    The agent card declares ``streaming: true``, but that advertises what we
    accept, not what the client chooses. Only the logs can settle it.
    """

    async def on_message_send(self, params, context=None):  # type: ignore[override]
        log.info("A2A transport: message/send (unary, no mid-turn push possible)")
        return await super().on_message_send(params, context)

    async def on_message_send_stream(self, params, context=None):  # type: ignore[override]
        log.info("A2A transport: message/stream (SSE, mid-turn push may be possible)")
        async for event in super().on_message_send_stream(params, context):
            yield event

    async def on_resubscribe_to_task(self, params, context=None):  # type: ignore[override]
        # Spec 7.6.2 says a client SHOULD resubscribe after out-of-band auth.
        # If this ever fires, GE does poll after all and the sign-in
        # notification can be delivered properly.
        log.info("A2A transport: tasks/resubscribe — client IS re-subscribing")
        async for event in super().on_resubscribe_to_task(params, context):
            yield event


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

    handler = _TransportLoggingRequestHandler(
        agent_executor=executor,
        task_store=InMemoryTaskStore(),
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
