"""Golden and property tests for the A2UI compiler.

The headline test is `test_every_pack_and_stage_compiles`: every pack, every
stage, validated against the vendored catalog. It is the one that stops a
pack edit from shipping a surface GE will reject.

The golden files under `tests/golden/` pin the exact JSON. Their value is not
correctness — the validators cover that — but *visibility*. A diff in a golden
file makes an accidental change to the wire format impossible to merge by
mistake. Regenerate them deliberately with:

    UPDATE_GOLDEN=1 uv run pytest tests/test_compiler.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from qualify.a2ui import compiler
from qualify.a2ui.catalog import A2UI_VERSION, catalog_id
from qualify.a2ui.validate import (
    SurfaceError,
    check_bindings,
    check_component_graph,
    validate_surface,
)
from qualify.packs.loader import load_all_packs
from qualify.schema.use_case_record import Meta, UseCaseRecord

GOLDEN_DIR = Path(__file__).parent / "golden"

PACKS = load_all_packs()
ALL_STAGES = [
    (name, idx, stage.id)
    for name, pack in PACKS.items()
    for idx, stage in enumerate(pack.stages)
]


@pytest.fixture
def empty_record() -> UseCaseRecord:
    return UseCaseRecord(meta=Meta(record_id="uc-0001", context_id="ctx-abc"))


@pytest.fixture
def sized_record() -> UseCaseRecord:
    """The worked example from ge_intake_business/SKILL.md.

    10 tasks a week x 6 minutes saved x 50 weeks x 20 users = 1,000 hours.
    """
    record = UseCaseRecord(meta=Meta(record_id="uc-0002"))
    record.business.user_count = 20
    record.sizing.task_frequency_weekly = 10
    record.sizing.baseline_minutes_per_task = 15
    record.sizing.target_minutes_saved_per_task = 6
    return record


# ---------------------------------------------------------------------------
# The headline test
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("pack_name,stage_idx,stage_id", ALL_STAGES)
def test_every_pack_and_stage_compiles(pack_name, stage_idx, stage_id, empty_record):
    messages = compiler.build_surface(PACKS[pack_name], empty_record, stage_idx)
    validate_surface(messages)


@pytest.mark.parametrize("pack_name,stage_idx,stage_id", ALL_STAGES)
def test_every_stage_binds_every_field(pack_name, stage_idx, stage_id):
    pack = PACKS[pack_name]
    components = compiler.build_stage_components(pack, stage_idx)
    bound = set(check_bindings(components))
    for field in pack.stages[stage_idx].fields:
        assert field.path in bound, f"{field.path} is declared but never bound"


# ---------------------------------------------------------------------------
# Message shape
# ---------------------------------------------------------------------------


def test_every_message_carries_the_wire_version(empty_record):
    for message in compiler.build_surface(PACKS["business"], empty_record, 0):
        assert message["version"] == A2UI_VERSION == "v0.9"


def test_surface_declares_the_ge_catalog(empty_record):
    create = compiler.build_surface(PACKS["business"], empty_record, 0)[0]
    assert create["createSurface"]["catalogId"] == catalog_id()
    assert "gemini_enterprise_composite_catalog" in create["createSurface"]["catalogId"]


def test_surface_order_is_create_components_then_data(empty_record):
    messages = compiler.build_surface(PACKS["business"], empty_record, 0)
    assert list(messages[0]) == ["version", "createSurface"]
    assert "updateComponents" in messages[1]
    assert "updateDataModel" in messages[2]


def test_out_of_range_stage_raises(empty_record):
    with pytest.raises(IndexError):
        compiler.build_surface(PACKS["business"], empty_record, 99)


def test_patch_is_standalone():
    """Phase 0 verified a bare updateDataModel moves the rendered field."""
    patch = compiler.build_patch("/uc/business/user_count", 20)
    assert patch == {
        "version": "v0.9",
        "updateDataModel": {
            "surfaceId": compiler.SURFACE_ID,
            "path": "/uc/business/user_count",
            "value": 20,
        },
    }


# ---------------------------------------------------------------------------
# The Continue button — D14
# ---------------------------------------------------------------------------


def test_continue_button_sends_the_whole_record():
    """sendDataModel is a no-op in GE, so the action context must carry it."""
    components = compiler.build_stage_components(PACKS["business"], 0)
    button = next(c for c in components if c["id"] == compiler._CONTINUE_ID)
    context = button["action"]["event"]["context"]

    assert button["action"]["event"]["name"] == compiler.COMMIT_STAGE
    assert context["data"] == {"path": "/uc"}
    assert context["stage"] == "needs"
    assert context["pack"] == "business"


def test_last_stage_button_says_submit():
    pack = PACKS["business"]
    last = len(pack.stages) - 1
    components = compiler.build_stage_components(pack, last)
    label = next(c for c in components if c["id"] == compiler._CONTINUE_LABEL_ID)
    assert label["text"] == "Submit"


def test_earlier_stage_buttons_say_continue():
    components = compiler.build_stage_components(PACKS["business"], 0)
    label = next(c for c in components if c["id"] == compiler._CONTINUE_LABEL_ID)
    assert label["text"] == "Continue"


# ---------------------------------------------------------------------------
# No Stepper
# ---------------------------------------------------------------------------


def test_progress_is_a_caption_not_a_stepper():
    """`Stepper` is not in the GE catalog, and tabs would break stage gating."""
    for idx in range(len(PACKS["business"].stages)):
        components = compiler.build_stage_components(PACKS["business"], idx)
        names = {c["component"] for c in components}
        assert "Stepper" not in names
        assert "Tabs" not in names
        assert "MaterialTabs" not in names


def test_stage_caption_reads_naturally():
    assert (
        compiler.stage_caption(PACKS["business"], 1)
        == "Stage 2 of 4 — Effort and value"
    )


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


def test_data_model_splits_record_from_display(sized_record):
    model = compiler.build_data_model(sized_record, PACKS["business"], 1)
    assert set(model) == {"uc", "ui"}
    assert model["uc"]["business"]["user_count"] == 20
    assert "summary" not in model["uc"]


def test_derived_reaches_the_display_tree(sized_record):
    model = compiler.build_data_model(sized_record, PACKS["business"], 1)
    assert "1,000 hours a year across the team" in model["ui"]["summary"]["hours_line"]
    assert "10 times a week" in model["ui"]["summary"]["hours_basis"]


def test_empty_sizing_prompts_instead_of_showing_zero(empty_record):
    """A zero would read as 'we measured no saving'. It means 'not yet sized'."""
    model = compiler.build_data_model(empty_record, PACKS["business"], 1)
    line = model["ui"]["summary"]["hours_line"]
    assert "0" not in line
    assert "Fill in" in line


def test_stage_state_tracks_the_active_stage(empty_record):
    model = compiler.build_data_model(empty_record, PACKS["business"], 2)
    assert model["ui"]["stage"] == {
        "index": 2,
        "count": 4,
        "id": "data",
        "caption": "Stage 3 of 4 — Data and systems",
    }


# ---------------------------------------------------------------------------
# Component ids and graph
# ---------------------------------------------------------------------------


def test_component_ids_are_derived_from_paths():
    assert (
        compiler.component_id("/uc/business/problem_description")
        == "f-uc-business-problem-description"
    )


def test_graph_check_catches_a_dangling_child():
    with pytest.raises(SurfaceError, match="not in the component list"):
        check_component_graph(
            [{"id": "root", "component": "Column", "children": ["ghost"]}]
        )


def test_graph_check_catches_an_orphan():
    with pytest.raises(SurfaceError, match="never referenced"):
        check_component_graph(
            [
                {"id": "root", "component": "Column", "children": []},
                {"id": "stray", "component": "Text", "text": "hi"},
            ]
        )


def test_binding_check_catches_a_typo():
    with pytest.raises(SurfaceError, match="does not resolve"):
        check_bindings(
            [
                {
                    "id": "x",
                    "component": "TextField",
                    "label": "X",
                    "value": {"path": "/uc/business/nope"},
                }
            ]
        )


# ---------------------------------------------------------------------------
# Golden files
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("pack_name,stage_idx,stage_id", ALL_STAGES)
def test_golden(pack_name, stage_idx, stage_id, empty_record):
    messages = compiler.build_surface(PACKS[pack_name], empty_record, stage_idx)
    actual = json.dumps(messages, indent=2, ensure_ascii=False) + "\n"

    path = GOLDEN_DIR / f"{pack_name}_{stage_idx}_{stage_id}.json"
    if os.environ.get("UPDATE_GOLDEN"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(actual, encoding="utf-8")
        pytest.skip(f"rewrote {path.name}")

    assert path.exists(), (
        f"{path.name} is missing. Generate it with "
        f"UPDATE_GOLDEN=1 uv run pytest tests/test_compiler.py"
    )
    assert actual == path.read_text(encoding="utf-8")
