#!/usr/bin/env python3
"""
Unit tests for deterministic scoring and ROI scripts in skills/ge_qualify/scripts/
"""

import os
import sys
import unittest

# Add skills/ge_qualify/scripts to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "skills", "ge_qualify", "scripts")))

from calculate_score import analyze_opportunity, evaluate_workflow, evaluate_systems, evaluate_network, evaluate_security, evaluate_stakeholders
from calculate_roi import calculate_time_reclaimed


class TestCalculateScore(unittest.TestCase):

    def test_pure_ge_app_scenario(self):
        data = {
            "workflow": {
                "as_is_process_described": True,
                "user_personas": ["Support Engineers"],
                "manual_touchpoints_identified": True,
                "business_impact": "Reduce query time by 50%",
            },
            "systems_and_data": {
                "systems": [
                    {"name": "Google Drive", "hosting": "gcp", "interface": "managed_connector", "schema_documented": True},
                    {"name": "BigQuery", "hosting": "gcp", "interface": "managed_connector", "schema_documented": True},
                    {"name": "Salesforce", "hosting": "saas", "interface": "managed_connector", "schema_documented": True},
                ],
                "data_formats": ["Docs", "Structured Tables"],
            },
            "network": {
                "network_path_to_gcp": "public_with_whitelisting",
                "firewall_status": "configured",
                "latency_or_bandwidth_constraints": False,
            },
            "security": {
                "auth_mechanisms": ["Google Workspace SSO"],
                "data_classification": "Confidential",
                "residency_requirements": "US",
                "policy_cloud_processing_ban": False,
            },
            "stakeholders": {
                "system_owners_identified": True,
                "domain_sme_available": True,
                "gcp_landing_zone_ready": True,
            },
        }

        result = analyze_opportunity(data)
        self.assertEqual(result["feasibility_indicator"], "Pure GE App")
        self.assertEqual(len(result["critical_blockers"]), 0)
        self.assertGreaterEqual(result["completeness_score"], 85.0)
        self.assertTrue(result["is_ready_for_fde_kickoff"])

    def test_custom_agent_scenario_hybrid_legacy(self):
        data = {
            "workflow": {
                "as_is_process_described": True,
                "user_personas": ["Inventory Managers"],
                "manual_touchpoints_identified": True,
                "business_impact": "Automate order lookup",
            },
            "systems_and_data": {
                "systems": [
                    {"name": "SAP ECC", "hosting": "on-prem", "interface": "jdbc", "schema_documented": True},
                    {"name": "IBM DB2", "hosting": "on-prem", "interface": "direct_sql", "schema_documented": True},
                ],
                "data_formats": ["Relational Tables"],
            },
            "network": {
                "network_path_to_gcp": "cloud_vpn",
                "firewall_status": "configured",
                "latency_or_bandwidth_constraints": False,
            },
            "security": {
                "auth_mechanisms": ["Active Directory / Kerberos"],
                "data_classification": "Internal",
                "residency_requirements": "None",
                "policy_cloud_processing_ban": False,
            },
            "stakeholders": {
                "system_owners_identified": True,
                "domain_sme_available": True,
                "gcp_landing_zone_ready": True,
            },
        }

        result = analyze_opportunity(data)
        self.assertEqual(result["feasibility_indicator"], "Custom Agent in GE App")
        self.assertEqual(len(result["critical_blockers"]), 0)
        self.assertTrue(result["is_ready_for_fde_kickoff"])

    def test_critical_blocker_airgap(self):
        data = {
            "network": {
                "network_path_to_gcp": "airgap",
            },
            "security": {
                "policy_cloud_processing_ban": True,
            },
        }
        result = analyze_opportunity(data)
        self.assertEqual(result["feasibility_indicator"], "Blockers / High Risk")
        self.assertIn("Airgapped environment with no network path to Google Cloud.", result["critical_blockers"])
        self.assertIn("Company policy strictly prohibits cloud data processing.", result["critical_blockers"])
        self.assertFalse(result["is_ready_for_fde_kickoff"])

    def test_undocumented_schema_without_owner(self):
        data = {
            "systems_and_data": {
                "systems": [
                    {"name": "Legacy DB", "schema_documented": False}
                ]
            },
            "stakeholders": {
                "system_owners_identified": False
            }
        }
        result = analyze_opportunity(data)
        self.assertIn("Data schemas are undocumented and no system owner/DBA is available to assist.", result["critical_blockers"])
        self.assertEqual(result["feasibility_indicator"], "Blockers / High Risk")


class TestCalculateRoi(unittest.TestCase):

    def test_time_reclaimed_math(self):
        # 10 users, 5 tasks/week, 30 min/task = 25 hours total/week
        # 70% automation rate = 17.5 hours saved/week
        # 48 weeks = 840 hours saved/year
        res = calculate_time_reclaimed(
            team_size=10,
            tasks_per_week_per_user=5.0,
            avg_minutes_per_task=30.0,
            automation_rate=0.70,
            hourly_rate=75.0,
            working_weeks_per_year=48,
        )
        self.assertEqual(res["metrics"]["current_weekly_hours_spent"], 25.0)
        self.assertEqual(res["metrics"]["estimated_weekly_hours_saved"], 17.5)
        self.assertEqual(res["metrics"]["estimated_annual_hours_saved"], 840.0)
        self.assertEqual(res["metrics"]["estimated_annual_value_saved_usd"], 63000.0)
        self.assertAlmostEqual(res["fte_equivalent_saved"], 0.44, places=2)


if __name__ == "__main__":
    unittest.main()
