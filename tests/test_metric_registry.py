from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

import pytest

from dataagent.bi.catalog import IndustrialPDMAdapter
from dataagent.bi.contracts import BIPlan, MetricDefinition
from dataagent.bi.metric_registry import MetricRegistry, pdm_metric_registry, pdm_result_bindings
from dataagent.bi.scania import ScaniaComponentXAdapter
from dataagent.errors import ConfigurationError, DataAgentError


def test_contract_hash_is_stable_order_independent_and_formula_sensitive():
    metric = pdm_metric_registry().get("failure_count")
    assert metric.semantic_hash == metric.semantic_hash
    reordered = metric.model_copy(update={"dimensions": list(reversed(metric.dimensions))})
    assert reordered.semantic_hash == metric.semantic_hash
    assert (
        metric.model_copy(update={"expression": "different controlled aggregate"}).semantic_hash
        != metric.semantic_hash
    )
    assert metric.model_copy(update={"version": "2.0.0"}).semantic_hash != metric.semantic_hash
    public = metric.model_dump()
    assert "expression" not in public
    assert len(public["semantic_hash"]) == 64


def test_registry_rejects_missing_binding_and_duplicates():
    metric = pdm_metric_registry().get("failure_count")
    with pytest.raises(ConfigurationError, match="Duplicate registered metric"):
        MetricRegistry("pdm", [metric, metric])
    with pytest.raises(ConfigurationError, match="executable binding"):
        MetricRegistry(
            "pdm",
            [
                MetricDefinition(
                    metric_id="unbound",
                    name="unbound",
                    description="unbound",
                    relation="raw",
                    expression="COUNT(*)",
                )
            ],
        )
    with pytest.raises(DataAgentError, match="Unknown/unregistered"):
        pdm_metric_registry().get("SELECT password FROM users")


def test_every_pdm_contract_has_explicit_semantics():
    registry = pdm_metric_registry()
    assert len(registry.definitions()) == 7
    for metric in registry.definitions():
        assert metric.version == "1.0.0"
        assert metric.grain and metric.numerator and metric.null_policy and metric.time_policy
        assert metric.exclusions and metric.owner and metric.implementation_id
        assert metric.result_column == metric.metric_id
    rate = registry.get("failure_rate_pct")
    assert "base_machines" in rate.denominator
    assert "DISTINCT" in rate.numerator
    assert "failure_type" in str(rate.exclusions)
    assert (
        registry.provenance()["metric_hashes"]["failure_count"] == registry.get("failure_count").semantic_hash
    )


def test_pdm_binding_recomputes_rate_and_preserves_missing_values():
    rate = pdm_result_bindings(["failure_rate_pct"], None)[0]
    assert rate.read({"totalDevices": 3, "failedDevices": 1, "failureRatePct": 999}) == 33.33
    assert rate.read({"totalDevices": 0, "failedDevices": 0}) is None
    age = pdm_result_bindings(["average_device_age"], None)[0]
    assert age.read({"averageDeviceAge": None}) is None
    with pytest.raises(ConfigurationError, match="incompatible result schema"):
        rate.read({"failureRatePct": 0})
    with pytest.raises(ConfigurationError, match="incompatible result schema"):
        age.read({"unrelated_column": 0})


class PdmProvider:
    async def overview(self, _filters):
        return {
            "totalDevices": 3,
            "failedDevices": 2,
            "failureCount": 4,
            "failureRatePct": 66.67,
            "errorCount": 5,
            "maintenanceCount": 6,
            "averageDeviceAge": None,
            "scope": {"startDate": "2024-01-01", "endDate": "2024-01-03"},
        }

    async def failure_by_model(self, _filters):
        return {
            "items": [
                {
                    "model": "model1",
                    "deviceCount": 3,
                    "failedDeviceCount": 2,
                    "failureCount": 4,
                    "failureRatePct": 999,
                }
            ],
            "scope": {"model": "model1"},
        }

    async def failure_trend(self, _filters, _grain):
        return {"items": [{"period": "2024-01-01", "failureCount": 4, "failedMachineCount": 2}], "scope": {}}

    async def failure_types(self, _filters):
        return {"items": [{"failureType": "comp1", "failureCount": 4}], "scope": {}}

    async def error_ranking(self, _filters, limit):
        return {"items": [{"errorId": f"error{i}", "errorCount": 4} for i in range(limit)], "scope": {}}

    async def machine_ranking(self, _filters, limit):
        return {
            "items": [
                {"machineId": i + 1, "failureCount": 4, "errorCount": 5, "maintenanceCount": 6}
                for i in range(limit)
            ],
            "scope": {},
        }

    async def analysis_context(self, filters):
        return {"question": "snapshot", "scope": {}, "evidence": {"overview": await self.overview(filters)}}


@pytest.mark.parametrize(
    ("dimension", "metric_ids", "expected"),
    [
        (None, ["device_count", "failure_count"], {"device_count": 3, "failure_count": 4}),
        ("model", ["failure_rate_pct"], {"model": "model1", "failure_rate_pct": 66.67}),
        ("period", ["failed_device_count"], {"period": "2024-01-01", "failed_device_count": 2}),
        ("failureType", ["failure_count"], {"failureType": "comp1", "failure_count": 4}),
        ("errorId", ["error_count"], {"errorId": "error0", "error_count": 4}),
        (
            "machineId",
            ["failure_count", "maintenance_count"],
            {"machineId": 1, "failure_count": 4, "maintenance_count": 6},
        ),
    ],
)
async def test_pdm_controlled_execution_bindings(dimension, metric_ids, expected):
    adapter = IndustrialPDMAdapter(PdmProvider())
    result = await adapter.query(
        BIPlan(dataset_id=adapter.dataset_id, metric_ids=metric_ids, dimension=dimension, limit=1)
    )
    assert result["data"] == [expected]
    provenance = result["source"]["metric_registry"]
    assert provenance["metric_ids"] == metric_ids
    assert all(
        m["semantic_hash"] == provenance["metric_hashes"][m["metric_id"]]
        for m in result["metric_definitions"]
    )
    assert provenance["execution"]["implementation_id"].startswith("DashboardService.")
    if dimension in {"errorId", "machineId"}:
        assert result["truncated"] is True


async def test_pdm_dashboard_and_chat_share_registry():
    adapter = IndustrialPDMAdapter(PdmProvider())
    dashboard = await adapter.dashboard({})
    query = await adapter.query(BIPlan(dataset_id=adapter.dataset_id, metric_ids=["failure_count"]))
    assert (
        dashboard["metric_registry"]["semantic_hash"] == query["source"]["metric_registry"]["semantic_hash"]
    )
    assert len(dashboard["metric_definitions"]) == 7
    assert any("MTTR" in limitation for limitation in dashboard["limitations"])
    assert any("OEE" in limitation for limitation in dashboard["limitations"])


def test_pdm_rejects_duplicate_metrics_and_wrong_dataset():
    adapter = IndustrialPDMAdapter(PdmProvider())
    with pytest.raises(DataAgentError, match="Duplicate metrics"):
        adapter.validate(BIPlan(dataset_id=adapter.dataset_id, metric_ids=["failure_count", "failure_count"]))
    with pytest.raises(DataAgentError, match="does not match"):
        adapter.validate(BIPlan(dataset_id="scania_component_x", metric_ids=["failure_count"]))


@pytest.fixture
def registry_scania(settings, tmp_path: Path):
    raw = tmp_path / "scania" / "raw"
    raw.mkdir(parents=True)

    def write(name: str, columns: list[str], rows: list[list[Any]]) -> None:
        with (raw / f"{name}.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(columns)
            writer.writerows(rows)

    for split in ("train", "validation", "test"):
        write(
            f"{split}_specifications",
            ["vehicle_id", *(f"Spec_{i}" for i in range(8))],
            [[1, *(["A"] * 8)], [2, *(["B"] * 8)]],
        )
        write(
            f"{split}_operational_readouts",
            ["vehicle_id", "time_step", "171_0", "666_0"],
            [[1, 0, 10, 20], [1, 20, "", 40], [2, 10, 30, 60]],
        )
        if split == "train":
            write(
                "train_tte",
                ["vehicle_id", "in_study_repair", "length_of_study_time_step"],
                [[1, 1, 25], [2, 0, 50]],
            )
        else:
            write(f"{split}_labels", ["vehicle_id", "class_label"], [[1, 4], [2, 0]])
    settings.scania_data_dir = raw.parent
    return ScaniaComponentXAdapter(settings)


def test_every_scania_metric_has_scope_grain_and_binding(registry_scania):
    metrics = registry_scania.metrics()
    assert len(metrics) == 10  # Eight entity/readout definitions plus two anonymous mean features.
    for metric in metrics:
        assert metric.version == "1.0.0"
        assert metric.grain and metric.numerator and metric.exclusions and metric.owner
        assert "not hours/days" in metric.time_policy
        assert metric.result_column == metric.metric_id
    assert registry_scania.metric_registry().get("mean_171_0").unit == "unknown"
    assert "COUNT(non-null" in registry_scania.metric_registry().get("mean_171_0").denominator


async def test_scania_dashboard_and_query_provenance_and_null_policy(registry_scania):
    query = await registry_scania.query(
        BIPlan(dataset_id=registry_scania.dataset_id, metric_ids=["mean_171_0"])
    )
    assert query["data"] == [{"mean_171_0": 20.0}]
    dashboard = await registry_scania.dashboard({}, "171_0")
    assert (
        dashboard["evidence"]["metric_registry"]["semantic_hash"]
        == query["source"]["metric_registry"]["semantic_hash"]
    )
    assert any(m["metric_id"] == "mean_171_0" for m in dashboard["metric_definitions"])
    correlation = await registry_scania.query(
        BIPlan(
            dataset_id=registry_scania.dataset_id,
            intent="correlation",
            metric_ids=["mean_171_0", "mean_666_0"],
        )
    )
    execution = correlation["source"]["metric_registry"]["execution"]
    assert execution["aggregation_applied"] is False
    assert execution["bindings"][0]["result_column"] == "171_0"
    assert execution["bindings"][0]["value_kind"] == "raw_readout"
    empty = await registry_scania.query(
        BIPlan(
            dataset_id=registry_scania.dataset_id,
            metric_ids=["repair_vehicle_share_pct"],
            filters={"specifications": {"Spec_0": "absent"}},
        )
    )
    assert empty["data"] == [{"repair_vehicle_share_pct": None}]


async def test_missing_source_outcome_flags_fail_closed(registry_scania):
    path = registry_scania.root / "raw" / "train_tte.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["vehicle_id", "in_study_repair", "length_of_study_time_step"])
        writer.writerows([[1, "", 25], [2, 0, 50]])
    with pytest.raises(ConfigurationError, match="missing or invalid source outcome"):
        await registry_scania.query(
            BIPlan(dataset_id=registry_scania.dataset_id, metric_ids=["repair_vehicle_share_pct"])
        )
