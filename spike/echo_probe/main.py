"""Server entry point for the echo probe.

Runs locally with `uv run --directory spike/echo_probe .` and on Cloud Run via
the `start` script declared in pyproject.toml.
"""

import logging
import os

from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from dotenv import load_dotenv
from probe_agent import build_agent_card
from probe_executor import EchoProbeExecutor
from starlette.middleware.cors import CORSMiddleware
import uvicorn

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


def build_app():
    host = "0.0.0.0"
    port = int(os.environ.get("PORT", 8080))

    # On Cloud Run the public URL is injected as AGENT_URL after the service
    # exists; the card must advertise that, not the container's bind address.
    base_url = os.environ.get("AGENT_URL", f"http://localhost:{port}")

    agent_card = build_agent_card(base_url)
    handler = DefaultRequestHandler(
        agent_executor=EchoProbeExecutor(agent_card),
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

    logger.info("Echo probe ready. base_url=%s", base_url)
    return app, host, port


def serve():
    app, host, port = build_app()
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    serve()
