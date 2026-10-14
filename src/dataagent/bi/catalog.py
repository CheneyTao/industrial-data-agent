from __future__ import annotations

import re
from datetime import timedelta
from typing import Any, Protocol

from dataagent.bi.contracts import BIPlan, DatasetDefinition, FieldDefinition, MetricDefinition, PdmFilters
from dataagent.bi.metric_registry import pdm_metric_registry, pdm_result_bindings
from dataagent.bi.scania import ScaniaComponentXAdapter
from dataagent.config import Settings
from dataagent.dashboard import DashboardFilterInput, DashboardService
from dataagent.errors import DataAgentError, NotFoundError


class DatasetAdapter(Protocol):
    dataset_id: str
    filter_model: Any

    def definition(self) -> DatasetDefinition: ...
    def metrics(self) -> list[MetricDefinition]: ...
    def validate(self, plan: BIPlan) -> Any: ...
    async def dashboard_filters(self, question: str, filters: dict[str, Any]) -> dict[str, Any]: ...
    async def query(self, plan: BIPlan) -> dict[str, Any]: ...
    async def dashboard(
        self, raw_filters: dict[str, Any], feature: str | None = None, dimension: str | None = None
    ) -> dict[str, Any]: ...


class IndustrialPDMAdapter:
    dataset_id = "industrial_pdm"
    filter_model = PdmFilters
    # This compatibility mapping is derived from the authoritative registry.
    # SQL and scope implementation remain in the existing DashboardService.
    mappings = {
        metric.metric_id: (metric.expression, metric.name, metric.unit, metric.dimensions)
        for metric in pdm_metric_registry().definitions()
    }

    def __init__(self, service: DashboardService):
        self.service = service

    def definition(self) -> DatasetDefinition:
        return DatasetDefinition(
            dataset_id=self.dataset_id,
            name="工业设备 PdM",
            aliases=["pdm", "工业设备"],
            description="设备、故障、Error、Maintenance、Telemetry",
            entity_key="machineId",
            time_kind="calendar",
            fields=[
                FieldDefinition(name="machineId", type="integer", role="entity", description="设备 ID"),
                FieldDefinition(name="datetime", type="datetime", role="time", description="来源事件时间"),
                FieldDefinition(name="model", type="string", role="dimension", description="设备型号"),
                FieldDefinition(name="failureType", type="string", role="dimension", description="故障部件"),
                FieldDefinition(name="errorId", type="string", role="dimension", description="错误类型"),
            ],
            capabilities=["overview", "comparison", "ranking", "trend", "distribution"],
            limitations=[
                "故障率为筛选期内故障设备数/设备数，不是故障事件数/设备数。",
                "维修/故障的时间关系不是因果关系。",
                "无设备的分母为零时故障设备占比不可用，不解释为零风险。",
                "没有修复完成时间或维修时长，MTTR 不可用；"
                "遥测行数/故障数仅是观测小时代理，不是真实运行 MTBF。",
                "缺少计划生产时长、节拍、总产量与合格产量，不能计算 OEE 或 FPY。",
            ],
            dashboard_route="/dashboard?dataset=industrial_pdm",
        )

    def metrics(self) -> list[MetricDefinition]:
        return pdm_metric_registry().definitions()

    def validate(self, plan: BIPlan) -> PdmFilters:
        filters = PdmFilters.model_validate(plan.filters)
        if plan.dataset_id != self.dataset_id:
            raise DataAgentError("Metric plan dataset does not match this adapter")
        if len(set(plan.metric_ids)) != len(plan.metric_ids):
            raise DataAgentError("Duplicate metrics")
        if plan.intent not in self.definition().capabilities:
            raise DataAgentError(
                "This PdM BI operation is not supported; use the existing full DataAgent analysis"
            )
        pdm_result_bindings(plan.metric_ids, plan.dimension)
        if plan.intent == "trend" and plan.dimension != "period":
            raise DataAgentError("PdM trend requires period")
        if plan.limit > 100:
            raise DataAgentError("PdM ranking limit must not exceed 100")
        if plan.dimension == "machineId" and plan.metric_ids[0] != "failure_count":
            raise DataAgentError("设备排名由 failure_count 排序；Error/维修可作为附加指标")
        return filters

    async def dashboard_filters(self, question: str, filters: dict[str, Any]) -> dict[str, Any]:
        output = dict(filters)
        for pattern, key in [
            (r"(?:machine_?id|设备(?:\s*ID)?)\s*[:=：]?\s*(\d+)", "machine_id"),
            (r"\b(model\d+)\b", "model"),
            (r"\b(comp\d+)\b", "failure_type"),
        ]:
            match = re.search(pattern, question, re.I)
            if match:
                output[key] = int(match[1]) if key == "machine_id" else match[1].lower()
        dates = re.findall(r"\b20\d{2}-\d{2}-\d{2}\b", question)
        if len(dates) == 2:
            output.update(start_date=dates[0], end_date=dates[1])
        recent = re.search(r"(?:最近|过去)\s*(7|30|90)\s*天", question)
        if recent:
            options = await self.service.filter_options()
            from datetime import date

            end = date.fromisoformat(options["dataRange"]["endDate"])
            output.update(
                start_date=(end - timedelta(days=int(recent[1]) - 1)).isoformat(), end_date=end.isoformat()
            )
        return self.filter_model.model_validate(output).model_dump(mode="json")

    async def query(self, plan: BIPlan) -> dict[str, Any]:
        filters = DashboardFilterInput(**self.validate(plan).model_dump())
        dimension = plan.dimension
        registry = pdm_metric_registry()
        bindings = pdm_result_bindings(plan.metric_ids, dimension)
        if dimension == "model":
            response = await self.service.failure_by_model(filters)
            operation = "DashboardService.failure_by_model"
        elif dimension == "period":
            response = await self.service.failure_trend(filters, plan.grain)
            operation = "DashboardService.failure_trend"
        elif dimension == "failureType":
            response = await self.service.failure_types(filters)
            operation = "DashboardService.failure_types"
        elif dimension == "errorId":
            response = await self.service.error_ranking(filters, plan.limit + 1)
            operation = "DashboardService.error_ranking"
        elif dimension == "machineId":
            response = await self.service.machine_ranking(filters, plan.limit + 1)
            operation = "DashboardService.machine_ranking"
        else:
            overview = await self.service.overview(filters)
            response = {"items": [overview], "scope": overview.get("scope")}
            operation = "DashboardService.overview"
        rows = []
        for row in response["items"]:
            mapped = {dimension: row[dimension]} if dimension else {}
            for binding in bindings:
                mapped[binding.metric_id] = binding.read(row)
            rows.append(mapped)
        return {
            "column": ([dimension] if dimension else []) + plan.metric_ids,
            "data": rows[: plan.limit],
            "truncated": len(rows) > plan.limit,
            "metric_definitions": registry.public_definitions(plan.metric_ids),
            "source": {
                "dataset_id": self.dataset_id,
                "service": "DashboardService",
                "scope": response.get("scope"),
                "metric_registry": registry.provenance(
                    plan.metric_ids,
                    execution={
                        "implementation_id": operation,
                        "dimension": dimension,
                        "grain": plan.grain if dimension == "period" else None,
                        "scope": response.get("scope"),
                        "bindings": [
                            {
                                "metric_id": binding.metric_id,
                                "result_column": binding.metric_id,
                                "source_column": binding.source_column,
                                "numerator_column": binding.numerator_column,
                                "denominator_column": binding.denominator_column,
                            }
                            for binding in bindings
                        ],
                    },
                ),
            },
            "limitations": self.definition().limitations,
        }

    async def dashboard(
        self, raw_filters: dict[str, Any], feature: str | None = None, dimension: str | None = None
    ) -> dict[str, Any]:
        filters = DashboardFilterInput(**PdmFilters.model_validate(raw_filters).model_dump())
        response = await self.service.analysis_context(filters)
        registry = pdm_metric_registry()
        response["metric_definitions"] = registry.public_definitions()
        response["metric_registry"] = registry.provenance(
            execution={
                "implementation_id": "DashboardService.analysis_context",
                "scope": response.get("scope"),
            }
        )
        response["limitations"] = self.definition().limitations
        # Use the exact same registry rate policy as BI chat, including empty populations.
        overview = response.get("evidence", {}).get("overview")
        if isinstance(overview, dict):
            overview["failureRatePct"] = pdm_result_bindings(["failure_rate_pct"], None)[0].read(overview)
        return response


class DatasetCatalog:
    def __init__(self, settings: Settings, dashboard: DashboardService):
        self.adapters: dict[str, DatasetAdapter] = {}
        self.register(IndustrialPDMAdapter(dashboard))
        self.register(ScaniaComponentXAdapter(settings))

    def register(self, adapter: DatasetAdapter) -> None:
        if adapter.dataset_id in self.adapters:
            raise ValueError(f"Duplicate dataset: {adapter.dataset_id}")
        self.adapters[adapter.dataset_id] = adapter

    def get(self, dataset_id: str) -> DatasetAdapter:
        if dataset_id not in self.adapters:
            raise NotFoundError("Unknown dataset")
        return self.adapters[dataset_id]

    def listing(self) -> list[dict[str, Any]]:
        items = []
        for key, adapter in self.adapters.items():
            try:
                definition = adapter.definition()
                items.append(
                    {
                        "dataset_id": key,
                        "name": definition.name,
                        "available": True,
                        "time_kind": definition.time_kind,
                        "route": definition.dashboard_route,
                    }
                )
            except DataAgentError as exc:
                items.append({"dataset_id": key, "name": key, "available": False, "reason": exc.message})
        return items

    def resolve(self, question: str, explicit: str | None, inherited: str | None) -> str | None:
        if explicit:
            self.get(explicit)
            return explicit
        matches = []
        for key, adapter in self.adapters.items():
            try:
                aliases = adapter.definition().aliases
            except DataAgentError:
                aliases = [key]
            if any(alias.casefold() in question.casefold() for alias in [key, *aliases]):
                matches.append(key)
        if len(matches) > 1:
            raise DataAgentError("问题涉及多个数据集，请分别分析或明确当前数据集")
        return matches[0] if matches else inherited
