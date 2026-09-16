"""Starlette HTTP application and server entrypoint for the Qualification Agent."""

from __future__ import annotations

import logging
import os

from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
import uvicorn

from qualify.agent.card import build_agent_card
from qualify.agent.executor import QualifyAgentExecutor
from qualify.agent.turn import GeminiChatClient
from qualify.a2ui.patcher import GeminiExtractionClient
from qualify.sinks.session import InMemorySessionStore

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)
log = logging.getLogger(__name__)


def build_app():
    """Builds the Starlette application with A2A protocol routes and CORS."""
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
        session_store=InMemorySessionStore(),
        extraction_client=extraction_client,
        chat_client=chat_client,
    )

    handler = DefaultRequestHandler(
        agent_executor=executor,
        task_store=InMemoryTaskStore(),
    )
    server = A2AStarletteApplication(agent_card=agent_card, http_handler=handler)
    app = server.build()

    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"https?://.*",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    log.info("Qualification Agent app built. base_url=%s", base_url)
    return app, host, port


def serve() -> None:
    """Runs uvicorn server."""
    app, host, port = build_app()
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    serve()
