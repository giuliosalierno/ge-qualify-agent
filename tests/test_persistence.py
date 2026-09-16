"""Tests for Phase 3 persistence sinks: LocalRecordStore and GoogleSheetsInventorySink."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from qualify.sinks.record_store import LocalRecordStore
from qualify.sinks.session import new_session
from qualify.sinks.sheets import GoogleSheetsInventorySink, record_to_sheet_row


def test_local_record_store_persists_and_reloads_session_and_record(tmp_path: Path) -> None:
    store = LocalRecordStore(tmp_path)
    session = new_session("ctx-persist-123", pack_name="business", record_id="UC-2026-TEST01")
    session.record.meta.initiative_name = "Sales Order Automation"
    session.committed.add(0)
    session.next_surface_id()

    store.save(session)

    # Reload session by context_id
    loaded_session = store.load("ctx-persist-123")
    assert loaded_session is not None
    assert loaded_session.context_id == "ctx-persist-123"
    assert loaded_session.record.meta.record_id == "UC-2026-TEST01"
    assert loaded_session.record.meta.initiative_name == "Sales Order Automation"
    assert loaded_session.committed == {0}
    assert loaded_session.stage_surface_ids[0].startswith("qualify-s0-")

    # Reload standalone record by record_id (used by Agent 2 handover)
    loaded_rec = store.load_record("UC-2026-TEST01")
    assert loaded_rec is not None
    assert loaded_rec.meta.initiative_name == "Sales Order Automation"


def test_google_sheets_inventory_sink_upserts_row() -> None:
    session = new_session("ctx-sheet-123", record_id="UC-2026-SHEET1")
    session.record.meta.initiative_name = "HR Policy Concierge"
    session.record.business.user_count = 100

    row = record_to_sheet_row(session.record)
    assert row[0] == "UC-2026-SHEET1"
    assert row[2] == "HR Policy Concierge"
    assert row[7] == 100

    # Mock the Google Sheets API client
    mock_service = MagicMock()
    mock_values = mock_service.spreadsheets.return_value.values.return_value
    mock_values.get.return_value.execute.return_value = {"values": [["Record ID"], ["UC-2026-OTHER"]]}

    sink = GoogleSheetsInventorySink("fake-sheet-id", service=mock_service)
    ok = sink.upsert_record(session.record)
    assert ok is True
    mock_values.append.assert_called_once()
