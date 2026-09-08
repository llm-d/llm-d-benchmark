"""Pydantic models for the Agent Core: Structured Recommendation Facts,
Execution Facts, and the artifacts the Agent Core reads and writes.

Every model here is pure data -- there is no runtime state in this package,
so everything is pydantic rather than a dataclass. Input models (facts,
overrides, execution facts) are ``extra="forbid"`` so a typo is a
``ValidationError`` rather than a silently-ignored key. Artifact-reader
models are ``extra="ignore"`` so a later slice can add a field without
breaking an older reader.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Discriminator, Field, field_validator

from llmdbenchmark.analysis.benchmark_report.base import UNITS_GEN_LATENCY, UNITS_TIME

# The only units score.py's normalization table (score.py:_SECONDS_PER_UNIT,
# _SECONDS_PER_TOKEN_PER_UNIT) knows how to compare against an observed
# aggregate value.
_SLO_GATE_UNITS = {str(u) for u in (*UNITS_TIME, *UNITS_GEN_LATENCY)}

# Mirrors llmdbenchmark/parser/config_schema.py:29-33 STRICT_CONFIG.
STRICT = ConfigDict(
    extra="forbid",
    validate_assignment=True,
    str_strip_whitespace=True,
)

# For artifacts this package writes and later reads back: unknown fields
# from a newer writer must not break an older reader.
READER = ConfigDict(
    extra="ignore",
    str_strip_whitespace=True,
)

# 47 chars max so "llmdbench-agent-<id>" (16 chars of prefix, see
# render.py's render_benchmark_job_manifest) stays inside the 63-char
# RFC1123 limit: 16 + 47 = 63.
_SESSION_ID_PATTERN = r"^[a-z0-9]([-a-z0-9]{0,45}[a-z0-9])?$"


###############################################################################
# Workload Intents and gate percentile
###############################################################################


class WorkloadIntent(StrEnum):
    """The three PRD Workload Intents a Recommendation Map row can select on."""

    INTERACTIVE_CHAT = "Interactive Chat"
    LONG_CONTEXT_GENERATION = "Long-Context Generation"
    BATCH_THROUGHPUT = "Batch Throughput"


class GatePercentile(StrEnum):
    """The SLO Gate Percentile a caller (or an override) may select."""

    P95 = "p95"
    P99 = "p99"


class RecommendationConfidence(StrEnum):
    """How well a Recommendation Map row fits the request.

    This describes Recommendation Map fit only. It is not an estimate of the
    probability that a run will pass its SLO Gates.
    """

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class SloMetric(StrEnum):
    """The benchmark-report v0.2 aggregate latency leaf names an SLO Gate
    may reference. ``time_per_output_token`` and ``inter_token_latency`` are
    kept as separate members because the schema documents that they are not
    interchangeable across tools (schema_v0_2.py:319-333)."""

    TIME_TO_FIRST_TOKEN = "time_to_first_token"
    INTER_TOKEN_LATENCY = "inter_token_latency"
    TIME_PER_OUTPUT_TOKEN = "time_per_output_token"
    NORMALIZED_TIME_PER_OUTPUT_TOKEN = "normalized_time_per_output_token"
    REQUEST_LATENCY = "request_latency"


###############################################################################
# Structured Recommendation Facts / Recommendation Overrides
###############################################################################


class RecommendationFacts(BaseModel):
    """Structured Recommendation Facts: the inputs that select a
    Recommendation Map row. Deliberately minimal -- Execution Facts are a
    separate model so that endpoint details can never influence mapping."""

    model_config = STRICT

    workload_intent: WorkloadIntent
    prefix_reuse: bool = False


class RecommendationOverrides(BaseModel):
    """Caller overrides to the mapped Recommendation Output fields."""

    model_config = STRICT

    specification: str | None = None
    harness: str | None = None
    workload_profile: str | None = None
    slo_gate_percentile: GatePercentile | None = None


###############################################################################
# Execution Facts
###############################################################################


class WorkspaceVolume(BaseModel):
    """The Benchmark Workspace Volume: a PVC claim name and mount path."""

    model_config = STRICT

    claim_name: str
    mount_path: str = "/workspace"


class SecretEnvReference(BaseModel):
    """A Benchmark Secret Reference materialized as a container env var."""

    model_config = STRICT

    kind: Literal["env"]
    secret_name: str
    secret_key: str
    env_var: str


class SecretFileReference(BaseModel):
    """A Benchmark Secret Reference materialized as a mounted file."""

    model_config = STRICT

    kind: Literal["file"]
    secret_name: str
    mount_path: str


# No field on either member can hold a secret value, so a raw credential
# raises ValidationError at construction rather than needing to be stripped
# from every downstream output.
BenchmarkSecretReference = Annotated[
    Union[SecretEnvReference, SecretFileReference],
    Discriminator("kind"),
]


class ExecutionFacts(BaseModel):
    """Execution Facts: everything needed to render a run command and a
    Benchmark Job Manifest. Kept separate from Recommendation Facts so that
    endpoint details cannot influence Recommendation Map selection."""

    model_config = STRICT

    benchmark_session_id: str = Field(pattern=_SESSION_ID_PATTERN)
    endpoint_url: str
    model: str
    namespace: str
    # Required, no default: no harness image is documented to contain the
    # llmdbenchmark CLI.
    benchmark_runner_image: str
    benchmark_workspace_volume: WorkspaceVolume
    # None means the namespace default service account.
    benchmark_runner_auth: str | None = None
    benchmark_secret_references: list[BenchmarkSecretReference] = Field(
        default_factory=list
    )
    benchmark_monitoring_override: bool = False


###############################################################################
# Diagnostics
###############################################################################


class Diagnostic(BaseModel):
    """A non-fatal finding from Agent Static Validation or SLO scoring."""

    model_config = STRICT

    severity: Literal["error", "warning", "info"]
    code: str
    message: str
    subject: str


###############################################################################
# Recommendation Output
###############################################################################


class SelectedConfiguration(BaseModel):
    """The specification/harness/profile/percentile a recommendation selected."""

    model_config = READER

    specification: str
    harness: str
    workload_profile: str
    slo_gate_percentile: GatePercentile


class RecommendationOutput(BaseModel):
    """The result of ``recommend()``: a Recommendation Map row applied to
    Structured Recommendation Facts, with any Recommendation Overrides
    folded in and Agent Static Validation diagnostics attached."""

    model_config = READER

    agent_artifact_version: Literal["1"] = "1"
    recommendation_id: str
    recommendation_map_version: str
    workload_intent: WorkloadIntent
    selected: SelectedConfiguration
    rationale: str
    recommendation_confidence: RecommendationConfidence
    expected_artifacts: list[str]
    diagnostics: list[Diagnostic]


###############################################################################
# Agent-Rendered Run Command
###############################################################################


class AgentRenderedRunCommand(BaseModel):
    """The Agent-Rendered Run Command: an argv list plus its deterministic,
    copy-pasteable rendering."""

    model_config = READER

    argv: list[str]
    rendered: str


###############################################################################
# SLO Goodput
###############################################################################


class SloGate(BaseModel):
    """A caller-supplied SLO Gate. No defaults: no threshold value exists
    anywhere in this repository, and the PRD names neither a metric set nor
    a number."""

    model_config = STRICT

    metric: SloMetric
    threshold: float
    units: str

    @field_validator("units")
    @classmethod
    def _units_must_be_scorable(cls, value: str) -> str:
        if value not in _SLO_GATE_UNITS:
            raise ValueError(f"units {value!r} is not one of {sorted(_SLO_GATE_UNITS)}")
        return value


class GateResult(BaseModel):
    """The outcome of a single SLO Gate against a single report."""

    model_config = READER

    metric: SloMetric
    threshold: float
    threshold_units: str
    observed: float | None
    observed_units: str | None
    passed: bool | None


class SloGoodputReport(BaseModel):
    """SLO Goodput scoring for a single benchmark-report v0.2 file."""

    model_config = READER

    report_path: str
    stage: int | None
    rate_qps: float | None
    concurrency: Any | None
    verdict: Literal["pass", "fail", "indeterminate"]
    gate_results: list[GateResult]
    output_token_rate_mean: float | None
    request_rate_mean: float | None


class SloGoodput(BaseModel):
    """SLO Goodput: gate-at-percentile plus the passing reports' throughput.
    This is not per-request SLO attainment -- benchmark-report v0.2 stores
    aggregates only."""

    model_config = READER

    agent_artifact_version: Literal["1"] = "1"
    slo_gate_percentile: GatePercentile
    reports: list[SloGoodputReport]
    slo_scoring_diagnostics: list[Diagnostic]
    slo_goodput_output_token_rate: float | None
