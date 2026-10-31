"""Typed run outputs: one subpackage per artifact kind."""

from .base import (
    Artifact,
    ArtifactHeader,
    Producer,
    Run,
    RunTime,
    ValidationIssue,
)
from .registry import (
    LEGACY_BENCHMARK_REPORT_VERSIONS,
    artifact_model,
    load_artifact,
    register,
)

__all__ = [
    "LEGACY_BENCHMARK_REPORT_VERSIONS",
    "Artifact",
    "ArtifactHeader",
    "Producer",
    "Run",
    "RunTime",
    "ValidationIssue",
    "artifact_model",
    "load_artifact",
    "register",
]
