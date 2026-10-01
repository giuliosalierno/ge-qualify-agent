FROM python:3.12-slim

WORKDIR /app

# Supply chain (go/pip-install-remediation): every dependency, including
# transitive ones, is pinned with hashes in requirements.txt and installed
# with --require-hashes. Regenerate after changing pyproject.toml / uv.lock:
#   uv export --frozen --no-dev --no-emit-project --format requirements-txt -o requirements.txt
COPY requirements.txt ./
RUN python -m venv /app/.venv \
 && /app/.venv/bin/pip install --no-cache-dir --require-hashes --only-binary=:all: -r requirements.txt

# Copy application source. It runs from source (PYTHONPATH), so no build
# backend has to be fetched outside the hashed requirements.
COPY agent/ ./agent/
# Appended to the system instruction by qualify/agent/turn.py:load_instructions.
COPY skills/ge_capability_grounding/ ./skills/ge_capability_grounding/
COPY qualify/ ./qualify/

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
ENV PORT=8080

# Run as an unprivileged user.
RUN useradd --uid 10001 --no-create-home appuser
USER appuser

EXPOSE 8080

CMD ["python", "-m", "qualify.agent.server"]
