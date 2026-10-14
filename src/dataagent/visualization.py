from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator


class ChartSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["table", "column", "bar", "line", "pie", "scatter", "heatmap", "stacked_bar"]
    title: str = ""
    dimension: str | None = Field(
        None,
        validation_alias=AliasChoices("dimension", "x"),
        description="类别/时间字段；散点图为数值自变量。不是物理横轴，横条图也填类别字段。",
    )
    measures: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("measures", "y"),
        description="数值度量字段名列表，不能填设备名或型号等类别；不是物理纵轴。",
    )
    group_by: str | None = Field(
        None,
        validation_alias=AliasChoices("group_by", "category"),
        description="长表趋势/比较图的系列分组字段；热力图的第二维度。无分组填 null。",
    )

    @model_validator(mode="before")
    @classmethod
    def legacy_category(cls, value: Any) -> Any:
        # Previously category had meaning only for heatmaps; do not reinterpret
        # unused legacy metadata as a new grouping instruction on old reports.
        if isinstance(value, dict) and "category" in value and value.get("type") != "heatmap":
            value = {key: item for key, item in value.items() if key != "category"}
        return value

    # Accept historical persisted x/y/category specs at the boundary. New JSON
    # schemas and stored decisions only expose semantic names, never axis names.
    @property
    def x(self) -> str | None:
        return self.dimension

    @property
    def y(self) -> list[str]:
        return self.measures

    @property
    def category(self) -> str | None:
        return self.group_by

    def legacy_display(self) -> dict[str, Any]:
        return {"type": self.type, "title": self.title, "x": self.dimension, "y": self.measures}


def number(value: Any) -> float | None:
    if value is None or isinstance(value, bool) or (isinstance(value, str) and not value.strip()):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def chart_from_result(payload: dict[str, Any], spec: ChartSpec) -> dict[str, Any] | None:
    """The model chooses fields; all plotted values come from the stored SQL result."""
    if spec.type == "table":
        return None
    if spec.type in {"column", "bar", "line", "scatter"} and len(spec.measures) > 1:
        # Numeric types alone cannot establish compatible units. Isolate each
        # measure in its own panel instead of mixing %, hours, money and counts
        # on a shared value axis. No name-based unit guesses or data conversion.
        if len(set(spec.measures)) != len(spec.measures):
            raise ValueError("Chart measures must be distinct")
        if len(spec.measures) > 4:
            raise ValueError("At most four measure panels per chart; select the most relevant measures")
        count = len(spec.measures)
        option = {
            "title": [{"text": spec.title or "查询结果", "left": 12, "top": 0}],
            "tooltip": {"trigger": "axis", "renderMode": "richText"},
            "legend": {"type": "scroll", "top": 30},
            "grid": [],
            "xAxis": [],
            "yAxis": [],
            "series": [],
        }
        for index, measure in enumerate(spec.measures):
            panel = chart_from_result(payload, spec.model_copy(update={"measures": [measure]}))
            assert panel is not None
            top = 15 + index * 82 / count
            option["title"].append(
                {"text": measure, "left": 16, "top": f"{top - 4}%", "textStyle": {"fontSize": 12}}
            )
            option["grid"].append(
                {
                    "top": f"{top}%",
                    "height": f"{82 / count - 8}%",
                    "left": 65,
                    "right": 30,
                    "containLabel": True,
                }
            )
            option["xAxis"].append({**panel["xAxis"], "gridIndex": index})
            option["yAxis"].append({**panel["yAxis"], "gridIndex": index})
            option["series"].extend(
                {**series, "xAxisIndex": index, "yAxisIndex": index} for series in panel["series"]
            )
        return option
    rows, columns = payload["data"], payload["column"]
    if len(rows) < 2 or spec.x not in columns or not spec.y:
        raise ValueError("A chart requires at least two rows and existing dimension/measure fields")
    if len(spec.y) != len(set(spec.y)) or any(y not in columns or y == spec.x for y in spec.y):
        raise ValueError("Chart fields must be distinct, existing result columns")
    if spec.group_by is not None and (
        spec.group_by not in columns or spec.group_by in [spec.dimension, *spec.measures]
    ):
        raise ValueError("group_by must be a separate, existing category column")
    if spec.group_by and spec.type in {"pie", "scatter", "stacked_bar"}:
        raise ValueError("This chart type does not support group_by; use a compatible shape")
    values = {y: [number(row.get(y)) for row in rows] for y in spec.y}
    for y in spec.y:
        if not any(v is not None for v in values[y]):
            raise ValueError(f"No numeric values for {y}")
        if any(row.get(y) is not None and number(row[y]) is None for row in rows):
            raise ValueError(f"Non-numeric values in measure {y}; aggregate or clean with SQL first")
    labels = [str(row.get(spec.x)) if row.get(spec.x) is not None else "(空值)" for row in rows]
    option: dict[str, Any] = {
        "title": {"text": spec.title or "查询结果", "left": 12},
        "tooltip": {"trigger": "axis", "renderMode": "richText"},
        "legend": {"type": "scroll", "top": 35},
        "grid": {"top": 80, "left": 65, "right": 30, "bottom": 65, "containLabel": True},
    }
    if spec.type == "pie":
        series = values[spec.y[0]]
        if len(spec.y) != 1 or len(rows) > 8 or len(set(labels)) != len(labels):
            raise ValueError("Pie charts require one measure and 2–8 distinct categories")
        if any(v is None or v < 0 for v in series) or sum(v for v in series if v is not None) <= 0:
            raise ValueError("Pie values must be non-negative, non-null and have a positive total")
        option.update(
            tooltip={"trigger": "item", "renderMode": "richText"},
            series=[
                {
                    "type": "pie",
                    "name": spec.y[0],
                    "radius": ["30%", "65%"],
                    "data": [
                        {"name": label, "value": value} for label, value in zip(labels, series, strict=True)
                    ],
                }
            ],
        )
    elif spec.type == "scatter":
        x_values = [number(row.get(spec.x)) for row in rows]
        if any(value is None for value in x_values):
            raise ValueError("Scatter X values must be numeric and non-null")
        option.update(
            xAxis={"type": "value", "name": spec.x},
            yAxis={"type": "value"},
            series=[
                {
                    "type": "scatter",
                    "name": y,
                    "data": [[x, v] for x, v in zip(x_values, values[y], strict=True) if v is not None],
                }
                for y in spec.y
            ],
        )
    elif spec.type == "heatmap":
        if spec.category not in columns or spec.category == spec.x or len(spec.y) != 1:
            raise ValueError("Heatmaps require two different category fields and one measure")
        xs = list(dict.fromkeys(labels))
        ys = list(dict.fromkeys(str(row.get(spec.category)) for row in rows))
        cells, seen = [], set()
        for row, label, value in zip(rows, labels, values[spec.y[0]], strict=True):
            cell = (label, str(row.get(spec.category)))
            if cell in seen:
                raise ValueError("Duplicate heatmap cells; aggregate the two dimensions in SQL first")
            seen.add(cell)
            if value is not None:
                cells.append([xs.index(cell[0]), ys.index(cell[1]), value])
        option.update(
            tooltip={"position": "top", "renderMode": "richText"},
            xAxis={"type": "category", "data": xs},
            yAxis={"type": "category", "data": ys},
            visualMap={
                "min": min(c[2] for c in cells),
                "max": max(c[2] for c in cells),
                "calculable": True,
                "orient": "horizontal",
                "bottom": 0,
            },
            series=[{"type": "heatmap", "data": cells, "name": spec.y[0], "label": {"show": True}}],
        )
    else:
        if spec.group_by:
            groups = [str(row.get(spec.group_by)) for row in rows]
            pairs = list(zip(labels, groups, strict=True))
            if len(set(pairs)) != len(pairs):
                raise ValueError("Repeated dimension/group_by pairs; aggregate in SQL first")
        elif len(set(labels)) != len(labels):
            raise ValueError("Repeated categories; supply group_by or aggregate in SQL first")
        if spec.type == "stacked_bar" and len(spec.y) < 2:
            raise ValueError("Stacked bars require at least two compatible measures")
        horizontal = spec.type == "bar"
        category = {"type": "category", "data": labels}
        value_axis = {"type": "value"}
        option.update(
            xAxis=value_axis if horizontal else category, yAxis=category if horizontal else value_axis
        )
        option["series"] = [
            {
                "name": y,
                "type": "line" if spec.type == "line" else "bar",
                "data": values[y],
                **({"stack": "total"} if spec.type == "stacked_bar" else {}),
            }
            for y in spec.y
        ]
        if spec.group_by:
            categories = list(dict.fromkeys(labels))
            category["data"] = categories
            option["series"] = []
            for group in dict.fromkeys(groups):
                for measure in spec.measures:
                    points = {
                        label: value
                        for label, row_group, value in zip(labels, groups, values[measure], strict=True)
                        if row_group == group
                    }
                    option["series"].append(
                        {
                            "name": group if len(spec.measures) == 1 else f"{group} / {measure}",
                            "type": "line" if spec.type == "line" else "bar",
                            "data": [points.get(label) for label in categories],
                            "connectNulls": False,
                        }
                    )
        if len(rows) > 20:
            option["dataZoom"] = [{"type": "slider", **({"yAxisIndex": 0} if horizontal else {})}]
    return option


def result_profile(payload: dict[str, Any]) -> dict[str, Any]:
    rows = payload["data"]
    fields = {}
    for column in payload["column"]:
        raw = [row.get(column) for row in rows]
        numeric = [number(v) for v in raw if v is not None]
        summary: dict[str, Any] = {
            "nullCount": sum(v is None for v in raw),
            "distinctCount": len({str(v) for v in raw if v is not None}),
            "type": "numeric" if numeric and all(v is not None for v in numeric) else "category",
        }
        if numeric and all(value is not None for value in numeric):
            summary.update(min=min(numeric), max=max(numeric), mean=sum(numeric) / len(numeric))
        fields[column] = summary
    return {
        "returnedRows": len(rows),
        "truncated": bool(payload.get("truncated")),
        "fields": fields,
        "scope": "Statistics describe returned query rows, not unqueried records or causal relationships.",
    }
