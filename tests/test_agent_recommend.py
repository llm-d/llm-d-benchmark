"""Tests for llmdbenchmark.agent recommendation behavior.

Validates that:
- each Workload Intent maps to the expected specification/harness/profile
- every Recommendation Map rule's assets actually resolve on disk (the
  regression that would have caught the inference-scheduling rename)
- every rule's harness is scorable (present in analysis._WRITER_NAMES)
- Recommendation ID is deterministic and changes with overrides / map version
- recommend() takes no ExecutionFacts parameter (story 40, by signature)
- ExecutionFacts validation rejects missing/invalid fields
- a raw credential in a Benchmark Secret Reference raises ValidationError
- the Batch Throughput fallback fires when the primary profile is absent
- importing the package pulls in neither planner nor kubernetes
"""

from __future__ import annotations

import inspect
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from llmdbenchmark.agent import (
    ExecutionFacts,
    RecommendationFacts,
    RecommendationOverrides,
    SecretEnvReference,
    WorkloadIntent,
    WorkspaceVolume,
    recommend,
)
from llmdbenchmark.agent.recommend import RecommendationMap, _recommendation_id
from llmdbenchmark.analysis import _WRITER_NAMES
from llmdbenchmark.utilities.os.filesystem import resolve_specification_file

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_interactive_chat_maps_to_chatbot_profile():
    result = recommend(
        RecommendationFacts(workload_intent=WorkloadIntent.INTERACTIVE_CHAT)
    )
    assert result.selected.specification == "guides/optimized-baseline"
    assert result.selected.harness == "inference-perf"
    assert result.selected.workload_profile == "chatbot_synthetic.yaml"
    assert result.diagnostics == []


def test_interactive_chat_with_prefix_reuse_maps_to_shared_prefix_profile():
    result = recommend(
        RecommendationFacts(
            workload_intent=WorkloadIntent.INTERACTIVE_CHAT, prefix_reuse=True
        )
    )
    assert result.selected.workload_profile == "shared_prefix_synthetic.yaml"


def test_long_context_generation_maps_to_pd_disaggregation():
    result = recommend(
        RecommendationFacts(workload_intent=WorkloadIntent.LONG_CONTEXT_GENERATION)
    )
    assert result.selected.specification == "guides/pd-disaggregation"
    assert result.selected.harness == "inference-perf"
    assert result.selected.workload_profile == "summarization_synthetic.yaml"


def test_batch_throughput_maps_to_guidellm():
    result = recommend(
        RecommendationFacts(workload_intent=WorkloadIntent.BATCH_THROUGHPUT)
    )
    assert result.selected.harness == "guidellm"
    assert result.selected.workload_profile == "summarization_synthetic.yaml"


class TestMapIntegrity:
    """Every Recommendation Map rule's assets must resolve against the real
    repository tree, not just against a fixture."""

    map_data = RecommendationMap.load()

    def test_every_specification_resolves(self):
        for rule in self.map_data.rules:
            resolve_specification_file(rule["specification"], base_dir=PROJECT_ROOT)

    def test_every_profile_resolves(self):
        for rule in self.map_data.rules:
            harness_dir = PROJECT_ROOT / "workload" / "profiles" / rule["harness"]
            profile = rule["workload_profile"]
            assert (harness_dir / profile).exists() or (
                harness_dir / f"{profile}.in"
            ).exists(), f"{rule['harness']}/{profile} not found"

    def test_fallback_profile_resolves(self):
        for rule in self.map_data.rules:
            fallback = rule.get("fallback_workload_profile")
            if fallback is None:
                continue
            harness_dir = PROJECT_ROOT / "workload" / "profiles" / rule["harness"]
            assert (harness_dir / fallback).exists() or (
                harness_dir / f"{fallback}.in"
            ).exists(), f"{rule['harness']}/{fallback} not found"

    def test_every_harness_is_scorable(self):
        for rule in self.map_data.rules:
            assert rule["harness"] in _WRITER_NAMES


class TestRecommendationId:
    def test_stable_across_repeated_calls(self):
        facts = RecommendationFacts(workload_intent=WorkloadIntent.INTERACTIVE_CHAT)
        a = recommend(facts)
        b = recommend(facts)
        assert a.recommendation_id == b.recommendation_id

    def test_changes_with_override(self):
        facts = RecommendationFacts(workload_intent=WorkloadIntent.INTERACTIVE_CHAT)
        a = recommend(facts)
        b = recommend(facts, RecommendationOverrides(harness="guidellm"))
        assert a.recommendation_id != b.recommendation_id

    def test_changes_with_map_version(self):
        facts = RecommendationFacts(workload_intent=WorkloadIntent.INTERACTIVE_CHAT)
        overrides = RecommendationOverrides()
        one = _recommendation_id(facts, overrides, "0.1.0")
        two = _recommendation_id(facts, overrides, "0.2.0")
        assert one != two

    def test_recommend_has_no_execution_facts_parameter(self):
        """Story 40: recommend()'s signature must never gain an
        ExecutionFacts parameter, so endpoint details cannot influence
        Recommendation Map selection."""
        params = inspect.signature(recommend).parameters
        assert "execution_facts" not in params
        assert set(params) == {"facts", "overrides", "map_path", "base_dir"}


class TestExecutionFactsValidation:
    def _kwargs(self, **overrides):
        base = dict(
            benchmark_session_id="sess-1",
            endpoint_url="http://endpoint",
            model="a-model",
            namespace="ns",
            benchmark_runner_image="img:tag",
            benchmark_workspace_volume=WorkspaceVolume(claim_name="pvc"),
        )
        base.update(overrides)
        return base

    def test_missing_endpoint_url_raises(self):
        kwargs = self._kwargs()
        del kwargs["endpoint_url"]
        with pytest.raises(ValidationError):
            ExecutionFacts(**kwargs)

    def test_missing_model_raises(self):
        kwargs = self._kwargs()
        del kwargs["model"]
        with pytest.raises(ValidationError):
            ExecutionFacts(**kwargs)

    def test_missing_namespace_raises(self):
        kwargs = self._kwargs()
        del kwargs["namespace"]
        with pytest.raises(ValidationError):
            ExecutionFacts(**kwargs)

    def test_missing_runner_image_raises(self):
        kwargs = self._kwargs()
        del kwargs["benchmark_runner_image"]
        with pytest.raises(ValidationError):
            ExecutionFacts(**kwargs)

    def test_unknown_key_raises(self):
        with pytest.raises(ValidationError):
            ExecutionFacts(**self._kwargs(bogus_field="x"))

    def test_uppercase_session_id_raises(self):
        with pytest.raises(ValidationError):
            ExecutionFacts(**self._kwargs(benchmark_session_id="ABC"))

    def test_52_char_session_id_raises(self):
        with pytest.raises(ValidationError):
            ExecutionFacts(**self._kwargs(benchmark_session_id="a" * 52))

    def test_47_char_session_id_allowed_and_yields_legal_job_name(self):
        # 16-char "llmdbench-agent-" prefix + 47 == 63, the RFC1123 cap.
        facts = ExecutionFacts(**self._kwargs(benchmark_session_id="a" * 47))
        assert len(f"llmdbench-agent-{facts.benchmark_session_id}") == 63

    def test_48_char_session_id_raises(self):
        with pytest.raises(ValidationError):
            ExecutionFacts(**self._kwargs(benchmark_session_id="a" * 48))


def test_secret_reference_with_raw_value_raises():
    with pytest.raises(ValidationError):
        SecretEnvReference(
            kind="env",
            secret_name="s",
            secret_key="k",
            env_var="E",
            value="do-not-store-me",
        )


def test_batch_throughput_falls_back_when_primary_profile_missing(tmp_path):
    # Mirror the repo layout minus the Batch Throughput primary profile.
    guidellm_dir = tmp_path / "workload" / "profiles" / "guidellm"
    guidellm_dir.mkdir(parents=True)
    (guidellm_dir / "sanity_concurrent.yaml.in").write_text("profile: concurrent\n")
    spec_dir = tmp_path / "config" / "specification" / "guides"
    spec_dir.mkdir(parents=True)
    (spec_dir / "optimized-baseline.yaml.j2").write_text("base_dir: {}\n")

    result = recommend(
        RecommendationFacts(workload_intent=WorkloadIntent.BATCH_THROUGHPUT),
        base_dir=tmp_path,
    )
    assert result.selected.workload_profile == "sanity_concurrent.yaml"
    assert any(d.code == "workload_profile_fallback" for d in result.diagnostics)
    assert result.recommendation_confidence == "low"


def test_prefix_reuse_on_an_intent_without_a_dedicated_row_falls_back():
    """Long-Context Generation and Batch Throughput have no
    prefix_reuse=true row; setting prefix_reuse=True globally must not
    crash the sole entry point on an otherwise-legal input."""
    result = recommend(
        RecommendationFacts(
            workload_intent=WorkloadIntent.LONG_CONTEXT_GENERATION, prefix_reuse=True
        )
    )
    assert result.selected.workload_profile == "summarization_synthetic.yaml"
    assert any(d.code == "prefix_reuse_not_supported" for d in result.diagnostics)


def test_bogus_overrides_are_validated_not_bypassed():
    """Agent Static Validation must check the post-override selection, not
    the raw Recommendation Map row: a bogus override must surface as a
    diagnostic, never as an empty diagnostics list."""
    result = recommend(
        RecommendationFacts(workload_intent=WorkloadIntent.INTERACTIVE_CHAT),
        RecommendationOverrides(
            specification="does/not-exist",
            harness="nope",
            workload_profile="ghost.yaml",
        ),
    )
    codes = {d.code for d in result.diagnostics}
    assert "specification_not_found" in codes
    assert "harness_not_found" in codes
    assert result.selected.specification == "does/not-exist"
    assert result.selected.harness == "nope"


def test_importing_agent_leaves_no_planner_or_kubernetes_in_sys_modules():
    """A process-global ``sys.modules`` assertion cannot run in-process:
    pytest collection imports every test module up front, and sibling
    modules (test_smoketest_inference.py, test_cluster_resource_resolver.py)
    install a ``planner`` stub / import ``kubernetes`` before this test
    body ever runs. Check it in a fresh subprocess instead."""
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import llmdbenchmark.agent; "
            "assert 'planner' not in sys.modules; "
            "assert 'kubernetes' not in sys.modules",
        ],
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
