"""Agent Core: a leaf package of pure functions that recommend a benchmark
configuration for a Workload Intent, render a run command and Benchmark Job
Manifest for it, and score benchmark-report v0.2 output against
caller-supplied SLO Gates.

This package has no Kubernetes client, no CLI surface, and no runtime
state. Its only repository imports are
``llmdbenchmark.utilities.os.filesystem.resolve_specification_file`` and
``llmdbenchmark.analysis.benchmark_report.import_benchmark_report``.
"""

from llmdbenchmark.agent.facts import (
    AgentRenderedRunCommand,
    BenchmarkSecretReference,
    Diagnostic,
    ExecutionFacts,
    GatePercentile,
    GateResult,
    RecommendationConfidence,
    RecommendationFacts,
    RecommendationOutput,
    RecommendationOverrides,
    SecretEnvReference,
    SecretFileReference,
    SelectedConfiguration,
    SloGate,
    SloGoodput,
    SloGoodputReport,
    SloMetric,
    WorkloadIntent,
    WorkspaceVolume,
)
from llmdbenchmark.agent.recommend import recommend
from llmdbenchmark.agent.render import render_benchmark_job_manifest, render_run_command
from llmdbenchmark.agent.score import discover_agent_analysis_input, score_slo_goodput
from llmdbenchmark.agent.workspace import write_agent_session_workspace

__all__ = [
    "AgentRenderedRunCommand",
    "BenchmarkSecretReference",
    "Diagnostic",
    "ExecutionFacts",
    "GatePercentile",
    "GateResult",
    "RecommendationConfidence",
    "RecommendationFacts",
    "RecommendationOutput",
    "RecommendationOverrides",
    "SecretEnvReference",
    "SecretFileReference",
    "SelectedConfiguration",
    "SloGate",
    "SloGoodput",
    "SloGoodputReport",
    "SloMetric",
    "WorkloadIntent",
    "WorkspaceVolume",
    "discover_agent_analysis_input",
    "recommend",
    "render_benchmark_job_manifest",
    "render_run_command",
    "score_slo_goodput",
    "write_agent_session_workspace",
]
