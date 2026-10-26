from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from contextvars import ContextVar
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

CONFIG_FINGERPRINT = ContextVar("config_fingerprint", default="")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0"] = "1.0"
    id: str
    created_at: str = Field(default_factory=now)
    input_refs: list[str] = Field(default_factory=list)
    config_fingerprint: str = Field(default_factory=lambda: CONFIG_FINGERPRINT.get())


class CandidateSeed(Record):
    domain_id: str
    role_id: str
    scenario_id: str
    work_need_id: str
    business_objects: list[str]
    execution_pattern: str
    compatibility: bool = True
    sampling_basis: str = "marginal coverage"
    status: str = "candidate"


class SourceRecord(Record):
    original_url: str
    final_url: str
    institution: str
    title: str = ""
    source_type: str
    acquired_at: str = Field(default_factory=now)
    status_code: int | None = None
    declared_mime: str = ""
    detected_mime: str = ""
    parsed_format: str = ""
    byte_count: int = 0
    raw_hash: str = ""
    content_hash: str = ""
    local_path: str = ""
    parsed_path: str = ""
    license_status: str = "unknown"
    download_status: str = "pending"
    parse_status: str = "pending"
    purpose: str = "workflow_reference"
    applicable_scope: str = "Reference structure only; not a universal business rule"
    error: str | None = None


class EvidenceRecord(Record):
    source_id: str
    excerpt: str = Field(max_length=1200)
    location: dict[str, Any]
    claim: str
    purpose: str
    extraction_method: str
    source_hash: str


class GroundedClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    evidence_ids: list[str] = Field(default_factory=list)
    basis: Literal["evidence_supported", "synthetic_choice", "unverified"]


class SeedBlueprint(Record):
    candidate_id: str
    dimensions: dict[str, str]
    business_goal: str
    workflow: list[GroundedClaim]
    rules: list[GroundedClaim]
    input_materials: list[str]
    decision_points: list[str]
    file_roles: list[str]
    typical_outputs: list[str]
    variable_parameters: list[str]
    invariants: list[str]
    evidence_ids: list[str]
    source_ids: list[str]
    template_refs: list[str] = Field(default_factory=list)
    evidence_gaps: list[str] = Field(default_factory=list)
    review_status: Literal["candidate", "needs_evidence", "accepted", "rejected"]
    provenance: Literal["fixture", "online"]
    family_key: str


class Fact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fact_id: str
    type: str
    value: Any
    unit: str = ""
    valid_from: str
    valid_to: str | None = None
    input_fact_ids: list[str] = Field(default_factory=list)
    calculation: str | None = None


class WorldInstance(Record):
    seed_id: str
    instance_seed: int
    role: str
    entities: list[dict[str, str]]
    relationships: list[dict[str, str]]
    transactions: list[dict[str, Any]]
    rules: list[dict[str, Any]]
    events: list[dict[str, Any]]
    time_range: list[str]
    facts: list[Fact]
    business_invariants: list[str]
    group_key: str
    synthetic: bool = True

    def ledger(self) -> dict[str, Any]:
        return {f.fact_id: f.value for f in self.facts}


class FileDependency(BaseModel):
    file_id: str
    type: Literal["references", "derived_from", "summarizes", "supersedes"]


class FilePlan(Record):
    relative_path: str
    format: Literal["docx", "xlsx", "pptx", "pdf", "md", "eml"]
    role: str
    author_entity_id: str
    business_date: str
    version_id: str = "v1"
    valid_from: str
    valid_to: str | None = None
    fact_ids: list[str]
    dependencies: list[FileDependency] = Field(default_factory=list)
    structure: dict[str, Any]
    template_ref: str = "builtin/settlement-v1"
    strategy: str = "structured_ir"
    checks: list[str] = Field(default_factory=lambda: ["structure", "facts", "references", "versions"])

    @field_validator("relative_path")
    @classmethod
    def safe_relative_path(cls, value: str) -> str:
        from pathlib import PurePosixPath
        p = PurePosixPath(value)
        if p.is_absolute() or ".." in p.parts or "\\" in value or ":" in value:
            raise ValueError("relative_path must stay inside public/")
        return value


class ValidationResult(Record):
    scope: str
    check_id: str
    severity: Literal["blocking", "warning"] = "blocking"
    status: Literal["pass", "fail", "skipped"]
    expected: Any = None
    observed: Any = None
    location: dict[str, Any] = Field(default_factory=dict)
    failure_class: str | None = None
    repair_hint: str | None = None


class StageStatus(str, Enum):
    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    blocked = "blocked"
    skipped = "skipped"


class StageRecord(Record):
    stage: str
    object_id: str
    input_fingerprint: str
    status: StageStatus
    attempts: int = 0
    started_at: str | None = None
    ended_at: str | None = None
    output_refs: list[str] = Field(default_factory=list)
    provider: str | None = None
    model: str | None = None
    request_counts: dict[str, int] = Field(default_factory=dict)
    tokens: int | None = None
    error_type: str | None = None
    error: str | None = None


def money(value: Any) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"))
