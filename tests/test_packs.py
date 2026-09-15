"""Tests for pack loading and its validation rules.

Most of these assert that a *bad* pack is rejected. That is deliberate: the
loader's whole job is to fail at startup rather than mid-interview, and a test
that only loads the good pack proves nothing about that.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from qualify.packs.loader import Pack, PackError, load_all_packs, load_pack_file


def write_pack(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "probe.yaml"
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


BASE = """
pack: probe
version: "1.0"
title: Probe
root_path: /uc
option_sets:
  sources:
    - {{ label: Drive, value: drive }}
stages:
  - id: one
    label: One
    fields:
{fields}
"""


def build(tmp_path: Path, fields: str) -> Pack:
    return load_pack_file(write_pack(tmp_path, BASE.format(fields=fields)))


# ---------------------------------------------------------------------------
# The shipped pack
# ---------------------------------------------------------------------------


def test_every_shipped_pack_loads():
    packs = load_all_packs()
    assert "business" in packs


def test_business_pack_has_four_stages():
    pack = load_all_packs()["business"]
    assert [s.id for s in pack.stages] == ["needs", "sizing", "data", "ownership"]


def test_business_pack_collects_no_coe_fields():
    """AGENT_PLAN.MD L144-146: scoring and execution belong to the CoE."""
    pack = load_all_packs()["business"]
    for stage in pack.stages:
        for field in stage.fields:
            if field.readonly:
                continue
            assert not field.path.startswith("/uc/scoring/")
            assert not field.path.startswith("/uc/execution/")
            assert not field.path.startswith("/uc/derived/")


def test_owner_questions_land_in_proposed():
    pack = load_all_packs()["business"]
    ownership = pack.stage_by_id("ownership")
    owner = next(f for f in ownership.fields if "business_owner" in f.path)
    assert owner.path == "/uc/proposed/business_owner"


def test_every_stage_has_at_least_one_required_field():
    pack = load_all_packs()["business"]
    for stage in pack.stages:
        assert stage.required_paths, f"stage {stage.id} can be skipped entirely"


# ---------------------------------------------------------------------------
# Rejections
# ---------------------------------------------------------------------------


def test_rejects_unknown_path(tmp_path):
    with pytest.raises(PackError, match="no field 'problem_descriptoin'"):
        build(
            tmp_path,
            """      - path: /uc/business/problem_descriptoin
        label: Typo
        component: TextField
        variant: shortText""",
        )


def test_rejects_unverified_component(tmp_path):
    with pytest.raises(PackError, match="not an allowed component"):
        build(
            tmp_path,
            """      - path: /uc/business/problem_description
        label: Material
        component: MaterialInput""",
        )


def test_rejects_choicepicker_on_a_scalar(tmp_path):
    """A ChoicePicker writes a list; the record field is a string."""
    with pytest.raises(PackError, match="ChoicePicker writes a list"):
        build(
            tmp_path,
            """      - path: /uc/business/user_profile
        label: Who
        component: ChoicePicker
        variant: mutuallyExclusive
        options_ref: sources""",
        )


def test_rejects_textfield_on_a_list(tmp_path):
    with pytest.raises(PackError, match="only ChoicePicker can edit"):
        build(
            tmp_path,
            """      - path: /uc/technical/data_sources
        label: Sources
        component: TextField
        variant: shortText""",
        )


def test_rejects_checkbox_on_a_string(tmp_path):
    with pytest.raises(PackError, match="CheckBox writes a boolean"):
        build(
            tmp_path,
            """      - path: /uc/business/user_profile
        label: Who
        component: CheckBox""",
        )


def test_rejects_numeric_field_without_number_variant(tmp_path):
    with pytest.raises(PackError, match="needs variant: number"):
        build(
            tmp_path,
            """      - path: /uc/business/user_count
        label: Users
        component: TextField
        variant: shortText""",
        )


def test_rejects_number_variant_on_a_string(tmp_path):
    with pytest.raises(PackError, match="variant 'number'"):
        build(
            tmp_path,
            """      - path: /uc/business/user_profile
        label: Who
        component: TextField
        variant: number""",
        )


def test_textfield_still_refused_for_an_enum(tmp_path):
    """A free-text box would accept values outside the ladder."""
    with pytest.raises(PackError, match="is an enum"):
        build(
            tmp_path,
            """      - path: /uc/technical/capability_level
        label: Capability
        component: TextField
        variant: shortText""",
        )


# ---------------------------------------------------------------------------
# Scalar single-selects — L12, resolved 2026-09-15
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "component",
    ["MaterialSelect", "MaterialRadioButton", "MaterialButtonToggle", "MaterialChips"],
)
def test_enum_field_accepts_any_verified_single_select(tmp_path, component):
    pack = build(
        tmp_path,
        f"""      - path: /uc/technical/capability_level
        label: Capability level
        component: {component}""",
    )
    assert pack.stages[0].fields[0].component == component


def test_enum_options_come_from_the_enum(tmp_path):
    """Six levels in the ladder means six options, with no YAML listing them."""
    pack = build(
        tmp_path,
        """      - path: /uc/technical/capability_level
        label: Capability level
        component: MaterialRadioButton""",
    )
    options = pack.options_for(pack.stages[0].fields[0])
    assert [o.value for o in options] == ["1", "2", "3", "4", "5", "6"]
    assert options[0].label == "Default assistant"
    assert options[5].label == "Custom high-code agent (ADK / A2A)"


def test_pack_cannot_override_an_enum_vocabulary(tmp_path):
    """Offering a level the record cannot store is worse than offering none."""
    with pytest.raises(PackError, match="options come from the enum itself"):
        build(
            tmp_path,
            """      - path: /uc/technical/capability_level
        label: Capability level
        component: MaterialRadioButton
        options_ref: sources""",
        )


def test_single_select_on_a_string_needs_an_options_ref(tmp_path):
    with pytest.raises(PackError, match="needs an options_ref"):
        build(
            tmp_path,
            """      - path: /uc/business/user_profile
        label: Who
        component: MaterialRadioButton""",
        )


def test_single_select_accepts_a_string_with_a_vocabulary(tmp_path):
    pack = build(
        tmp_path,
        """      - path: /uc/business/user_profile
        label: Who
        component: MaterialRadioButton
        options_ref: sources""",
    )
    assert [o.value for o in pack.options_for(pack.stages[0].fields[0])] == ["drive"]


def test_single_select_refused_on_a_number(tmp_path):
    with pytest.raises(PackError, match="writes a string"):
        build(
            tmp_path,
            """      - path: /uc/business/user_count
        label: Users
        component: MaterialChips
        options_ref: sources""",
        )


def test_single_select_refused_on_a_list(tmp_path):
    with pytest.raises(PackError, match="only ChoicePicker"):
        build(
            tmp_path,
            """      - path: /uc/technical/data_sources
        label: Sources
        component: MaterialChips
        options_ref: sources""",
        )


def test_render_only_component_refused_for_input(tmp_path):
    """MaterialText renders but is display-only, so it cannot hold input."""
    with pytest.raises(PackError, match="never been observed writing"):
        build(
            tmp_path,
            """      - path: /uc/business/user_profile
        label: Who
        component: MaterialText""",
        )


def test_data_classification_is_a_vocabulary_not_free_text():
    """Free text on a closed vocabulary yields forty spellings of one label."""
    pack = load_all_packs()["business"]
    field = next(
        f
        for f in pack.stage_by_id("data").fields
        if f.path.endswith("data_classification")
    )
    assert field.component == "MaterialRadioButton"
    assert [o.value for o in pack.options_for(field)] == [
        "public",
        "internal",
        "confidential",
        "restricted",
        "unknown",
    ]


def test_rejects_writable_coe_field(tmp_path):
    with pytest.raises(PackError, match="CoE-owned"):
        build(
            tmp_path,
            """      - path: /uc/scoring/business_value_score
        label: Value
        component: TextField
        variant: number""",
        )


def test_allows_readonly_coe_field(tmp_path):
    pack = build(
        tmp_path,
        """      - path: /uc/derived/total_annual_team_hours_saved
        label: Hours
        component: Text
        readonly: true""",
    )
    assert pack.stages[0].fields[0].readonly


def test_rejects_writable_ui_path(tmp_path):
    with pytest.raises(PackError, match="must be marked readonly"):
        build(
            tmp_path,
            """      - path: /ui/summary/hours_line
        label: Hours
        component: TextField
        variant: shortText""",
        )


def test_rejects_path_stopping_on_a_container(tmp_path):
    with pytest.raises(PackError, match="stops on a container"):
        build(
            tmp_path,
            """      - path: /uc/business
        label: Business
        component: TextField
        variant: shortText""",
        )


def test_rejects_choicepicker_without_options(tmp_path):
    with pytest.raises(PackError, match="needs an options_ref"):
        build(
            tmp_path,
            """      - path: /uc/technical/data_sources
        label: Sources
        component: ChoicePicker
        variant: multipleSelection""",
        )


def test_rejects_dangling_options_ref(tmp_path):
    with pytest.raises(PackError, match="not in option_sets"):
        build(
            tmp_path,
            """      - path: /uc/technical/data_sources
        label: Sources
        component: ChoicePicker
        variant: multipleSelection
        options_ref: nope""",
        )


def test_rejects_bad_variant(tmp_path):
    with pytest.raises(PackError, match="is not one of"):
        build(
            tmp_path,
            """      - path: /uc/business/user_profile
        label: Who
        component: TextField
        variant: enormousText""",
        )


def test_rejects_relative_path(tmp_path):
    with pytest.raises(PackError, match="absolute JSON Pointer"):
        build(
            tmp_path,
            """      - path: business/user_profile
        label: Who
        component: TextField
        variant: shortText""",
        )


def test_rejects_field_in_two_stages(tmp_path):
    body = """
    pack: probe
    version: "1.0"
    title: Probe
    stages:
      - id: one
        label: One
        fields:
          - path: /uc/business/user_profile
            label: Who
            component: TextField
            variant: shortText
      - id: two
        label: Two
        fields:
          - path: /uc/business/user_profile
            label: Who again
            component: TextField
            variant: shortText
    """
    with pytest.raises(PackError, match="appears in both stage"):
        load_pack_file(write_pack(tmp_path, body))


def test_rejects_unknown_yaml_key(tmp_path):
    with pytest.raises(PackError):
        build(
            tmp_path,
            """      - path: /uc/business/user_profile
        label: Who
        component: TextField
        variant: shortText
        plceholder: oops""",
        )
