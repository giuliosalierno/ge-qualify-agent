FROM python:3.12-slim

WORKDIR /app

# Install uv package manager
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Copy dependency definitions first for Docker layer caching
COPY pyproject.toml uv.lock ./

# Install dependencies into /app/.venv at build time (zero runtime downloads)
RUN uv sync --frozen --no-dev --no-install-project

# Copy application source
COPY agent/ ./agent/
# Appended to the system instruction by qualify/agent/turn.py:load_instructions.
COPY skills/ge_capability_grounding/ ./skills/ge_capability_grounding/
COPY qualify/ ./qualify/

# Install the local package
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"
ENV PORT=8080

EXPOSE 8080

CMD ["start"]
