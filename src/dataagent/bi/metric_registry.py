from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from dataagent.bi.contracts import MetricDefinition
from dataagent.errors import ConfigurationError, DataAgentError

REGISTRY_VERSION = "1"
PDM_TIME_POLICY = (
    "Source calendar time, no timezone conversion; start_date 00:00 inclusive to "
    "day after end_date 00:00 exclusive. Missing dates use the dataset event range. "
    "Equipment population/age do not change with the selected event window."
)
SCANIA_TIME_POLICY = (
    "Same source split only. time_step is anonymous relative time, not hours/days. "
    "min_time/max_time are inclusive readout bounds; entity metrics retain source "
    "labels and select vehicles with a readout in that interval when time bounds exist."
)


@dataclass(frozen=True)
class ResultBinding:
    """A code-owned provider binding; never accepts model SQL or a formula."""

    metric_id: str
    implementation_id: str
    source_column: str
    numerator_column: str | None = None
    denominator_column: str | None = None

    def read(self, row: dict[str, Any]) -> Any:
        required = (
            [self.numerator_column, self.denominator_column]
            if self.denominator_column is not None
            else [self.source_column]
        )
        if any(column not in row for column in required):
            raise ConfigurationError(
                f"Registered metric {self.metric_id} provider returned an incompatible result schema"
            )
        if self.denominator_column is None:
            return row[self.source_column]
        numerator = row[self.numerator_column]  # type: ignore[index]
        denominator = row[self.denominator_column]
        if numerator is None or denominator is None or denominator == 0:
            return None
        return float(
            (Decimal(str(numerator)) * 100 / Decimal(str(denominator))).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
        )


class MetricRegistry:
    def __init__(self, dataset_id: str, definitions: list[MetricDefinition]):
        self.dataset_id = dataset_id
        self._definitions: dict[str, MetricDefinition] = {}
        for definition in definitions:
            if definition.metric_id in self._definitions:
                raise ConfigurationError(f"Duplicate registered metric: {definition.metric_id}")
            if not definition.result_column or not definition.implementation_id:
                raise ConfigurationError(f"Metric {definition.metric_id} lacks an executable binding")
            if len(set(definition.dimensions)) != len(definition.dimensions):
                raise ConfigurationError(f"Duplicate dimensions: {definition.metric_id}")
            self._definitions[definition.metric_id] = definition

    def get(self, metric_id: str) -> MetricDefinition:
        try:
            return self._definitions[metric_id]
        except KeyError as exc:
            raise DataAgentError(f"Unknown/unregistered metric: {metric_id}") from exc

    def definitions(self, metric_ids: list[str] | None = None) -> list[MetricDefinition]:
        return [self.get(key) for key in (metric_ids if metric_ids is not None else self._definitions)]

    def public_definitions(self, metric_ids: list[str] | None = None) -> list[dict[str, Any]]:
        return [metric.model_dump() for metric in self.definitions(metric_ids)]

    @property
    def semantic_hash(self) -> str:
        fingerprint = {
            "dataset_id": self.dataset_id,
            "version": REGISTRY_VERSION,
            "metrics": {key: self._definitions[key].semantic_hash for key in sorted(self._definitions)},
        }
        return hashlib.sha256(
            json.dumps(fingerprint, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    def provenance(
        self, metric_ids: list[str] | None = None, *, execution: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        definitions = self.definitions(metric_ids)
        return {
            "version": REGISTRY_VERSION,
            "dataset_id": self.dataset_id,
            "semantic_hash": self.semantic_hash,
            "metric_ids": [metric.metric_id for metric in definitions],
            "metric_hashes": {metric.metric_id: metric.semantic_hash for metric in definitions},
            "execution": execution or {},
        }


_PDM_METRICS = (
    ("device_count", "设备数", "totalDevices", "count", ["model"], "equipment", "COUNT(base_machines)", None),
    (
        "failed_device_count",
        "故障设备数",
        "failedDevices",
        "count",
        ["model", "period"],
        "equipment",
        "COUNT(DISTINCT scoped failures.machineID)",
        None,
    ),
    (
        "failure_count",
        "故障次数",
        "failureCount",
        "count",
        ["model", "period", "failureType", "machineId"],
        "failure event",
        "COUNT(scoped failure events)",
        None,
    ),
    (
        "failure_rate_pct",
        "故障设备占比",
        "failureRatePct",
        "%",
        ["model"],
        "equipment",
        "COUNT(DISTINCT scoped failures.machineID)",
        "COUNT(base_machines in the same equipment/model scope)",
    ),
    (
        "error_count",
        "Error 次数",
        "errorCount",
        "count",
        ["errorId", "machineId"],
        "error event",
        "COUNT(scoped error events on event_machines)",
        None,
    ),
    (
        "maintenance_count",
        "维修次数",
        "maintenanceCount",
        "count",
        ["machineId"],
        "maintenance record",
        "COUNT(scoped maintenance rows on event_machines)",
        None,
    ),
    (
        "average_device_age",
        "平均设备年龄",
        "averageDeviceAge",
        "year",
        [],
        "equipment",
        "SUM(non-null base_machines.age)",
        "COUNT(non-null base_machines.age)",
    ),
)


def pdm_metric_registry() -> MetricRegistry:
    definitions = []
    for key, name, column, unit, dimensions, grain, numerator, denominator in _PDM_METRICS:
        event_selected = key in {"error_count", "maintenance_count"}
        exclusions = [
            "Equipment outside selected machine_id/model is excluded.",
            "Events outside [start_date, day after end_date) are excluded.",
        ]
        if event_selected:
            exclusions.append(
                "With failure_type filter, Error/maintenance include only equipment that had that failure "
                "in the selected event window; these rows do not carry a failure component label."
            )
        elif key in {"failure_count", "failed_device_count", "failure_rate_pct"}:
            exclusions.append("failure_type filters failure events, not the base equipment denominator.")
        elif key in {"device_count", "average_device_age"}:
            exclusions = [
                "Only machine_id/model filter the base equipment population; failure_type/event dates "
                "do not remove equipment from this denominator."
            ]
        descriptions = {
            "failure_rate_pct": (
                "同一设备范围内，筛选期至少发生一次指定故障的设备数/设备数×100；不是事件发生率或预测概率"
            ),
            "average_device_age": "设备主表非缺失 age 的均值；不是从事件时间推算的设备年龄",
        }
        definitions.append(
            MetricDefinition(
                metric_id=key,
                name=name,
                description=descriptions.get(key, f"受控工业看板聚合指标 {column}"),
                unit=unit,
                relation="dashboard",
                expression=column,
                dimensions=dimensions,
                version="1.0.0",
                grain=grain,
                numerator=numerator,
                denominator=denominator,
                exclusions=exclusions,
                time_policy=PDM_TIME_POLICY,
                owner="dataagent.industrial_pdm",
                result_column=key,
                implementation_id="DashboardService.registered_pdm_aggregate.v1",
                null_policy=(
                    "Zero denominator yields NULL (unavailable), not zero risk; "
                    "otherwise percent rounded to 2 decimals"
                    if key == "failure_rate_pct"
                    else "Missing age excluded; empty non-null age population yields NULL; "
                    "rounded to 2 decimals"
                    if key == "average_device_age"
                    else "COUNT aggregates return 0 for an empty scoped population; "
                    "missing required provider columns fail closed"
                ),
            )
        )
    return MetricRegistry("industrial_pdm", definitions)


_PDM_SOURCE_COLUMNS = {
    None: {key: column for key, _name, column, *_rest in _PDM_METRICS},
    "model": {
        "device_count": "deviceCount",
        "failed_device_count": "failedDeviceCount",
        "failure_count": "failureCount",
        "failure_rate_pct": "failureRatePct",
    },
    "period": {"failure_count": "failureCount", "failed_device_count": "failedMachineCount"},
    "failureType": {"failure_count": "failureCount"},
    "errorId": {"error_count": "errorCount"},
    "machineId": {
        "failure_count": "failureCount",
        "error_count": "errorCount",
        "maintenance_count": "maintenanceCount",
    },
}


def pdm_result_bindings(metric_ids: list[str], dimension: str | None) -> list[ResultBinding]:
    registry = pdm_metric_registry()
    columns = _PDM_SOURCE_COLUMNS.get(dimension)
    if columns is None:
        raise DataAgentError("Unregistered PdM dimension")
    result = []
    for key in metric_ids:
        registry.get(key)
        if key not in columns:
            raise DataAgentError("Unregistered PdM metric/dimension combination")
        numerator, denominator = (None, None)
        if key == "failure_rate_pct":
            numerator = columns["failed_device_count"]
            denominator = columns["device_count"]
        result.append(
            ResultBinding(
                key, "DashboardService.registered_pdm_aggregate.v1", columns[key], numerator, denominator
            )
        )
    return result


def scania_metric_contract(metric: MetricDefinition) -> MetricDefinition:
    """Bind each registered aggregate to its source grain and split semantics."""
    entity = metric.relation == "entities"
    denominator = None
    numerator = metric.expression
    null_policy = "COUNT returns zero for empty population; missing aggregate values remain NULL"
    if metric.metric_id == "repair_vehicle_share_pct":
        numerator, denominator = "SUM(train.in_study_repair)", "COUNT(same scoped training entities)"
        null_policy = (
            "SUM ignores missing repair flags; zero vehicle denominator yields NULL, not zero probability"
        )
    elif metric.metric_id == "study_duration_mean":
        numerator, denominator = (
            "SUM(non-null length_of_study_time_step)",
            "COUNT(non-null length_of_study_time_step)",
        )
        null_policy = "Missing duration is excluded; no observed durations yields NULL"
    elif metric.metric_id.startswith("mean_"):
        field = metric.metric_id.removeprefix("mean_")
        numerator, denominator = f'SUM(non-null readouts."{field}")', f'COUNT(non-null readouts."{field}")'
        null_policy = (
            "Missing operational values are excluded, never filled with zero; all missing yields NULL"
        )
    elif metric.metric_id == "known_label_level":
        null_policy = "Missing source class_label yields NULL; MAX is only permitted for one vehicle"
    elif metric.metric_id in {"repair_vehicle_count", "censored_vehicle_count"}:
        null_policy = (
            "Missing repair flags are excluded, not treated as repaired or censored; empty sum returns zero"
        )
    exclusions = [
        "Different train/validation/test splits never join or share denominators.",
        "Vehicles outside vehicle/specification/source-label filters are excluded.",
        "One-to-one metadata keys must be non-null, unique, and cover matching vehicles; "
        "mismatches fail closed.",
    ]
    if not entity:
        exclusions.append(
            "Readouts outside inclusive min_time/max_time are excluded; "
            "repeated observations are row weighted."
        )
    else:
        exclusions.append(
            "Time-filtered vehicle counts use existence of eligible readouts; "
            "source event/label values are not relabeled for that subwindow."
        )
    enriched = metric.model_dump(exclude={"semantic_hash"})
    enriched.update(
        expression=metric.expression,
        version="1.0.0",
        grain="one vehicle per selected split" if entity else "one operational readout per vehicle/time_step",
        numerator=numerator,
        denominator=denominator,
        exclusions=exclusions,
        null_policy=null_policy,
        time_policy=SCANIA_TIME_POLICY,
        owner="dataagent.scania_component_x",
        result_column=metric.metric_id,
        implementation_id="ScaniaComponentXAdapter.registered_duckdb_aggregate.v1",
    )
    return MetricDefinition.model_validate(enriched)
