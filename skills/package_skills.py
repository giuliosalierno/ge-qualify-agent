#!/usr/bin/env python3
"""
Packages qualification skills into distributable zip archives:
- ge_intake_business.zip: Front Office Business Intake & Value Skill (Gate 1).
- ge_tech_review.zip: Back Office Architecture & Security Review Skill (Gate 2).
- ge_qualify_skill.zip: Full skill bundle (SKILL.md, scripts, references, assets).
"""

import os
import zipfile

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def package_single_file_skill(zip_filename: str, skill_folder: str, label: str):
    zip_path = os.path.join(BASE_DIR, zip_filename)
    src_file = os.path.join(BASE_DIR, "skills", skill_folder, "SKILL.md")
    print(f"Building {zip_filename} [{label}]...")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(src_file, arcname="SKILL.md")
    print(f"  ✓ {zip_filename} created with standalone SKILL.md")


def package_full_skill():
    zip_path = os.path.join(BASE_DIR, "ge_qualify_skill.zip")
    src_dir = os.path.join(BASE_DIR, "skills", "ge_qualify")
    if not os.path.exists(src_dir):
        print(f"Skipping {os.path.basename(zip_path)} (source directory {src_dir} not found)")
        return
    print(f"Building {os.path.basename(zip_path)} [Full / Multi-Agent Version]...")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(src_dir):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for f in files:
                if f.endswith(".pyc"):
                    continue
                abs_path = os.path.join(root, f)
                rel_path = os.path.relpath(abs_path, src_dir)
                zf.write(abs_path, arcname=rel_path)
    print(f"  ✓ {os.path.basename(zip_path)} created with full skill bundle")


def main():
    package_single_file_skill("ge_intake_business.zip", "ge_intake_business", "Front Office Business Intake")
    package_single_file_skill("ge_tech_review.zip", "ge_tech_review", "Back Office Technical Review")
    package_single_file_skill("ge_capability_grounding.zip", "ge_capability_grounding", "GCP Capability Grounding & Anti-Overcommitment")
    package_full_skill()
    print("All skill packages built successfully.")


if __name__ == "__main__":
    main()
