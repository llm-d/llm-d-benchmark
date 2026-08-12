"""Tests for llmdbenchmark.agent rendering behavior.

Validates that:
- the Agent-Rendered Run Command argv matches the pinned canonical ordering
- every run-flag token in argv is a real flag defined in interface/run.py
- --analyze is always present; --monitoring only under the override
- rendering is deterministic (byte-identical argv and manifest dump)
- the Benchmark Job Manifest carries both identities, no ttlSecondsAfterFinished,
  no hostPath/kubeconfig, and args identical to the run command's own argv slice
- Benchmark Secret References render as references only, never raw values
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from llmdbenchmark.agent import (
    ExecutionFacts,
    RecommendationFacts,
    SecretEnvReference,
    SecretFileReference,
    WorkloadIntent,
    WorkspaceVolume,
    recommend,
    render_benchmark_job_manifest,
    render_run_command,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RUN_PY = PROJECT_ROOT / "llmdbenchmark" / "interface" / "run.py"


def _real_run_flags() -> set[str]:
    return set(re.findall(r'"(--[a-z-]+)"', RUN_PY.read_text()))


def _execution_facts(**overrides) -> ExecutionFacts:
    base = dict(
        benchmark_session_id="sess-1",
        endpoint_url="http://endpoint",
        model="a-model",
        namespace="ns",
        benchmark_runner_image="img:tag",
        benchmark_workspace_volume=WorkspaceVolume(claim_name="pvc"),
    )
    base.update(overrides)
    return ExecutionFacts(**base)


def test_argv_pinned_ordering():
    recommendation = recommend(
        RecommendationFacts(workload_intent=WorkloadIntent.INTERACTIVE_CHAT)
    )
    execution_facts = _execution_facts()
    run_command = render_run_command(recommendation, execution_facts)
    assert run_command.argv == [
        "llmdbenchmark",
        "--spec",
        "guides/optimized-baseline",
        "--workspace",
        "/workspace",
        "run",
        "--namespace",
        "ns",
        "--endpoint-url",
        "http://endpoint",
        "--model",
        "a-model",
        "--harness",
        "inference-perf",
        "--workload",
        "chatbot_synthetic.yaml",
        "--analyze",
    ]


def test_run_flags_are_a_subset_of_real_run_py_flags():
    recommendation = recommend(
        RecommendationFacts(workload_intent=WorkloadIntent.BATCH_THROUGHPUT)
    )
    execution_facts = _execution_facts(benchmark_monitoring_override=True)
    run_command = render_run_command(recommendation, execution_facts)

    # argv[5] is "run"; everything before it is root-level (--spec, --workspace).
    run_flags_used = {tok for tok in run_command.argv[6:] if tok.startswith("--")}
    assert run_flags_used <= _real_run_flags()


def test_analyze_always_present_monitoring_only_on_override():
    recommendation = recommend(
        RecommendationFacts(workload_intent=WorkloadIntent.INTERACTIVE_CHAT)
    )

    default_command = render_run_command(recommendation, _execution_facts())
    assert "--analyze" in default_command.argv
    assert "--monitoring" not in default_command.argv
    assert "--config" not in default_command.argv
    assert "--generate-config" not in default_command.argv
    assert default_command.argv.count("--endpoint-url") == 1
    assert default_command.argv.count("--model") == 1

    monitored_command = render_run_command(
        recommendation, _execution_facts(benchmark_monitoring_override=True)
    )
    assert monitored_command.argv.count("--monitoring") == 1


def test_rendering_is_deterministic():
    recommendation = recommend(
        RecommendationFacts(workload_intent=WorkloadIntent.INTERACTIVE_CHAT)
    )
    execution_facts = _execution_facts()
    first = render_run_command(recommendation, execution_facts)
    second = render_run_command(recommendation, execution_facts)
    assert first.argv == second.argv
    assert first.rendered == second.rendered

    manifest_one = render_benchmark_job_manifest(recommendation, execution_facts, first)
    manifest_two = render_benchmark_job_manifest(
        recommendation, execution_facts, second
    )
    dump_one = yaml.safe_dump(manifest_one, sort_keys=True, default_flow_style=False)
    dump_two = yaml.safe_dump(manifest_two, sort_keys=True, default_flow_style=False)
    assert dump_one == dump_two


class TestBenchmarkJobManifest:
    def _manifest(self, **execution_overrides):
        recommendation = recommend(
            RecommendationFacts(workload_intent=WorkloadIntent.INTERACTIVE_CHAT)
        )
        execution_facts = _execution_facts(**execution_overrides)
        run_command = render_run_command(recommendation, execution_facts)
        manifest = render_benchmark_job_manifest(
            recommendation, execution_facts, run_command
        )
        return recommendation, execution_facts, run_command, manifest

    def test_kind_and_api_version(self):
        _, _, _, manifest = self._manifest()
        assert manifest["apiVersion"] == "batch/v1"
        assert manifest["kind"] == "Job"

    def test_name_is_legal_rfc1123(self):
        _, _, _, manifest = self._manifest()
        name = manifest["metadata"]["name"]
        assert len(name) <= 63
        assert re.match(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$", name)

    def test_name_is_legal_rfc1123_at_max_session_id_length(self):
        # The longest benchmark_session_id ExecutionFacts allows must not
        # push metadata.name past the 63-char RFC1123 limit.
        _, _, _, manifest = self._manifest(benchmark_session_id="a" * 47)
        name = manifest["metadata"]["name"]
        assert len(name) == 63
        assert re.match(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$", name)

    def test_both_identities_present_and_distinct(self):
        recommendation, _, _, manifest = self._manifest()
        labels = manifest["metadata"]["labels"]
        session_id = labels["llmdbenchmark.llm-d.ai/benchmark-session-id"]
        recommendation_id = labels["llmdbenchmark.llm-d.ai/recommendation-id"]
        assert session_id == "sess-1"
        assert recommendation_id == recommendation.recommendation_id
        assert session_id != recommendation_id

    def test_workload_intent_slugified_label_verbatim_annotation(self):
        _, _, _, manifest = self._manifest()
        assert (
            manifest["metadata"]["labels"]["llmdbenchmark.llm-d.ai/workload-intent"]
            == "interactive-chat"
        )
        assert (
            manifest["metadata"]["annotations"][
                "llmdbenchmark.llm-d.ai/workload-intent"
            ]
            == "Interactive Chat"
        )

    def test_container_image_and_args_match_run_command_slice(self):
        _, _, run_command, manifest = self._manifest()
        container = manifest["spec"]["template"]["spec"]["containers"][0]
        assert container["image"] == "img:tag"
        assert container["args"] == run_command.argv[1:]

    def test_pvc_volume_mounted_at_mount_path(self):
        _, _, _, manifest = self._manifest()
        pod_spec = manifest["spec"]["template"]["spec"]
        pvc_volumes = [v for v in pod_spec["volumes"] if "persistentVolumeClaim" in v]
        assert len(pvc_volumes) == 1
        assert pvc_volumes[0]["persistentVolumeClaim"]["claimName"] == "pvc"
        mount = pod_spec["containers"][0]["volumeMounts"][0]
        assert mount["mountPath"] == "/workspace"

    def test_service_account_only_when_supplied(self):
        _, _, _, manifest_without = self._manifest()
        assert "serviceAccountName" not in manifest_without["spec"]["template"]["spec"]

        _, _, _, manifest_with = self._manifest(benchmark_runner_auth="runner-sa")
        assert (
            manifest_with["spec"]["template"]["spec"]["serviceAccountName"]
            == "runner-sa"
        )

    def test_no_hostpath_no_kubeconfig_no_ttl(self):
        _, _, _, manifest = self._manifest()
        dumped = yaml.safe_dump(manifest)
        assert "hostPath" not in dumped
        assert "kubeconfig" not in dumped
        assert "ttlSecondsAfterFinished" not in dumped

    def test_secret_references_render_by_reference_only(self):
        sentinel = "sentinel-secret-name-xyz"
        _, _, _, manifest = self._manifest(
            benchmark_secret_references=[
                SecretEnvReference(
                    kind="env", secret_name=sentinel, secret_key="key", env_var="TOKEN"
                ),
                SecretFileReference(
                    kind="file", secret_name="file-secret", mount_path="/etc/creds"
                ),
            ]
        )
        dumped = yaml.safe_dump(manifest)
        assert sentinel in dumped
        container = manifest["spec"]["template"]["spec"]["containers"][0]
        assert container["env"][0]["valueFrom"]["secretKeyRef"]["name"] == sentinel
        secret_volumes = [
            v for v in manifest["spec"]["template"]["spec"]["volumes"] if "secret" in v
        ]
        assert len(secret_volumes) == 1
        assert secret_volumes[0]["secret"]["secretName"] == "file-secret"

    def test_recommendation_output_dump_has_no_secret_material(self):
        recommendation, _, _, _ = self._manifest()
        dumped = yaml.safe_dump(recommendation.model_dump(mode="json"))
        assert "secret" not in dumped.lower()
