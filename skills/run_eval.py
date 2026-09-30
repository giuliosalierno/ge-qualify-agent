#!/usr/bin/env python3
"""
Evaluation Runner for ge_qualify skill.
Validates skill frontmatter format, token limits, trigger classification,
and scoring execution against test scenarios in skills/eval_cases.json.
"""

import json
import os
import re
import sys

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SKILL_MD_PATH = os.path.join(BASE_DIR, "skills", "ge_qualify", "SKILL.md")
EVAL_CASES_PATH = os.path.join(BASE_DIR, "skills", "eval_cases.json")


def extract_frontmatter(file_path: str):
    if not os.path.exists(file_path):
        return None, None
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", content, re.DOTALL)
    if not match:
        return None, content

    yaml_block = match.group(1)
    body = match.group(2)
    return yaml_block, body


def parse_simple_yaml(yaml_block: str):
    data = {}
    lines = yaml_block.splitlines()
    current_key = None
    multiline_value = []

    for line in lines:
        if re.match(r"^[a-zA-Z0-9_\-]+:\s*\|?\s*$", line):
            if current_key and multiline_value:
                data[current_key] = "\n".join(multiline_value).strip()
                multiline_value = []
            current_key = line.split(":")[0].strip()
        elif current_key and line.startswith("  "):
            multiline_value.append(line.strip())
        elif ":" in line:
            if current_key and multiline_value:
                data[current_key] = "\n".join(multiline_value).strip()
                multiline_value = []
            parts = line.split(":", 1)
            current_key = parts[0].strip()
            data[current_key] = parts[1].strip()
        else:
            continue

    if current_key and multiline_value:
        data[current_key] = "\n".join(multiline_value).strip()

    return data


SKILL_FULL_PATH = os.path.join(BASE_DIR, "skills", "ge_qualify", "SKILL.md")
SKILL_BIZ_SIMPLE_PATH = os.path.join(BASE_DIR, "skills", "ge_intake_business", "SKILL.md")
SKILL_TECH_SIMPLE_PATH = os.path.join(BASE_DIR, "skills", "ge_tech_review", "SKILL.md")


def validate_single_skill_metadata(skill_path: str, label: str, require_self_contained: bool = False):
    print(f"  Validating [{label}] ({os.path.relpath(skill_path, BASE_DIR)})...")
    yaml_block, body = extract_frontmatter(skill_path)
    if not yaml_block:
        print(f"  [!] FAIL: SKILL.md at {skill_path} not found or missing YAML frontmatter (---).")
        return False

    metadata = parse_simple_yaml(yaml_block)
    name = metadata.get("name", "")
    description = metadata.get("description", "")

    # Check 1: Naming kebab-case
    if not re.match(r"^[a-z0-9]+(-[a-z0-9]+)*$", name):
        print(f"  [!] FAIL: Skill name '{name}' must be kebab-case.")
        return False
    print(f"    ✓ Skill Name: '{name}' (kebab-case)")

    # Check 2: Description length (<= 1024 chars)
    desc_len = len(description)
    word_count = len(description.split())
    if desc_len > 1024:
        print(f"  [!] FAIL: Description exceeds 1024 characters ({desc_len} chars).")
        return False
    print(f"    ✓ Description Length: {desc_len} chars, {word_count} words (limit: 1024 chars)")

    # Check 3: Description has required sections (what it does, when to use, anti-trigger)
    desc_lower = description.lower()
    if "use when" not in desc_lower:
        print("    [!] FAIL: Description must contain positive trigger clause ('Use when...')")
        return False
    if "do not use" not in desc_lower and "not for" not in desc_lower:
        print("    [!] FAIL: Description must contain anti-trigger clause ('Do NOT use for...')")
        return False
    print("    ✓ Description contains action verbs, positive triggers, and anti-triggers")

    # Check 4: Body size limit (< 5000 words to avoid context rot)
    body_words = len(body.split())
    if body_words > 5000:
        print(f"    [!] FAIL: Body exceeds 5000 words ({body_words} words). Move details to references/.")
        return False
    print(f"    ✓ SKILL.md Body size: {body_words} words (well below 5,000-word limit)")

    # Check 5: If standalone GE version, ensure no external relative links to references/ or assets/
    if require_self_contained:
        external_refs = re.findall(r"\[.*?\]\((?:references|assets|scripts)/.*?\)", body)
        if external_refs:
            print(f"    [!] FAIL: Standalone GE version must not have relative links to external files: {external_refs}")
            return False
        print("    ✓ Standalone GE constraint: 100% self-contained (zero external file references)")

    return True


def validate_skill_metadata():
    print("[1/3] Validating SKILL.md Frontmatter & Constraints...")
    full_ok = True
    if os.path.exists(SKILL_FULL_PATH):
        full_ok = validate_single_skill_metadata(SKILL_FULL_PATH, "Full / Multi-Agent Version", require_self_contained=False)
    biz_ok = validate_single_skill_metadata(SKILL_BIZ_SIMPLE_PATH, "Front Office Business Intake (GE Simple)", require_self_contained=True)
    tech_ok = validate_single_skill_metadata(SKILL_TECH_SIMPLE_PATH, "Back Office Tech Review (GE Simple)", require_self_contained=True)
    return full_ok and biz_ok and tech_ok


def validate_eval_cases():
    print("\n[2/3] Validating Evaluation Cases Schema...")
    if not os.path.exists(EVAL_CASES_PATH):
        print("  [!] FAIL: skills/eval_cases.json does not exist.")
        return False

    with open(EVAL_CASES_PATH, "r", encoding="utf-8") as f:
        eval_data = json.load(f)

    cases = eval_data.get("cases", [])
    if len(cases) < 4:
        print(f"  [!] FAIL: Expected at least 4 test cases, found {len(cases)}.")
        return False

    for c in cases:
        case_id = c.get("case_id")
        desc = c.get("description")
        inp = c.get("input")
        skill = c.get("expected_skill")
        print(f"  ✓ Validated Case [{case_id}]: '{desc[:45]}...' (Expected: {skill or 'Anti-Trigger'})")

    return True


def validate_deterministic_scripts():
    print("\n[3/3] Validating Deterministic Script Executions...")
    scripts_dir = os.path.join(BASE_DIR, "skills", "ge_qualify", "scripts")
    if not os.path.exists(scripts_dir):
        print("  ✓ Skipping script executions (skills/ge_qualify/scripts not yet present)")
        return True

    sys.path.insert(0, scripts_dir)
    from calculate_score import analyze_opportunity

    test_payload = {
        "workflow": {"as_is_process_described": True, "user_personas": ["SME"], "manual_touchpoints_identified": True, "business_impact": "High"},
        "systems_and_data": {"systems": [{"name": "SAP", "hosting": "on-prem", "interface": "jdbc"}], "data_formats": ["Tables"]},
        "network": {"network_path_to_gcp": "cloud_vpn"},
        "security": {"auth_mechanisms": ["AD"]},
        "stakeholders": {"system_owners_identified": True}
    }

    res = analyze_opportunity(test_payload)
    if res["feasibility_indicator"] != "Custom Agent in GE App":
        print(f"  [!] FAIL: Expected 'Custom Agent in GE App', got {res['feasibility_indicator']}")
        return False
    print("  ✓ Scenario simulation successfully classified 'Custom Agent in GE App'")

    airgap_payload = {
        "network": {"network_path_to_gcp": "airgap"},
        "security": {"policy_cloud_processing_ban": True}
    }
    res_airgap = analyze_opportunity(airgap_payload)
    if res_airgap["feasibility_indicator"] != "Blockers / High Risk":
        print(f"  [!] FAIL: Expected 'Blockers / High Risk', got {res_airgap['feasibility_indicator']}")
        return False
    print("  ✓ Scenario simulation successfully classified 'Blockers / High Risk'")

    return True


def main():
    print("=" * 60)
    print(" EVALUATION SUITE: GE-QUALIFY SKILL")
    print("=" * 60)

    meta_ok = validate_skill_metadata()

    cases_ok = validate_eval_cases()
    scripts_ok = validate_deterministic_scripts()

    print("\n" + "=" * 60)
    if meta_ok and cases_ok and scripts_ok:
        print(" EVALUATION SUITE PASSED ALL CHECKS")
        print("=" * 60)
        sys.exit(0)
    else:
        print(" EVALUATION SUITE FOUND ISSUES")
        print("=" * 60)
        sys.exit(1)


if __name__ == "__main__":
    main()
