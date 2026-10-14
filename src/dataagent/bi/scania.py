from __future__ import annotations

import asyncio
import csv
import math
import re
import threading
from collections.abc import Callable
from typing import Any, TypeVar

from dataagent.bi.contracts import (
    BIPlan,
    DatasetDefinition,
    FieldDefinition,
    MetricDefinition,
    ScaniaFilters,
)
from dataagent.bi.metric_registry import MetricRegistry, scania_metric_contract
from dataagent.config import Settings
from dataagent.errors import ConfigurationError, DataAgentError, NotFoundError

T = TypeVar("T")
SPLITS = ("train", "validation", "test")
SPECS = tuple(f"Spec_{i}" for i in range(8))
LABELS = {
    0: "0 · 不在已标注临近事件窗口",
    1: "1 · 事件前 48–24 相对时间单位",
    2: "2 · 事件前 24–12 相对时间单位",
    3: "3 · 事件前 12–6 相对时间单位",
    4: "4 · 事件前 6–0 相对时间单位",
}
LIMITATIONS = [
    "time_step 是经匿名化处理的相对时间，不是日期、小时或天。",
    "运行计数器/直方图 bins 与 Spec_0–Spec_7 的物理含义未知，不推断温度、压力或车型。",
    "train 的维修事件与 validation/test 的最后观测点分类标签不能混算故障率。",
    "in_study_repair=0 是研究期内未观测到维修（右删失），观察时长不等于寿命。",
    "标签车辆排序是已知标签的回顾性展示，不是模型预测；相关性不构成因果证据。",
    "相对时间过滤 readout；车辆指标统计该区间有观测的车辆，标签仍是来源文件的标签。",
]


def finite(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


class ScaniaComponentXAdapter:
    """Semantic profile + bounded DuckDB execution on the existing local CSV files.

    SQL fragments are code-owned. User/model input supplies registered names and bound values only.
    Each operation owns its connection; cancellation interrupts that connection and joins its worker.
    """

    dataset_id = "scania_component_x"
    filter_model = ScaniaFilters

    def __init__(self, settings: Settings):
        self.settings = settings
        self.root = settings.scania_data_dir
        self._slots = asyncio.Semaphore(settings.bi_max_concurrency)

    def headers(self) -> dict[str, dict[str, str]]:
        if not self.root or not self.root.is_dir():
            raise ConfigurationError("请配置 DATAAGENT_SCANIA_DATA_DIR，指向已有 SCANIA 数据目录")
        result: dict[str, dict[str, str]] = {}
        for split in SPLITS:
            names = [
                f"{split}_specifications",
                f"{split}_operational_readouts",
                "train_tte" if split == "train" else f"{split}_labels",
            ]
            for name in names:
                path = self.root / "raw" / f"{name}.csv"
                if not path.is_file():
                    raise ConfigurationError(f"缺少已有 SCANIA 文件：{name}.csv；不会自动下载")
                with path.open(encoding="utf-8-sig", newline="") as handle:
                    columns = next(csv.reader(handle), [])
                if not columns or len(set(columns)) != len(columns):
                    raise ConfigurationError(f"Invalid or duplicate CSV columns: {name}")
                if name.endswith("specifications"):
                    expected = {"vehicle_id", *SPECS}
                elif name.endswith("tte"):
                    expected = {"vehicle_id", "in_study_repair", "length_of_study_time_step"}
                elif name.endswith("labels"):
                    expected = {"vehicle_id", "class_label"}
                else:
                    expected = {"vehicle_id", "time_step"}
                    if not all(re.fullmatch(r"\d+_\d+", c) for c in columns if c not in expected):
                        raise ConfigurationError(f"Unexpected operational field in {name}")
                if not expected.issubset(columns):
                    raise ConfigurationError(f"Schema mismatch: {name} requires {sorted(expected)}")
                result[name] = {
                    c: "VARCHAR"
                    if c in SPECS
                    else "BIGINT"
                    if c in {"vehicle_id", "class_label", "in_study_repair"}
                    else "DOUBLE"
                    for c in columns
                }
        reference = result["train_operational_readouts"]
        if any(result[f"{s}_operational_readouts"] != reference for s in SPLITS):
            raise ConfigurationError("SCANIA split operational schemas differ")
        return result

    def features(self) -> list[str]:
        return [
            c for c in self.headers()["train_operational_readouts"] if c not in {"vehicle_id", "time_step"}
        ]

    async def dashboard_filters(self, question: str, filters: dict[str, Any]) -> dict[str, Any]:
        output = {**filters, "specifications": dict(filters.get("specifications") or {})}
        for key, pattern in [
            ("train", r"训练集|\btrain\b"),
            ("validation", r"验证集|\bvalidation\b"),
            ("test", r"测试集|\btest\b"),
        ]:
            if re.search(pattern, question, re.I):
                if key != output.get("split"):
                    output.update(vehicle_id=None, class_label=None, in_study_repair=None)
                output["split"] = key
        vehicle = re.search(r"(?:vehicle(?:_id)?|车辆(?:\s*ID)?|车号)\s*[:=：]?\s*(\d+)", question, re.I)
        if vehicle:
            output["vehicle_id"] = int(vehicle[1])
        period = re.search(
            r"time_step\s*[:=：]?\s*(\d+(?:\.\d+)?)\s*[-~至到]\s*(\d+(?:\.\d+)?)", question, re.I
        )
        if period:
            output.update(min_time=float(period[1]), max_time=float(period[2]))
        for match in re.finditer(r"Spec_([0-7])\s*[:=：]\s*(Cat\d+)", question, re.I):
            output["specifications"][f"Spec_{match[1]}"] = "Cat" + match[2][3:]
        return self.filter_model.model_validate(output).model_dump(mode="json")

    def definition(self) -> DatasetDefinition:
        fields = [
            FieldDefinition(
                name="vehicle_id", type="integer", role="entity", description="匿名车辆 ID", anonymized=True
            ),
            FieldDefinition(name="time_step", type="number", role="time", description="相对运行时间"),
            FieldDefinition(
                name="class_label", type="integer", role="label", description="验证/测试集最后 readout 标签"
            ),
            FieldDefinition(
                name="in_study_repair", type="integer", role="label", description="训练期首次维修事件指示"
            ),
            FieldDefinition(
                name="length_of_study_time_step",
                type="number",
                role="measure",
                description="事件或删失观察时长；不是每辆车的故障时间",
            ),
        ]
        fields.extend(
            FieldDefinition(
                name=c, type="string", role="dimension", description="匿名分类规格", anonymized=True
            )
            for c in SPECS
        )
        fields.extend(
            FieldDefinition(
                name=c,
                type="number",
                role="measure",
                description="匿名运行计数器/直方图 bin",
                anonymized=True,
            )
            for c in self.features()
        )
        return DatasetDefinition(
            dataset_id=self.dataset_id,
            name="SCANIA Component X",
            aliases=["scania", "斯堪尼亚", "component x"],
            description="同 split 车辆规格、运行观测与事件/标签分析",
            entity_key="vehicle_id",
            time_kind="relative",
            fields=fields,
            relationships=[
                {
                    "from": "readouts.vehicle_id",
                    "to": "specifications.vehicle_id",
                    "cardinality": "many_to_one",
                    "scope": "same_split",
                },
                {
                    "from": "labels_or_tte.vehicle_id",
                    "to": "specifications.vehicle_id",
                    "cardinality": "one_to_one",
                    "scope": "same_split",
                },
            ],
            capabilities=[
                "overview",
                "comparison",
                "ranking",
                "trend",
                "distribution",
                "correlation",
                "period_comparison",
                "detail",
            ],
            limitations=LIMITATIONS,
            dashboard_route="/dashboard?dataset=scania_component_x",
        )

    def metrics(self) -> list[MetricDefinition]:
        entity_dims = [*SPECS, "class_label", "in_study_repair", "vehicle_id"]

        def metric(
            key: str,
            name: str,
            expression: str,
            description: str,
            unit: str = "count",
            splits: list[str] | None = None,
        ) -> MetricDefinition:
            return MetricDefinition(
                metric_id=key,
                name=name,
                expression=expression,
                description=description,
                unit=unit,
                relation="entities",
                dimensions=entity_dims,
                splits=splits or list(SPLITS),
            )

        metrics = [
            metric("vehicle_count", "车辆数", "COUNT(*)", "当前 split 和筛选范围的独立车辆数"),
            metric(
                "repair_vehicle_count",
                "观察期维修车辆",
                "COALESCE(SUM(in_study_repair),0)",
                "train 中首次 Component X 维修事件车辆数",
                splits=["train"],
            ),
            metric(
                "repair_vehicle_share_pct",
                "观察期维修车辆占比",
                "100.0 * SUM(in_study_repair) / NULLIF(COUNT(*),0)",
                "维修车辆数 / 当前训练车辆数；不是预测概率或发生强度",
                "%",
                ["train"],
            ),
            metric(
                "censored_vehicle_count",
                "未观测维修车辆",
                "COALESCE(SUM(1-in_study_repair),0)",
                "研究期内未观测到维修事件的车辆数（右删失）",
                splits=["train"],
            ),
            metric(
                "study_duration_mean",
                "平均观察时长",
                "AVG(length_of_study_time_step)",
                "事件和删失共同的平均观察时长，不等于平均寿命",
                "relative_time",
                ["train"],
            ),
            metric(
                "label_window_vehicle_count",
                "临近事件窗口标签车辆",
                "COALESCE(SUM(CASE WHEN class_label>0 THEN 1 ELSE 0 END),0)",
                "最后 readout 已知 class_label 1–4 的车辆数；不代表模型预测",
                splits=["validation", "test"],
            ),
        ]
        metrics.append(
            MetricDefinition(
                metric_id="known_label_level",
                name="已知临近事件标签等级",
                expression="MAX(class_label)",
                relation="entities",
                unit="ordinal_label",
                description="仅按车辆展示 class_label 0–4；等级越高越接近已标注事件，不是预测概率",
                dimensions=["vehicle_id"],
                splits=["validation", "test"],
            )
        )
        dims = [*SPECS, "vehicle_id", "time_bucket"]
        metrics.append(
            MetricDefinition(
                metric_id="readout_count",
                name="运行观测数",
                expression="COUNT(*)",
                description="当前筛选范围 readout 行数",
                relation="readouts",
                dimensions=dims,
            )
        )
        for feature in self.features():
            metrics.append(
                MetricDefinition(
                    metric_id=f"mean_{feature}",
                    name=f"{feature} 均值",
                    expression=f'AVG("{feature}")',
                    description="非缺失 readout 的均值；按观测行加权，物理单位未知",
                    unit="unknown",
                    relation="readouts",
                    dimensions=dims,
                )
            )
        return [scania_metric_contract(metric) for metric in metrics]

    def metric_registry(self) -> MetricRegistry:
        return MetricRegistry(self.dataset_id, self.metrics())

    async def run(self, filters: ScaniaFilters, operation: Callable[[Any], T]) -> T:
        headers = self.headers()
        state: dict[str, Any] = {"connection": None}
        guard = threading.Lock()
        stopped = threading.Event()

        def execute() -> T:
            try:
                import duckdb
            except ImportError as exc:
                raise ConfigurationError(
                    'SCANIA requires the optional BI dependencies: pip install -e ".[bi]"'
                ) from exc
            with duckdb.connect(
                config={
                    "threads": 2,
                    "memory_limit": f"{self.settings.bi_memory_limit_mb}MB",
                    "autoinstall_known_extensions": False,
                    "autoload_known_extensions": False,
                }
            ) as connection:
                with guard:
                    state["connection"] = connection
                try:
                    if stopped.is_set():
                        raise DataAgentError("Query cancelled", status_code=409)
                    self._views(connection, filters, headers)
                    if stopped.is_set():
                        raise DataAgentError("Query cancelled", status_code=409)
                    return operation(connection)
                finally:
                    with guard:
                        state["connection"] = None

        async with self._slots:
            worker = asyncio.create_task(asyncio.to_thread(execute))
            try:
                return await asyncio.wait_for(asyncio.shield(worker), self.settings.bi_query_timeout_seconds)
            except (TimeoutError, asyncio.CancelledError) as exc:
                stopped.set()
                with guard:
                    if state["connection"] is not None:
                        state["connection"].interrupt()
                await asyncio.gather(worker, return_exceptions=True)
                if isinstance(exc, TimeoutError):
                    raise DataAgentError("BI 查询超时，连接已中断，请缩小范围", status_code=504) from exc
                raise
            except DataAgentError:
                raise
            except Exception as exc:
                raise DataAgentError(
                    f"SCANIA 数据读取/聚合失败 ({type(exc).__name__})", status_code=503
                ) from exc

    def _views(self, con: Any, filters: ScaniaFilters, headers: dict[str, dict[str, str]]) -> None:
        assert self.root is not None
        split = filters.split
        files = {
            "specs": f"{split}_specifications",
            "raw_readouts": f"{split}_operational_readouts",
            "outcomes": "train_tte" if split == "train" else f"{split}_labels",
        }
        for view, name in files.items():
            con.read_csv(
                str(self.root / "raw" / f"{name}.csv"),
                header=True,
                columns=headers[name],
                na_values=["", "na", "NaN", "nan"],
            ).create_view(view)
        # Validate the small one-row-per-vehicle sides before any join. Do not silently
        # multiply observations or discard entities if source files are replaced.
        for view in ("specs", "outcomes"):
            total, keys, distinct = con.execute(
                f"SELECT COUNT(*),COUNT(vehicle_id),COUNT(DISTINCT vehicle_id) FROM {view}"
            ).fetchone()
            if total != keys or keys != distinct:
                raise ConfigurationError(f"{view}: vehicle_id must be non-null and unique")
        unmatched = con.execute(
            "SELECT COUNT(*) FROM specs s FULL OUTER JOIN outcomes o ON s.vehicle_id=o.vehicle_id "
            "WHERE s.vehicle_id IS NULL OR o.vehicle_id IS NULL"
        ).fetchone()[0]
        if unmatched:
            raise ConfigurationError(
                "Specifications and outcomes must cover the same vehicles within a split"
            )
        outcome_columns = [c for c in headers[files["outcomes"]] if c != "vehicle_id"]
        label_column = "in_study_repair" if split == "train" else "class_label"
        label_values = "0,1" if split == "train" else "0,1,2,3,4"
        invalid_labels = con.execute(
            f'SELECT COUNT(*) FROM outcomes WHERE "{label_column}" IS NULL '
            f'OR "{label_column}" NOT IN ({label_values})'
        ).fetchone()[0]
        if invalid_labels:
            raise ConfigurationError(
                f"{label_column}: missing or invalid source outcome labels; cannot publish outcome metrics"
            )
        projections = ["s.vehicle_id", *(f's."{c}"' for c in SPECS), *(f'o."{c}"' for c in outcome_columns)]
        conditions, params = [], []
        for column, value in [("vehicle_id", filters.vehicle_id), *filters.specifications.items()]:
            if value is not None:
                conditions.append(f's."{column}" = ?')
                params.append(value)
        for column, value in [
            ("class_label", filters.class_label),
            ("in_study_repair", filters.in_study_repair),
        ]:
            if value is not None:
                conditions.append(f'o."{column}" = ?')
                params.append(value)
        if filters.vehicle_id is not None:
            if not con.execute(
                "SELECT 1 FROM specs WHERE vehicle_id=? LIMIT 1", [filters.vehicle_id]
            ).fetchone():
                raise NotFoundError("Vehicle does not exist in the selected split")
        time_conditions, time_values = self.time_predicate(filters)
        if time_conditions:
            conditions.append(
                "EXISTS (SELECT 1 FROM raw_readouts r WHERE r.vehicle_id=s.vehicle_id AND "
                + " AND ".join(time_conditions)
                + ")"
            )
            params.extend(time_values)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        # Only bounded vehicle metadata is materialized in memory; operational CSVs remain views.
        con.execute(
            "CREATE TEMP TABLE entities AS SELECT "
            + ",".join(projections)
            + " FROM specs s JOIN outcomes o ON o.vehicle_id=s.vehicle_id"
            + where,
            params,
        )
        readout_fields = list(headers[files["raw_readouts"]])
        selections = [*(f'r."{c}"' for c in readout_fields), *(f'e."{c}"' for c in SPECS)]
        # Parameters are evaluated into a relation, then stored as a view (no string interpolation of values).
        # DuckDB cannot create a view with bound parameters; parameterized filter creates an eligible
        # relation at query time instead. The base view contains only already filtered vehicle entities.
        con.execute(
            "CREATE VIEW readouts AS SELECT "
            + ",".join(selections)
            + " FROM raw_readouts r JOIN entities e ON e.vehicle_id=r.vehicle_id"
        )

    @staticmethod
    def time_predicate(filters: ScaniaFilters, prefix: str = "r.") -> tuple[list[str], list[Any]]:
        conditions, values = [], []
        if filters.min_time is not None:
            conditions.append(f"{prefix}time_step >= ?")
            values.append(filters.min_time)
        if filters.max_time is not None:
            conditions.append(f"{prefix}time_step <= ?")
            values.append(filters.max_time)
        return conditions, values

    @staticmethod
    def rows(con: Any, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
        cursor = con.execute(sql, params or [])
        columns = [d[0] for d in cursor.description]
        return [{k: finite(v) for k, v in zip(columns, row, strict=True)} for row in cursor.fetchall()]

    def validate(self, plan: BIPlan) -> tuple[ScaniaFilters, list[MetricDefinition]]:
        filters = ScaniaFilters.model_validate(plan.filters)
        if plan.dataset_id != self.dataset_id:
            raise DataAgentError("Metric plan dataset does not match this adapter")
        metrics = self.metric_registry().definitions(plan.metric_ids)
        if len(set(plan.metric_ids)) != len(plan.metric_ids):
            raise DataAgentError("Duplicate metrics")
        if len({m.relation for m in metrics}) != 1:
            raise DataAgentError("Entity and readout metrics must be queried separately to avoid fan-out")
        if any(m.splits and filters.split not in m.splits for m in metrics):
            raise DataAgentError("Metric is not defined for this split")
        if plan.dimension and any(plan.dimension not in m.dimensions for m in metrics):
            raise DataAgentError("Dimension is not allowed for the selected metric")
        if plan.dimension == "class_label" and filters.split == "train":
            raise DataAgentError("train has no class_label")
        if plan.dimension == "in_study_repair" and filters.split != "train":
            raise DataAgentError("Only train has in_study_repair")
        if plan.intent == "correlation" and (
            len(metrics) != 2 or any(not m.metric_id.startswith("mean_") for m in metrics)
        ):
            raise DataAgentError("Correlation requires two registered operational mean_* metrics")
        if plan.intent == "period_comparison" and (
            metrics[0].relation != "readouts"
            or filters.min_time is None
            or filters.max_time is None
            or filters.min_time == filters.max_time
        ):
            raise DataAgentError(
                "Period comparison requires readout metrics and a nonempty relative-time range"
            )
        if plan.intent == "trend" and plan.dimension != "time_bucket":
            raise DataAgentError("SCANIA trend requires time_bucket, not a calendar date")
        if "known_label_level" in plan.metric_ids and plan.dimension != "vehicle_id":
            raise DataAgentError("Known label level is only defined for individual vehicles")
        return filters, metrics

    async def query(self, plan: BIPlan) -> dict[str, Any]:
        filters, metrics = self.validate(plan)
        registry = self.metric_registry()
        relation = metrics[0].relation
        conditions, params = self.time_predicate(filters, "") if relation == "readouts" else ([], [])
        where = " WHERE " + " AND ".join(conditions) if conditions else ""

        def execute(con: Any) -> dict[str, Any]:
            stats = None
            if plan.intent == "correlation":
                left, right = [m.metric_id.removeprefix("mean_") for m in metrics]
                sql = (
                    f'SELECT "{left}","{right}" FROM readouts'
                    + where
                    + f" ORDER BY vehicle_id,time_step LIMIT {plan.limit}"
                )
                stat_sql = (
                    f'SELECT corr("{left}","{right}") AS pearson_r,'
                    f' COUNT(CASE WHEN "{left}" IS NOT NULL AND "{right}" IS NOT NULL THEN 1 END)'
                    " AS paired_observations FROM readouts" + where
                )
                stats = self.rows(con, stat_sql, params)[0]
                stats["definition"] = (
                    "全量筛选 readout 的 Pearson 相关；重复车辆观测并不独立，不报告因果或 p 值"
                )
                columns = [left, right]
            else:
                select, group, prefix_params = [], "", []
                if plan.intent == "period_comparison":
                    assert filters.min_time is not None and filters.max_time is not None
                    midpoint = (filters.min_time + filters.max_time) / 2
                    select.append(
                        "CASE WHEN time_step < ? THEN 'first_half' ELSE 'second_half' END AS period"
                    )
                    prefix_params.append(midpoint)
                    group = " GROUP BY 1"
                elif plan.dimension:
                    select.append(
                        "FLOOR(time_step / ?) * ? AS time_bucket"
                        if plan.dimension == "time_bucket"
                        else f'"{plan.dimension}"'
                    )
                    if plan.dimension == "time_bucket":
                        prefix_params.extend([plan.bucket_width, plan.bucket_width])
                    group = " GROUP BY 1"
                select.extend(f'{m.expression} AS "{m.metric_id}"' for m in metrics)
                order = (
                    " ORDER BY 1"
                    if plan.dimension == "time_bucket" or plan.intent == "period_comparison"
                    else (f' ORDER BY "{metrics[0].metric_id}" DESC,1' if group else "")
                )
                sql = (
                    "SELECT "
                    + ",".join(select)
                    + f" FROM {relation}"
                    + where
                    + group
                    + order
                    + f" LIMIT {plan.limit + 1}"
                )
                columns = (
                    ["period"]
                    if plan.intent == "period_comparison"
                    else [plan.dimension]
                    if plan.dimension
                    else []
                )
                columns += [m.metric_id for m in metrics]
                params_to_use = prefix_params + params
            data = self.rows(con, sql, params if plan.intent == "correlation" else params_to_use)
            return {
                "column": columns,
                "data": data[: plan.limit],
                "truncated": len(data) > plan.limit,
                "sql": sql,
                "parameters": params if plan.intent == "correlation" else params_to_use,
                "statistics": stats,
                "metric_definitions": [m.model_dump() for m in metrics],
                "source": {
                    "dataset_id": self.dataset_id,
                    "split": filters.split,
                    "time_kind": "relative",
                    "filters": filters.model_dump(mode="json"),
                    "tables": [
                        f"{filters.split}_specifications.csv",
                        f"{filters.split}_operational_readouts.csv",
                        "train_tte.csv" if filters.split == "train" else f"{filters.split}_labels.csv",
                    ],
                    "join_policy": "same-split validated 1:1 metadata; readouts many:1 vehicle",
                    "metric_registry": registry.provenance(
                        plan.metric_ids,
                        execution={
                            "implementation_id": (
                                "ScaniaComponentXAdapter.pearson_readouts"
                                if plan.intent == "correlation"
                                else "ScaniaComponentXAdapter.registered_duckdb_aggregate.v1"
                            ),
                            "relation": relation,
                            "dimension": plan.dimension,
                            "bucket_width": plan.bucket_width if plan.dimension == "time_bucket" else None,
                            "scope": filters.model_dump(mode="json"),
                            "aggregation_applied": plan.intent != "correlation",
                            "bindings": [
                                {
                                    "metric_id": metric.metric_id,
                                    "result_column": (
                                        metric.metric_id.removeprefix("mean_")
                                        if plan.intent == "correlation"
                                        else metric.metric_id
                                    ),
                                    "value_kind": "raw_readout"
                                    if plan.intent == "correlation"
                                    else "aggregate",
                                }
                                for metric in metrics
                            ],
                        },
                    ),
                },
                "limitations": LIMITATIONS
                + (
                    ["散点仅展示按车辆/时间排序的前 N 个观测；相关系数使用全量配对观测"]
                    if plan.intent == "correlation"
                    else []
                ),
            }

        return await self.run(filters, execute)

    async def dashboard(
        self, raw_filters: dict[str, Any], feature: str | None = None, dimension: str | None = None
    ) -> dict[str, Any]:
        filters = ScaniaFilters.model_validate(raw_filters)
        feature = feature or self.features()[0]
        dimension = dimension or "Spec_0"
        if feature not in self.features() or dimension not in SPECS:
            raise DataAgentError("Unknown operational feature or specification")
        conditions, values = self.time_predicate(filters, "")
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        event_column = "in_study_repair" if filters.split == "train" else "class_label"

        def execute(con: Any) -> dict[str, Any]:
            registry = self.metric_registry()
            entity_metrics = [
                m
                for m in registry.definitions()
                if m.relation == "entities"
                and (not m.splits or filters.split in m.splits)
                and m.metric_id != "known_label_level"
            ]
            overview = self.rows(
                con,
                "SELECT "
                + ",".join(f'{m.expression} AS "{m.metric_id}"' for m in entity_metrics)
                + " FROM entities",
            )[0]
            time_sql = (
                f"SELECT FLOOR(time_step/20)*20 AS time_bucket,COUNT(*) AS readout_count,"
                f' AVG("{feature}") AS feature_mean,COUNT("{feature}") AS present_count,'
                f" MIN(time_step) AS min_time,MAX(time_step) AS max_time FROM readouts{where}"
                " GROUP BY 1 ORDER BY 1"
            )
            trend = self.rows(con, time_sql, values)
            overview["readout_count"] = sum(r["readout_count"] for r in trend)
            overview["min_time"] = min((r["min_time"] for r in trend), default=None)
            overview["max_time"] = max((r["max_time"] for r in trend), default=None)
            present = sum(r["present_count"] for r in trend)
            overview["feature_missing_pct"] = (
                100 * (overview["readout_count"] - present) / overview["readout_count"]
                if overview["readout_count"]
                else None
            )
            groups = self.rows(
                con,
                f'SELECT "{dimension}" AS category,COUNT(*) AS vehicle_count'
                f" FROM entities GROUP BY 1 ORDER BY 2 DESC,1 LIMIT 100",
            )
            labels = self.rows(
                con,
                f'SELECT "{event_column}" AS label,COUNT(*) AS vehicle_count'
                " FROM entities GROUP BY 1 ORDER BY 1",
            )
            ranking_sql = (
                f'SELECT vehicle_id,"{event_column}","{dimension}"'
                + (",length_of_study_time_step" if filters.split == "train" else "")
                + f' FROM entities ORDER BY "{event_column}" DESC,vehicle_id LIMIT 20'
            )
            ranking = self.rows(con, ranking_sql)
            duration = (
                self.rows(
                    con,
                    "SELECT FLOOR(length_of_study_time_step/20)*20 AS time_bucket,"
                    "in_study_repair,COUNT(*) AS vehicle_count FROM entities GROUP BY 1,2 ORDER BY 1,2",
                )
                if filters.split == "train"
                else []
            )
            return {
                "dataset_id": self.dataset_id,
                "filters": filters.model_dump(),
                "overview": overview,
                "metrics": [m.model_dump() for m in entity_metrics],
                "metric_definitions": registry.public_definitions(
                    [m.metric_id for m in entity_metrics] + ["readout_count", f"mean_{feature}"]
                ),
                "feature": feature,
                "dimension": dimension,
                "trend": trend,
                "specifications": groups,
                "labels": labels,
                "ranking": ranking,
                "duration": duration,
                "label_field": event_column,
                "label_definitions": LABELS,
                "limitations": LIMITATIONS,
                "evidence": {
                    "queries": [time_sql, ranking_sql],
                    "parameters": values,
                    "source_files": [
                        f"{filters.split}_specifications.csv",
                        f"{filters.split}_operational_readouts.csv",
                        "train_tte.csv" if filters.split == "train" else f"{filters.split}_labels.csv",
                    ],
                    "metric_registry": registry.provenance(
                        [m.metric_id for m in entity_metrics] + ["readout_count", f"mean_{feature}"],
                        execution={
                            "implementation_id": "ScaniaComponentXAdapter.dashboard",
                            "scope": filters.model_dump(mode="json"),
                            "bindings": [
                                {"metric_id": m.metric_id, "result_path": f"overview.{m.metric_id}"}
                                for m in entity_metrics
                            ]
                            + [
                                {"metric_id": "readout_count", "result_path": "overview.readout_count"},
                                {
                                    "metric_id": f"mean_{feature}",
                                    "result_path": "trend[].feature_mean",
                                    "dimension": "time_bucket",
                                    "bucket_width": 20,
                                },
                            ],
                        },
                    ),
                },
            }

        return await self.run(filters, execute)

    async def entity(self, vehicle_id: int, raw_filters: dict[str, Any], feature: str) -> dict[str, Any]:
        filters = ScaniaFilters.model_validate({**raw_filters, "vehicle_id": vehicle_id})
        if feature not in self.features():
            raise DataAgentError("Unknown operational feature")
        conditions, params = self.time_predicate(filters, "")
        where = " WHERE " + " AND ".join(conditions) if conditions else ""

        def execute(con: Any) -> dict[str, Any]:
            sql = (
                f'WITH points AS (SELECT time_step,"{feature}",ROW_NUMBER() OVER (ORDER BY time_step) AS rn,'
                f" COUNT(*) OVER () AS n FROM readouts{where}) SELECT MIN(time_step) AS time_step,"
                f' AVG("{feature}") AS value,COUNT(*) AS samples FROM points'
                " GROUP BY FLOOR((rn-1)/GREATEST(1,CEIL(n/500.0))) ORDER BY 1"
            )
            return {
                "vehicle_id": vehicle_id,
                "feature": feature,
                "points": self.rows(con, sql, params),
                "sampling": "连续时间序号分桶均值，最多 500 点",
                "sql": sql,
                "parameters": params,
            }

        return await self.run(filters, execute)
