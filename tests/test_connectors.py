"""The connector catalog: every native-connector claim must be citable."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from qualify.schema.capability import CapabilityLevel
from qualify.schema.use_case_record import Business, Meta, Technical, UseCaseRecord
from qualify.scoring.business_tier import classify_capability
from qualify.scoring.connectors import catalog_marker, load_catalog, resolve
from qualify.scoring.portfolio import evaluate_opportunity

ROOT = Path(__file__).resolve().parent.parent
DOC_PREFIX = "https://cloud.google.com/gemini/enterprise/docs/connectors/"


@pytest.mark.parametrize(
    ("name", "cid", "status"),
    [
        ("internal_api", "internal_api", "custom"),
        ("sap", "sap", "custom"),
        ("Workday HCM", "workday", "custom"),
        ("Oracle NetSuite", "oracle_netsuite", "native"),
        ("Oracle database", "oracle", "custom"),
        ("Cloud SQL", "cloud_sql", "native"),
        ("on-prem Postgres", "self_hosted_database", "custom"),
        ("SharePoint Online", "sharepoint", "native"),
        ("gcs", "gcs", "native"),
    ],
)
def test_resolve_places_sources(name: str, cid: str, status: str) -> None:
    m = resolve(name)
    assert (m.id, m.status) == (cid, status)


def test_unknown_names_are_unverified_not_native() -> None:
    m = resolve("Tagetik")
    assert m.status == "unverified"
    assert m.id is None and not m.native


def test_every_native_entry_cites_an_official_page() -> None:
    cat = load_catalog()
    assert cat.source_index.startswith(DOC_PREFIX)
    assert cat.custom_mcp_doc.startswith(DOC_PREFIX)
    for cid, entry in cat.entries.items():
        assert entry["status"] in ("native", "custom", "unverified"), cid
        if entry["status"] == "native":
            assert str(entry.get("doc", "")).startswith(DOC_PREFIX), cid
        if entry.get("actions"):
            assert entry["status"] == "native", f"{cid}: actions need a native connector"


def test_every_intake_option_is_in_the_catalog() -> None:
    """The Stage 1 checklist must never offer a source the catalog can't place."""
    pack = yaml.safe_load((ROOT / "qualify/packs/business.yaml").read_text())
    values = {opt["value"] for opt in pack["option_sets"]["data_sources"]}
    assert values, "data_sources options not found in business.yaml"
    cat = load_catalog()
    for value in values - {"other", "unknown"}:
        assert value in cat.entries, value


def _seed(record_id: str) -> UseCaseRecord:
    data = json.loads((ROOT / f"click-to-deploy/demo/seed/records/{record_id}.json").read_text())
    data.pop("derived", None)
    return UseCaseRecord.model_validate(data)


def test_kyc_internal_api_is_not_a_native_connector() -> None:
    """Audit bug: gcs + internal_api was scored as all-native (L3, Quick Win)."""
    rec = _seed("UC-2026-DEMO03")
    rec.technical.capability_level = CapabilityLevel.WORKFLOW_BUILDER_CHAT_AGENT
    rec.technical.capability_rationale = "legacy rationale without a catalog marker"

    ev = evaluate_opportunity(rec)

    assert rec.technical.capability_level == CapabilityLevel.WORKFLOW_AGENT_WITH_CUSTOM_MCP
    assert ev.feasibility_score == 3
    assert ev.quadrant == "Strategic Bets"
    rationale = rec.technical.capability_rationale or ""
    assert catalog_marker() in rationale
    assert DOC_PREFIX + "custom-mcp-server/" in rationale
    assert DOC_PREFIX in rationale


def test_current_classification_is_kept() -> None:
    rec = UseCaseRecord(
        meta=Meta(record_id="UC-2026-KEEP01", initiative_name="Keep"),
        business=Business(problem_description="Summarise policies."),
        technical=Technical(data_sources=["google_drive"]),
    )
    classify_capability(rec)
    first = rec.technical.capability_rationale
    rec.technical.capability_level = CapabilityLevel.HIGH_CODE_AGENT  # architect override
    classify_capability(rec)
    assert rec.technical.capability_level == CapabilityLevel.HIGH_CODE_AGENT
    assert rec.technical.capability_rationale == first


def test_labels_keep_their_casing() -> None:
    rec = UseCaseRecord(
        meta=Meta(record_id="UC-2026-LBL01", initiative_name="Labels"),
        business=Business(problem_description="Find answers."),
        technical=Technical(data_sources=["sharepoint"]),
    )
    classify_capability(rec)
    rationale = rec.technical.capability_rationale or ""
    assert resolve("sharepoint").label in rationale
    assert "Sharepoint" not in rationale


def test_unsized_single_user_record_scores_value_1() -> None:
    """Audit bug: 1 user, no hours -> raw 1.5 -> round() gave 2."""
    rec = UseCaseRecord(
        meta=Meta(record_id="UC-2026-TINY01", initiative_name="test"),
        business=Business(
            problem_description="test",
            user_count=1,
            expected_impacts="Faster turnaround for the team and fewer manual errors overall.",
        ),
    )
    assert evaluate_opportunity(rec).business_value_score == 1
