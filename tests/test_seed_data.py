"""The Click-to-Deploy seed data must load exactly like agent-written records.

If the schema changes and the committed seed files drift, a fresh demo would
show an empty portfolio. Regenerate with
``uv run python click-to-deploy/demo/seed/generate_seed.py``.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from qualify.agent.handover import list_pending_reviews
from qualify.scoring.portfolio import evaluate_portfolio
from qualify.sinks.record_store import LocalRecordStore

SEED = Path(__file__).resolve().parent.parent / "click-to-deploy" / "demo" / "seed"


@pytest.fixture
def seeded(tmp_path: Path) -> LocalRecordStore:
    for sub in ("records", "portfolio"):
        shutil.copytree(SEED / sub, tmp_path / sub)
    return LocalRecordStore(tmp_path)


def test_every_seed_record_loads_and_scores(seeded: LocalRecordStore) -> None:
    items = seeded.load_portfolio_items()
    assert len(items) == 3
    summary = evaluate_portfolio(items)
    assert len(summary.evaluations) == 3


def test_two_seed_records_wait_for_technical_review(seeded, monkeypatch) -> None:
    monkeypatch.setenv("STORAGE_PROVIDER", "none")
    pending, reachable = list_pending_reviews(store=seeded)
    assert reachable
    assert sorted(e["recordId"] for e in pending) == ["UC-2026-DEMO01", "UC-2026-DEMO03"]


def test_seed_data_is_synthetic() -> None:
    """Fictional Cymbal companies and demo personas only."""
    for path in SEED.rglob("*.json"):
        text = path.read_text(encoding="utf-8")
        assert "@" not in text, f"{path.name} contains what looks like an email"
