"""Generates the synthetic seed data uploaded by the Click-to-Deploy Terraform.

All companies, people and numbers are fictional (Cymbal is Google's standard
fictional brand). No customer or internal data.

The files are written through ``LocalRecordStore`` so they are byte-for-byte
the format the agent itself writes to GCS (``records/`` + ``portfolio/``).

Run from the repo root after changing the schema, then commit the output:

    uv run python click-to-deploy/demo/seed/generate_seed.py
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from qualify.schema.capability import CapabilityLevel  # noqa: E402
from qualify.schema.use_case_record import (  # noqa: E402
    Business,
    Grounding,
    Meta,
    Network,
    Proposed,
    Security,
    Sizing,
    SystemEntry,
    Technical,
    UseCaseRecord,
)
from qualify.sinks.record_store import LocalRecordStore  # noqa: E402
from qualify.sinks.session import Session  # noqa: E402

OUT = Path(__file__).resolve().parent


def _claims_triage() -> UseCaseRecord:
    return UseCaseRecord(
        meta=Meta(
            record_id="UC-2026-DEMO01",
            initiative_name="Claims intake triage",
            department_bu="Cymbal Insurance - Claims Operations",
            submitter="Alex Rivera (demo persona)",
        ),
        business=Business(
            user_profile="Claims handlers",
            user_count=120,
            problem_description=(
                "Handlers read every first notice of loss email and its attachments "
                "to classify severity and route the claim, which delays urgent claims."
            ),
            user_stories=(
                "As a claims handler I want new claims summarised and routed with a "
                "severity suggestion so I can act on urgent ones first."
            ),
            expected_impacts="Faster routing of urgent claims; less manual reading",
        ),
        sizing=Sizing(
            task_frequency_weekly=40,
            baseline_minutes_per_task=12,
            target_minutes_saved_per_task=7,
        ),
        technical=Technical(
            data_sources=["workspace_mail", "servicenow"],
            capability_level=CapabilityLevel.WORKFLOW_BUILDER_WORKFLOW_AGENT,
            capability_rationale="Multi-step routing with a write-back to the ticketing system.",
        ),
        proposed=Proposed(
            business_owner="Head of Claims Operations",
            executive_sponsor="COO",
            adoption_kpi="Median time to route an urgent claim",
        ),
    )


def _store_policy_assistant() -> UseCaseRecord:
    return UseCaseRecord(
        meta=Meta(
            record_id="UC-2026-DEMO02",
            initiative_name="Store policy assistant",
            department_bu="Cymbal Retail - Store Operations",
            submitter="Sam Okafor (demo persona)",
        ),
        business=Business(
            user_profile="Store managers",
            user_count=450,
            problem_description=(
                "Store managers search several intranet sites for returns, staffing "
                "and safety policies and often call the regional office instead."
            ),
            user_stories="As a store manager I want a cited answer to a policy question in seconds.",
            expected_impacts="Fewer calls to the regional office; consistent policy answers",
        ),
        sizing=Sizing(
            task_frequency_weekly=15,
            baseline_minutes_per_task=10,
            target_minutes_saved_per_task=8,
        ),
        technical=Technical(
            data_sources=["google_drive"],
            capability_level=CapabilityLevel.CUSTOM_SKILL,
            capability_rationale="Read-only Q&A over documents already in Google Drive.",
            data_freshness="Policies updated monthly",
            landing_zone_status="Existing Google Cloud organisation with Gemini Enterprise",
            systems=[
                SystemEntry(
                    name="Policy library (Google Drive)",
                    function="Policy documents",
                    hosting_location="Google Workspace",
                    interface="Native Gemini Enterprise connector",
                    data_format="Google Docs and PDF",
                    schema_status="Unstructured documents",
                    system_owner="Retail Operations Excellence",
                )
            ],
            network=Network(
                hosting_environments="SaaS (Google Workspace)",
                transit_path="Google-native, no network transit",
                firewall_proxy_status="Not applicable",
                transit_blocker_status="None",
            ),
            security=Security(
                user_authentication="Google identity (Cloud Identity)",
                service_authentication="Native connector, no service credentials",
                iam_least_privilege="Document permissions inherited from Drive",
                data_classification="internal",
                cloud_policy_status="Approved for Gemini Enterprise",
                residency_requirements="None",
            ),
            grounding=Grounding(
                acl_preservation_required=True,
                model_profile="Flash",
                citation_policy="Every answer cites the source policy",
                query_volume="About 7,000 queries per week",
                latency_sla="Interactive, under 5 seconds",
            ),
        ),
        proposed=Proposed(
            business_owner="VP Store Operations",
            executive_sponsor="Chief Retail Officer",
            tech_owner="Workplace Technology lead",
            production_catcher_team="Workplace Technology",
            adoption_kpi="Weekly active store managers",
        ),
    )


def _kyc_review() -> UseCaseRecord:
    return UseCaseRecord(
        meta=Meta(
            record_id="UC-2026-DEMO03",
            initiative_name="KYC document pre-check",
            department_bu="Cymbal Bank - Onboarding",
            submitter="Priya Nair (demo persona)",
        ),
        business=Business(
            user_profile="Onboarding analysts",
            user_count=60,
            problem_description=(
                "Analysts check every onboarding pack for missing or expired "
                "documents before the compliance review, and packs bounce back often."
            ),
            user_stories="As an onboarding analyst I want missing documents flagged before review.",
            expected_impacts="Fewer rejected onboarding packs; faster account opening",
        ),
        sizing=Sizing(
            task_frequency_weekly=25,
            baseline_minutes_per_task=20,
            target_minutes_saved_per_task=10,
        ),
        technical=Technical(
            data_sources=["gcs", "internal_api"],
            capability_level=CapabilityLevel.HIGH_CODE_AGENT,
            capability_rationale="Document AI extraction plus checks against an internal API.",
        ),
        proposed=Proposed(
            business_owner="Head of Onboarding",
            executive_sponsor="Chief Operating Officer",
            adoption_kpi="First-time-right rate of onboarding packs",
        ),
    )


def _write(store: LocalRecordStore, record: UseCaseRecord, pack: str) -> None:
    """Saves `record` as a finished `pack` session, as the agent would."""
    session = Session(context_id=f"seed-{record.meta.record_id}-{pack}", pack_name=pack, record=record)
    session.committed = set(range(len(session.pack.stages)))
    store.save(session)


def main() -> None:
    for sub in ("records", "portfolio", "sessions"):
        shutil.rmtree(OUT / sub, ignore_errors=True)
    store = LocalRecordStore(OUT)

    _write(store, _claims_triage(), "business")  # waiting for technical review
    _write(store, _kyc_review(), "business")  # waiting for technical review
    policy = _store_policy_assistant()
    _write(store, policy, "business")
    _write(store, policy, "tech")  # fully qualified

    # Sessions are per conversation; a fresh demo should not inherit them.
    shutil.rmtree(OUT / "sessions", ignore_errors=True)
    print(f"Seed data written to {OUT}")


if __name__ == "__main__":
    main()
