"""Tests for GAIE/EPP cleanup during teardown.

Regression coverage for: teardown reporting success while leaving the
GAIE/EPP Helm release (and its Deployment) behind when the model label
re-derived at teardown time doesn't match what was actually deployed at
standup (e.g. ``--models``/``LLMDBENCH_MODELS`` wasn't re-supplied).

Two independent mechanisms are covered:
- ``UninstallHelmStep._release_matches``: Helm release matching now also
  matches by chart identity on a full-scenario teardown (no --stack filter).
- ``DeleteResourcesStep.MODELSERVICE_PATTERNS``: the namespace-wide safety
  net now also matches "-epp"-suffixed resource names.
"""

from __future__ import annotations

from types import SimpleNamespace

import yaml

from llmdbenchmark.teardown.steps.step_01_uninstall_helm import UninstallHelmStep
from llmdbenchmark.teardown.steps.step_03_delete_resources import (
    MODELSERVICE_PATTERNS,
    DeleteResourcesStep,
)


class TestReleaseMatchesChartFallback:
    """UninstallHelmStep._release_matches chart-identity fallback."""

    def test_model_label_match_still_works_regardless_of_full_teardown(self):
        assert UninstallHelmStep._release_matches(
            "qwen-qwe-04b6bb85-router",
            "llmdbench",
            ["qwen-qwe-04b6bb85"],
            "llm-d-router-gateway-1.2.3",
            full_teardown=False,
        )

    def test_release_name_match_still_works_regardless_of_full_teardown(self):
        assert UninstallHelmStep._release_matches(
            "llmdbench-router",
            "llmdbench",
            [],
            "llm-d-router-gateway-1.2.3",
            full_teardown=False,
        )

    def test_full_teardown_matches_by_chart_identity_despite_label_mismatch(self):
        """The bug: teardown re-rendered with a different model, so neither
        the release name nor model_labels match -- but it's a full-scenario
        teardown, so the release still belongs to us by chart identity."""
        assert UninstallHelmStep._release_matches(
            "qwen-qwe-04b6bb85-en3-0-6b-router",
            "llmdbench",
            ["some-other-model-label"],
            "llm-d-router-gateway-1.2.3",
            full_teardown=True,
        )

    def test_full_teardown_matches_modelservice_chart_too(self):
        assert UninstallHelmStep._release_matches(
            "qwen-qwe-04b6bb85-en3-0-6b-ms",
            "llmdbench",
            ["some-other-model-label"],
            "llm-d-modelservice-0.4.0",
            full_teardown=True,
        )

    def test_partial_stack_teardown_does_not_use_chart_fallback(self):
        """A --stack-filtered teardown must not sweep up sibling stacks'
        releases just because they share a chart -- only a real name/label
        match should count."""
        assert not UninstallHelmStep._release_matches(
            "sibling-model-router",
            "llmdbench",
            ["some-other-model-label"],
            "llm-d-router-gateway-1.2.3",
            full_teardown=False,
        )

    def test_unmanaged_chart_never_matches_on_label_mismatch(self):
        assert not UninstallHelmStep._release_matches(
            "unrelated-release",
            "llmdbench",
            ["some-other-model-label"],
            "some-unrelated-chart-1.0.0",
            full_teardown=True,
        )


class TestModelservicePatternsCatchesEppDeployment:
    """DeleteResourcesStep's namespace-wide safety net for the EPP Deployment."""

    def test_router_epp_deployment_name_matches(self):
        assert DeleteResourcesStep._matches_any(
            "deployment/qwen-qwe-04b6bb85-en3-0-6b-router-epp",
            MODELSERVICE_PATTERNS,
        )

    def test_legacy_gaie_epp_deployment_name_matches(self):
        assert DeleteResourcesStep._matches_any(
            "deployment/qwen-qwe-04b6bb85-en3-0-6b-gaie-epp",
            MODELSERVICE_PATTERNS,
        )


def test_teardown_collects_gateway_namespace(tmp_path):
    stack_path = tmp_path / "stack"
    stack_path.mkdir()
    (stack_path / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "namespace": {"name": "benchmark"},
                "gateway": {"className": "none", "namespace": "model-serving"},
                "harness": {"namespace": "harness"},
            }
        ),
        encoding="utf-8",
    )
    context = SimpleNamespace(
        rendered_stacks=[stack_path],
        namespace="benchmark",
        harness_namespace="harness",
    )

    assert UninstallHelmStep()._all_target_namespaces(context) == [
        "benchmark",
        "harness",
        "model-serving",
    ]
