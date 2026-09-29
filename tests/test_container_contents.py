"""The container must ship every file the agent reads at runtime."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_dockerfile_copies_runtime_prompt_files() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY agent/ ./agent/" in dockerfile
    assert "COPY skills/ge_capability_grounding/ ./skills/ge_capability_grounding/" in dockerfile


def test_gcloudignore_keeps_the_grounding_skill_in_the_upload() -> None:
    lines = [
        line.strip()
        for line in (ROOT / ".gcloudignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    # A bare `skills/` excludes the directory itself, and a later negation
    # cannot re-include a file under an excluded directory.
    assert "skills/" not in lines
    assert "!skills/ge_capability_grounding/" in lines
