"""Supply-chain rules from go/pip-install-remediation (go/demos requirement).

Every dependency, transitive ones included, must be pinned with hashes and
installed with ``--require-hashes``.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
REQS = ROOT / "requirements.txt"


def _requirements() -> list[str]:
    """Logical requirement entries with their continuation lines joined."""
    text = REQS.read_text(encoding="utf-8").replace("\\\n", " ")
    return [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def test_dockerfile_installs_with_require_hashes() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    pip_lines = [line for line in dockerfile.splitlines() if "pip install" in line]
    assert pip_lines, "the image must install dependencies with pip"
    assert all("--require-hashes" in line for line in pip_lines)
    assert "uv sync" not in dockerfile


def test_every_requirement_is_pinned_and_hashed() -> None:
    entries = _requirements()
    assert entries
    for entry in entries:
        assert re.match(r"^[A-Za-z0-9_.\-\[\]]+==\S+", entry), entry
        assert "--hash=sha256:" in entry, entry


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv not installed")
def test_requirements_match_the_lockfile() -> None:
    exported = subprocess.run(
        ["uv", "export", "--frozen", "--no-dev", "--no-emit-project", "--format", "requirements-txt"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout

    def body(text: str) -> list[str]:
        return [line for line in text.splitlines() if not line.startswith("#")]

    assert body(exported) == body(REQS.read_text(encoding="utf-8")), (
        "requirements.txt is stale; regenerate with: uv export --frozen --no-dev "
        "--no-emit-project --format requirements-txt -o requirements.txt"
    )
