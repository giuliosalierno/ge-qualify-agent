"""Optional Google Sheets Inventory sink for completed UseCaseRecords.

When `INVENTORY_SHEET_ID` is configured in the environment, completed use case
records are automatically appended/upserted to the target Google Sheet.

Note on Licensing:
- A GCP Service Account can call the Google Sheets API free of charge without
  needing a paid Google Workspace seat.
- However, the spreadsheet itself must live in a Google Drive account (either
  a Google Workspace Shared Drive / user Drive or a consumer Google account)
  that grants Editor access to the Cloud Run Service Account email.
- For customers without Google Workspace or Drive sharing enabled,
  `GCSRecordStore` (`qualify/sinks/record_store.py`) acts as the canonical,
  zero-license-dependency persistence store.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from qualify.schema.use_case_record import UseCaseRecord

log = logging.getLogger(__name__)

INVENTORY_COLUMNS = [
    "Record ID",
    "Submission Date",
    "Initiative Name",
    "Sponsor Name",
    "Sponsor Email",
    "Department",
    "Target User Role",
    "User Count",
    "Task Frequency (Weekly)",
    "Baseline Mins / Task",
    "Target Mins Saved / Task",
    "Annual Hours Saved / User",
    "Total Annual Team Hours Saved",
    "Data Classification",
    "Recommended Capability Level",
    "Delivery Tier",
    "Gate 1 Decision",
]


def record_to_sheet_row(record: UseCaseRecord) -> list[Any]:
    """Flattens a UseCaseRecord into a 17-column row for the Use Case Inventory Sheet."""
    from qualify.scoring.business_tier import classify_capability  # noqa: PLC0415

    classify_capability(record)
    m = record.meta
    b = record.business
    s = record.sizing
    d = record.derived
    p = record.proposed
    t = record.technical

    cap = t.capability_level
    tier = d.delivery_tier

    return [
        m.record_id or "",
        str(m.submission_date or ""),
        m.initiative_name or "",
        p.executive_sponsor or "",
        m.submitter or "",
        m.department_bu or "",
        b.user_profile or "",
        b.user_count or 0,
        s.task_frequency_weekly or 0,
        s.baseline_minutes_per_task or 0,
        s.target_minutes_saved_per_task or 0,
        round(d.annual_hours_saved_per_user or 0.0, 1),
        round(d.total_annual_team_hours_saved or 0.0, 1),
        t.security.data_classification or "",
        cap.value if cap else "",
        tier.value if tier else "",
        "Ready for CoE Review",
    ]


class GoogleSheetsInventorySink:
    """Appends or updates a row in the master Use Case Inventory Google Sheet."""

    def __init__(self, spreadsheet_id: str, worksheet_name: str = "Inventory", service: Any = None) -> None:
        self.spreadsheet_id = spreadsheet_id
        self.worksheet_name = worksheet_name
        self._service = service

    def _get_service(self) -> Any:
        if self._service is not None:
            return self._service
        import google.auth  # type: ignore[import-untyped] # noqa: PLC0415
        from googleapiclient.discovery import build  # type: ignore[import-untyped] # noqa: PLC0415

        creds, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
        self._service = build("sheets", "v4", credentials=creds, cache_discovery=False)
        return self._service

    def upsert_record(self, record: UseCaseRecord) -> bool:
        """Writes or updates the row for `record.meta.record_id`. Returns True on success."""
        try:
            service = self._get_service()
            row_values = record_to_sheet_row(record)
            range_name = f"{self.worksheet_name}!A:Q"

            # Read existing IDs in column A to check if this record_id already exists
            result = (
                service.spreadsheets()
                .values()
                .get(spreadsheetId=self.spreadsheet_id, range=f"{self.worksheet_name}!A:A")
                .execute()
            )
            existing_rows = result.get("values", [])
            target_row_idx: int | None = None
            for idx, r in enumerate(existing_rows):
                if r and r[0] == record.meta.record_id:
                    target_row_idx = idx + 1  # 1-indexed
                    break

            if target_row_idx is not None:
                update_range = f"{self.worksheet_name}!A{target_row_idx}:Q{target_row_idx}"
                service.spreadsheets().values().update(
                    spreadsheetId=self.spreadsheet_id,
                    range=update_range,
                    valueInputOption="USER_ENTERED",
                    body={"values": [row_values]},
                ).execute()
                log.info("Updated row %d in Google Sheet %s for record %s", target_row_idx, self.spreadsheet_id, record.meta.record_id)
            else:
                service.spreadsheets().values().append(
                    spreadsheetId=self.spreadsheet_id,
                    range=range_name,
                    valueInputOption="USER_ENTERED",
                    insertDataOption="INSERT_ROWS",
                    body={"values": [row_values]},
                ).execute()
                log.info("Appended row to Google Sheet %s for record %s", self.spreadsheet_id, record.meta.record_id)
            return True
        except Exception as exc:
            log.warning("Google Sheets upsert failed for %s: %s", record.meta.record_id, exc)
            return False


def sync_to_optional_sheet(record: UseCaseRecord) -> bool:
    """If INVENTORY_SHEET_ID is set, upserts the record to Google Sheets."""
    sheet_id = os.environ.get("INVENTORY_SHEET_ID", "").strip()
    if not sheet_id:
        return False
    sink = GoogleSheetsInventorySink(sheet_id)
    return sink.upsert_record(record)
