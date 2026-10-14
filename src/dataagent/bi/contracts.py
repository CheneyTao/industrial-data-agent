from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class FieldDefinition(Contract):
    name: str
    type: str
    role: Literal["entity", "time", "dimension", "measure", "label"]
    description: str
    anonymized: bool = False


class MetricDefinition(Contract):
    metric_id: str
    name: str
    description: str
    unit: str = "count"
    relation: str
    expression: str = Field(exclude=True)
    dimensions: list[str] = Field(default_factory=list)
    splits: list[str] = Field(default_factory=list)
    null_policy: str = "NULL means unavailable; aggregates skip missing values"
    version: str = Field("1.0.0", pattern=r"^\d+\.\d+\.\d+$")
    grain: str = Field("registered relation grain", min_length=1)
    numerator: str = Field("registered implementation result", min_length=1)
    denominator: str | None = None
    exclusions: list[str] = Field(default_factory=list)
    time_policy: str = Field("selected dataset scope", min_length=1)
    owner: str = Field("dataagent", min_length=1)
    result_column: str = ""
    implementation_id: str = ""

    @computed_field
    @property
    def semantic_hash(self) -> str:
        """Hash the full contract, including the code-owned executable expression.

        SQL expressions stay excluded from public API serialization; a changed
        formula nevertheless changes this fingerprint. This is a definition
        fingerprint, not a checksum of the data or proof of causal validity.
        """
        content = self.model_dump(exclude={"semantic_hash"})
        content["expression"] = self.expression
        # Ordering of permitted dimensions/splits/exclusions has no semantic meaning.
        for key in ("dimensions", "splits", "exclusions"):
            content[key] = sorted(content[key])
        return hashlib.sha256(
            json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()


class DatasetDefinition(Contract):
    dataset_id: str
    name: str
    description: str
    aliases: list[str]
    entity_key: str
    time_kind: Literal["calendar", "relative"]
    fields: list[FieldDefinition]
    relationships: list[dict[str, str]] = Field(default_factory=list)
    capabilities: list[str]
    limitations: list[str]
    dashboard_route: str


class ScaniaFilters(Contract):
    split: Literal["train", "validation", "test"] = "train"
    vehicle_id: int | None = Field(None, ge=0)
    min_time: float | None = Field(None, ge=0)
    max_time: float | None = Field(None, ge=0)
    specifications: dict[str, str] = Field(default_factory=dict, max_length=8)
    class_label: int | None = Field(None, ge=0, le=4)
    in_study_repair: int | None = Field(None, ge=0, le=1)

    @model_validator(mode="after")
    def semantics(self) -> ScaniaFilters:
        if self.min_time is not None and self.max_time is not None and self.min_time > self.max_time:
            raise ValueError("min_time must not exceed max_time")
        if self.split == "train" and self.class_label is not None:
            raise ValueError("train has repair/censoring records, not class_label")
        if self.split != "train" and self.in_study_repair is not None:
            raise ValueError("in_study_repair is only defined for train")
        for key, value in self.specifications.items():
            if key not in {f"Spec_{i}" for i in range(8)} or len(value) > 80:
                raise ValueError("Unknown specification or invalid category")
        return self


class PdmFilters(Contract):
    start_date: date | None = None
    end_date: date | None = None
    machine_id: int | None = Field(None, ge=1)
    model: str | None = Field(None, max_length=80)
    failure_type: str | None = Field(None, max_length=80)

    @model_validator(mode="after")
    def dates(self) -> PdmFilters:
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("start_date must not exceed end_date")
        return self


class BIPlan(Contract):
    version: Literal["1"] = "1"
    dataset_id: str
    intent: Literal[
        "overview",
        "comparison",
        "ranking",
        "trend",
        "distribution",
        "correlation",
        "period_comparison",
        "detail",
    ] = "overview"
    metric_ids: list[str] = Field(min_length=1, max_length=4)
    dimension: str | None = None
    filters: dict[str, Any] = Field(default_factory=dict)
    grain: Literal["day", "week", "month"] = "day"
    bucket_width: float = Field(20, gt=0, le=1000)
    limit: int = Field(20, ge=1, le=500)
    # No code/options accepted here: the existing ChartSpec renderer owns rendering.
    chart_type: Literal["auto", "table", "column", "bar", "line", "pie", "scatter"] = "auto"


class BIRequest(Contract):
    agent_id: int = Field(ge=1)
    question: str = Field(min_length=1, max_length=6000)
    dataset_id: str | None = None
    filters: dict[str, Any] | None = None
    conversation_id: str | None = Field(None, max_length=64)
    plan: BIPlan | None = None
    report: bool = False


class DashboardRequest(Contract):
    filters: dict[str, Any] = Field(default_factory=dict)
    feature: str | None = None
    dimension: str | None = None
