"""Pydantic response / request models."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class ErrorBody(BaseModel):
    error: dict


class ConceptRef(BaseModel):
    prefix: str
    local: str
    qname: str


class Equation(BaseModel):
    id: str
    role: str
    summation: str
    items: list[dict]  # [{"concept": qname, "weight": 1|-1}]
    coefficients: dict[str, int]
    concept_count: int


class PackageRevisionOut(BaseModel):
    package_id: str
    revision: int
    concepts: list[str]
    equations: list[Equation]
    created_at: str


class PackageOut(BaseModel):
    package_id: str
    name: str
    latest_revision: int
    created_at: str
    updated_at: str
    latest: Optional[PackageRevisionOut] = None


class Fact(BaseModel):
    concept: str
    value: float
    decimals: object  # int or the string "INF"
    lower: float
    upper: float


class InstanceRevisionOut(BaseModel):
    instance_id: str
    package_id: str
    revision: int
    entity: str
    period: str
    currency: str
    facts: list[Fact]
    created_at: str


class InstanceOut(BaseModel):
    instance_id: str
    package_id: str
    latest_revision: int
    created_at: str
    updated_at: str
    latest: Optional[InstanceRevisionOut] = None


class WitnessFact(BaseModel):
    concept: str
    reported_value: float
    decimals: object
    true_value: float
    lower: float
    upper: float


class Conflict(BaseModel):
    equation: Equation
    residual_at_witness: Optional[float] = None
    facts: list[WitnessFact]


class ReportOut(BaseModel):
    report_id: str
    instance_id: str
    package_id: str
    instance_revision: int
    package_revision: int
    status: Literal["feasible", "infeasible", "pending"]
    is_correction: bool
    superseded: bool
    late: bool
    witness: Optional[list[WitnessFact]] = None
    conflicts: Optional[list[Conflict]] = None
    missing_facts: Optional[list[str]] = None
    detail: dict
    created_at: str
    has_instance_snapshot: bool = True
    has_xsd_snapshot: bool = True
    has_calc_snapshot: bool = True


class CheckAck(BaseModel):
    report: ReportOut
    superseded_report_ids: list[str] = Field(default_factory=list)


class ReportList(BaseModel):
    instance_id: str
    current: Optional[ReportOut] = None
    history: list[ReportOut]
