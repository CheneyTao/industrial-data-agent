from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from industrial_pdm.analyzer import IndustrialAnalyzer
from industrial_pdm.db import Database
from industrial_pdm.provenance import TraceRun
from scripts.generate_synthetic_data import GENERATOR_VERSION, stable_unit


class DatasetTests(unittest.TestCase):
    def test_failure_alignment_is_exact(self) -> None:
        failures = pd.read_csv(ROOT / "original" / "PdM_failures.csv")
        reports = pd.read_csv(ROOT / "synthetic" / "synthetic_fault_reports.csv", encoding="utf-8-sig")
        source_keys = set(zip(failures.machineID, failures.datetime, failures.failure))
        report_keys = set(zip(reports.machineID, reports.datetime, reports.component))
        self.assertEqual(source_keys, report_keys)
        self.assertEqual(len(report_keys), 761)

    def test_synthetic_tables_are_explicit_and_large_enough(self) -> None:
        manifest = json.loads((ROOT / "synthetic" / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["generator_version"], GENERATOR_VERSION)
        self.assertGreaterEqual(manifest["tables"]["synthetic_operator_logs"], 6000)
        self.assertGreaterEqual(manifest["tables"]["synthetic_work_orders"], 3000)
        self.assertEqual(manifest["tables"]["synthetic_daily_operations"], 36500)

    def test_deterministic_generator_seed(self) -> None:
        self.assertEqual(stable_unit("machine", 90, "cost"), stable_unit("machine", 90, "cost"))
        self.assertNotEqual(stable_unit("machine", 90, "cost"), stable_unit("machine", 91, "cost"))

    def test_rca_weights_and_component_coverage(self) -> None:
        rules = json.loads((ROOT / "config" / "rca_rules.json").read_text(encoding="utf-8"))
        self.assertAlmostEqual(sum(rules["weights"].values()), 1.0)
        for component in ("comp1", "comp2", "comp3", "comp4"):
            names = [r["name"] for r in rules["candidates"] if component in r["components"]]
            self.assertEqual(len(names), 3)

    def test_database_rejects_mutation_in_readonly_mode(self) -> None:
        db = Database("sqlite:///:memory:")
        with self.assertRaises(ValueError):
            db.read_only_query("DROP TABLE pdm_machines")

    def test_windows_sqlite_url_resolves_to_the_requested_file(self) -> None:
        expected = (ROOT / "generated" / "runtime" / "industrial_pdm.sqlite").resolve()
        database = Database(f"sqlite:///{expected}")
        self.assertEqual(database.sqlite_path, expected)

    def test_model_catalog_is_complete_and_preserves_source_codes(self) -> None:
        catalog = json.loads((ROOT / "config" / "model_catalog.json").read_text(encoding="utf-8"))
        rows = catalog["models"]
        self.assertEqual(
            {item["source_model_code"] for item in rows},
            {"model1", "model2", "model3", "model4"},
        )
        self.assertEqual(
            [item["display_name_zh"] for item in rows],
            ["A 型机组", "B 型机组", "C 型机组", "D 型机组"],
        )
        self.assertTrue(all(item["is_demo_alias"] == 1 for item in rows))

    def test_trace_rejects_unresolved_evidence(self) -> None:
        trace = TraceRun("test", "industrial_pdm", "test-version")
        trace.add_claim("unsupported", ["missing-artifact"])
        with self.assertRaises(ValueError):
            trace.as_dict()


@unittest.skipUnless((ROOT / "generated" / "runtime" / "industrial_pdm.sqlite").exists(), "SQLite mirror not built")
class AnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        url = f"sqlite:///{(ROOT / 'generated' / 'runtime' / 'industrial_pdm.sqlite').resolve()}"
        cls.analyzer = IndustrialAnalyzer(Database(url), ROOT / "config" / "rca_rules.json")

    def test_snapshot_counts(self) -> None:
        snapshot = self.analyzer.fleet_snapshot()
        self.assertEqual(snapshot["counts"]["pdm_telemetry"], 876100)
        self.assertEqual(snapshot["counts"]["pdm_failures"], 761)
        self.assertEqual(snapshot["counts"]["synthetic_daily_operations"], 36500)
        self.assertGreater(snapshot["synthetic_work_order_summary"]["total_cost"], 0)
        self.assertEqual(
            {row["model_name"] for row in snapshot["synthetic_oee_by_model"]},
            {"A 型机组", "B 型机组", "C 型机组", "D 型机组"},
        )

    def test_rca_has_ranked_evidence_candidates(self) -> None:
        result = self.analyzer.root_cause_analysis(16, "2015-01-02 03:00:00", "comp1")
        self.assertEqual(result["fault_id"], "F000001")
        self.assertEqual(len(result["candidates"]), 3)
        self.assertFalse(result["synthetic_reference_used_in_score"])
        self.assertGreaterEqual(
            result["candidates"][0]["confidence_score"],
            result["candidates"][1]["confidence_score"],
        )


if __name__ == "__main__":
    unittest.main()
