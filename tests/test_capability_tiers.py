"""The capability ladder in code must match the one the model is prompted with."""

from __future__ import annotations

import re
from pathlib import Path

from qualify.schema.capability import CapabilityLevel, DeliveryTier

ROOT = Path(__file__).resolve().parent.parent
GROUNDING = ROOT / "skills" / "ge_capability_grounding" / "SKILL.md"
INSTRUCTIONS = ROOT / "agent" / "instructions.md"


def test_levels_roll_up_to_the_documented_tiers() -> None:
    assert [lvl.delivery_tier for lvl in CapabilityLevel] == [
        DeliveryTier.NO_CODE,
        DeliveryTier.NO_CODE,
        DeliveryTier.LOW_CODE,
        DeliveryTier.LOW_CODE,
        DeliveryTier.PRO_CODE,
        DeliveryTier.PRO_CODE,
    ]


def test_grounding_skill_matrix_matches_code() -> None:
    rows = re.findall(r"^\| \*\*Level (\d)\*\* \|[^|]*\| \*\*Tier (\d):", GROUNDING.read_text(encoding="utf-8"), re.M)
    assert len(rows) == len(CapabilityLevel)
    for level, tier in rows:
        assert CapabilityLevel(int(level)).delivery_tier == DeliveryTier(int(tier)), f"Level {level}"


def test_agent_instructions_table_matches_code() -> None:
    names = {"No code": DeliveryTier.NO_CODE, "Low code": DeliveryTier.LOW_CODE, "Pro code": DeliveryTier.PRO_CODE}
    rows = re.findall(r"^\| (\d) \| [^|]+ \| ([A-Za-z ]+?) \|$", INSTRUCTIONS.read_text(encoding="utf-8"), re.M)
    assert len(rows) == len(CapabilityLevel)
    for level, tier in rows:
        assert CapabilityLevel(int(level)).delivery_tier == names[tier], f"Level {level}"


def _skill_ladder_rows(name: str) -> list[tuple[str, str]]:
    text = (ROOT / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
    return re.findall(r"^\| (\d) \| [^|]+ \| Tier (\d): [A-Za-z-]+ \|", text, re.M)


def test_standalone_skills_use_the_same_ladder() -> None:
    for name in ("ge_intake_business", "ge_tech_review"):
        rows = _skill_ladder_rows(name)
        assert len(rows) == len(CapabilityLevel), name
        for level, tier in rows:
            assert CapabilityLevel(int(level)).delivery_tier == DeliveryTier(int(tier)), f"{name} Level {level}"
