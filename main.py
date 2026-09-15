"""Top-level entry point for ge-qualify-agent service."""

from qualify.agent.server import build_app, serve

if __name__ == "__main__":
    serve()
