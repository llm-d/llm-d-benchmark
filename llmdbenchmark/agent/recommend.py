"""The Recommendation Map loader, ``recommend()``, and Agent Static
Validation.

``recommend()`` intentionally takes no Execution Facts parameter -- endpoint
details cannot influence Recommendation Map selection (see
``llmdbenchmark.agent.facts.ExecutionFacts``).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

from llmdbenchmark.agent.facts import (
    Diagnostic,
    GatePercentile,
    RecommendationConfidence,
    RecommendationFacts,
    RecommendationOutput,
    RecommendationOverrides,
    SelectedConfiguration,
)
from llmdbenchmark.utilities.os.filesystem import resolve_specification_file

_DEFAULT_MAP_PATH = Path(__file__).resolve().parent / "recommendation_map.yaml"
# llmdbenchmark/agent/recommend.py -> parents[2] = repository root, matching
# the package-relative assumption step_05_render_profiles.py:65 already makes.
_DEFAULT_BASE_DIR = Path(__file__).resolve().parents[2]


class RecommendationMap:
    """A loaded, validated Recommendation Map."""

    def __init__(self, version: str, rules: list[dict]):
        self.version = version
        self.rules = rules

    @classmethod
    def load(cls, map_path: Path | None = None) -> "RecommendationMap":
        path = map_path or _DEFAULT_MAP_PATH
        with open(path, "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
        return cls(version=data["recommendation_map_version"], rules=data["rules"])

    def find(self, facts: RecommendationFacts) -> dict | None:
        for rule in self.rules:
            if (
                rule["workload_intent"] == str(facts.workload_intent)
                and rule.get("prefix_reuse", False) == facts.prefix_reuse
            ):
                return rule
        return None


def _recommendation_id(
    facts: RecommendationFacts,
    overrides: RecommendationOverrides,
    map_version: str,
) -> str:
    payload = {
        "recommendation_map_version": map_version,
        "recommendation_facts": facts.model_dump(mode="json", exclude_none=True),
        "recommendation_overrides": overrides.model_dump(
            mode="json", exclude_none=True
        ),
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return f"rec-{digest[:12]}"


def _profile_exists(profiles_dir: Path, profile: str) -> bool:
    """The <name>-then-<name>.in rule from
    step_05_render_profiles.py:260-280 (``_resolve_source_file``)."""
    return (profiles_dir / profile).exists() or (
        profiles_dir / f"{profile}.in"
    ).exists()


def _validate(
    rule: dict,
    base_dir: Path,
) -> tuple[list[Diagnostic], str, bool]:
    """Agent Static Validation: local filesystem only, zero Kubernetes
    calls. Returns diagnostics, the selected workload profile (after any
    Batch Throughput fallback), and whether the fallback fired."""
    diagnostics: list[Diagnostic] = []

    specification = rule["specification"]
    try:
        resolve_specification_file(specification, base_dir=base_dir)
    except (FileNotFoundError, ValueError) as exc:
        diagnostics.append(
            Diagnostic(
                severity="error",
                code="specification_not_found",
                message=str(exc),
                subject=specification,
            )
        )

    harness = rule["harness"]
    harness_dir = base_dir / "workload" / "profiles" / harness
    if not harness_dir.is_dir():
        diagnostics.append(
            Diagnostic(
                severity="error",
                code="harness_not_found",
                message=f"Harness directory not found: {harness_dir}",
                subject=harness,
            )
        )

    profile = rule["workload_profile"]
    fallback_profile = rule.get("fallback_workload_profile")
    fallback_used = False
    if harness_dir.is_dir() and not _profile_exists(harness_dir, profile):
        if fallback_profile and _profile_exists(harness_dir, fallback_profile):
            diagnostics.append(
                Diagnostic(
                    severity="warning",
                    code="workload_profile_fallback",
                    message=(
                        f"Workload profile '{profile}' not found under "
                        f"{harness_dir}; falling back to '{fallback_profile}'."
                    ),
                    subject=profile,
                )
            )
            profile = fallback_profile
            fallback_used = True
        else:
            diagnostics.append(
                Diagnostic(
                    severity="error",
                    code="workload_profile_not_found",
                    message=(
                        f"Workload profile '{profile}' not found under {harness_dir}."
                    ),
                    subject=profile,
                )
            )

    return diagnostics, profile, fallback_used


def recommend(
    facts: RecommendationFacts,
    overrides: RecommendationOverrides | None = None,
    *,
    map_path: Path | None = None,
    base_dir: Path | None = None,
) -> RecommendationOutput:
    """Select a Recommendation Map row for the given Structured
    Recommendation Facts, apply any Recommendation Overrides, run Agent
    Static Validation against local repository assets, and return a
    Recommendation Output."""
    overrides = overrides or RecommendationOverrides()
    resolved_base_dir = base_dir or _DEFAULT_BASE_DIR

    recommendation_map = RecommendationMap.load(map_path)
    rule = recommendation_map.find(facts)
    if rule is None:
        raise ValueError(
            f"No Recommendation Map rule for workload_intent="
            f"{facts.workload_intent!r}, prefix_reuse={facts.prefix_reuse!r}"
        )

    diagnostics, profile, fallback_used = _validate(rule, resolved_base_dir)

    confidence = RecommendationConfidence(rule["recommendation_confidence"])
    degraded = False

    specification = overrides.specification
    if specification is None:
        specification = rule["specification"]
    else:
        degraded = True

    harness = overrides.harness
    if harness is None:
        harness = rule["harness"]
    else:
        degraded = True

    if overrides.workload_profile is not None:
        profile = overrides.workload_profile
        degraded = True

    slo_gate_percentile = overrides.slo_gate_percentile or GatePercentile.P95
    if overrides.slo_gate_percentile is not None:
        degraded = True

    if fallback_used:
        confidence = RecommendationConfidence.LOW
    elif degraded and confidence == RecommendationConfidence.HIGH:
        confidence = RecommendationConfidence.MEDIUM
    elif degraded and confidence == RecommendationConfidence.MEDIUM:
        confidence = RecommendationConfidence.LOW

    recommendation_id = _recommendation_id(facts, overrides, recommendation_map.version)

    return RecommendationOutput(
        recommendation_id=recommendation_id,
        recommendation_map_version=recommendation_map.version,
        workload_intent=facts.workload_intent,
        selected=SelectedConfiguration(
            specification=specification,
            harness=harness,
            workload_profile=profile,
            slo_gate_percentile=slo_gate_percentile,
        ),
        rationale=rule.get("rationale", ""),
        recommendation_confidence=confidence,
        expected_artifacts=[
            "<mount_path>/latest/results/*/benchmark_report_v0.2,_*.yaml"
        ],
        diagnostics=diagnostics,
    )
