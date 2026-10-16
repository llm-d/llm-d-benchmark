"""Autoscaling dry-run (eppKedaSaturation.dryRun).

The ScaledObject keeps its triggers and behavior but targets a shadow
Deployment of scheduling-gated pods, so KEDA computes replica decisions that
are never actuated on the model servers. Verifies:

- every guide scenario renders one gated shadow per scale target, and points
  the ScaledObject at it;
- dryRun=false renders exactly what it did before;
- the decode count is not treated as autoscaler-owned under dry-run;
- turning dry-run off, or tearing down, never leaves a shadow behind;
- the harness replica time series records the shadow's decisions.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import yaml

from llmdbenchmark.parser.cluster_resource_resolver import ClusterResourceResolver
from llmdbenchmark.parser.render_plans import RenderPlans
from llmdbenchmark.parser.version_resolver import VersionResolver
from llmdbenchmark.standup.steps.step_08_deploy_modelservice import (
    DeployModelserviceStep,
    decode_autoscaled,
)
from llmdbenchmark.teardown.steps.step_03_delete_resources import DeleteResourcesStep

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = PROJECT_ROOT / "config" / "templates" / "jinja"
DEFAULTS = PROJECT_ROOT / "config" / "templates" / "values" / "defaults.yaml"
GUIDES = PROJECT_ROOT / "config" / "scenarios" / "guides"
COLLECT_METRICS = PROJECT_ROOT / "workload" / "harnesses" / "collect_metrics.sh"

GATE = "llm-d.ai/autoscaling-dry-run"

# Every scenario that drives autoscaling through eppKedaSaturation, and the
# roles it scales.
KEDA_GUIDES = {
    "epp-keda-saturation": ["decode"],
    "keda-epp-token-aware": ["decode"],
    "keda-epp-token-aware-pd-disaggregation": ["prefill", "decode"],
}


def _render(tmp_path: Path, guide: str, dry_run: bool | None) -> list[dict]:
    logger = MagicMock()
    overrides = {} if dry_run is None else {"eppKedaSaturation": {"dryRun": dry_run}}
    errors = RenderPlans(
        template_dir=TEMPLATES,
        defaults_file=DEFAULTS,
        scenarios_file=GUIDES / f"{guide}.yaml",
        output_dir=tmp_path / "out",
        logger=logger,
        setup_overrides=overrides,
        version_resolver=VersionResolver(logger=logger, dry_run=True),
        cluster_resource_resolver=ClusterResourceResolver(logger=logger, dry_run=True),
    ).eval()
    assert not errors.has_errors, errors.to_dict()
    [rendered] = list((tmp_path / "out").rglob("30_keda-scaledobject*.yaml"))
    return [d for d in yaml.safe_load_all(rendered.read_text()) if d]


def _by_kind(docs: list[dict], kind: str) -> list[dict]:
    return [d for d in docs if d["kind"] == kind]


@pytest.mark.parametrize("guide, roles", KEDA_GUIDES.items())
def test_dry_run_scales_a_gated_shadow_per_target(tmp_path, guide, roles):
    live = _render(tmp_path / "live", guide, dry_run=None)
    docs = _render(tmp_path / "dry", guide, dry_run=True)

    shadows = {d["metadata"]["name"]: d for d in _by_kind(docs, "Deployment")}
    scaled_objects = _by_kind(docs, "ScaledObject")
    assert len(scaled_objects) == len(roles) == len(shadows)

    real_targets = [
        so["spec"]["scaleTargetRef"]["name"] for so in _by_kind(live, "ScaledObject")
    ]
    for so, real in zip(scaled_objects, real_targets):
        ref = so["spec"]["scaleTargetRef"]
        assert ref == {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "name": f"{real}-dryrun",
        }
        assert so["metadata"]["annotations"][f"{GATE}-target"] == f"Deployment/{real}"

        shadow = shadows[ref["name"]]
        pod = shadow["spec"]["template"]
        assert pod["spec"]["schedulingGates"] == [{"name": GATE}]
        labels = pod["metadata"]["labels"]
        # Never selected by the InferencePool, PodMonitor, or the decode pod wait.
        assert "llm-d.ai/inferenceServing" not in labels
        assert labels["llm-d.ai/role"].endswith("-dryrun")
        # Its own selector, so its HPA cannot collide with a live one.
        assert shadow["spec"]["selector"]["matchLabels"] == {f"{GATE}-of": real}
        # Per-model label, so turning dryRun off removes only this stack's shadow.
        assert (
            shadow["metadata"]["labels"][f"{GATE}-model"]
            == pod["metadata"]["labels"]["llm-d.ai/model"]
        )
        assert shadow["spec"]["replicas"] >= so["spec"]["minReplicaCount"]

    # Triggers, thresholds and behavior are untouched: only the target moved.
    for so_live, so_dry in zip(_by_kind(live, "ScaledObject"), scaled_objects):
        assert so_dry["spec"]["triggers"] == so_live["spec"]["triggers"]
        assert so_dry["spec"].get("advanced") == so_live["spec"].get("advanced")


@pytest.mark.parametrize("guide", KEDA_GUIDES)
def test_dry_run_false_renders_the_live_autoscaler(tmp_path, guide):
    assert _render(tmp_path / "a", guide, dry_run=False) == _render(
        tmp_path / "b", guide, dry_run=None
    )
    assert not _by_kind(_render(tmp_path / "c", guide, dry_run=None), "Deployment")


@pytest.mark.parametrize(
    "config, autoscaled",
    [
        ({"eppKedaSaturation": {"enabled": True, "dryRun": True}}, False),
        ({"eppKedaSaturation": {"enabled": True, "dryRun": False}}, True),
    ],
)
def test_a_dry_run_leaves_the_decode_count_to_the_scenario(config, autoscaled):
    assert decode_autoscaled(config) is autoscaled


@dataclass
class _Result:
    success: bool = True
    stdout: str = ""
    stderr: str = ""


@dataclass
class _Cmd:
    """Records kube calls; answers the teardown workload query with *workloads*."""

    workloads: list[dict] = field(default_factory=list)
    calls: list[tuple[str, ...]] = field(default_factory=list)

    def kube(self, *args: str, **_: Any) -> _Result:
        self.calls.append(args)
        if args[0] == "get" and "json" in args and "deployment" in args[1]:
            return _Result(stdout=json.dumps({"items": self.workloads}))
        return _Result()

    def deleted(self) -> list[str]:
        return [c[-1] for c in self.calls if c[0] == "delete" and "-l" not in c]


_LIVE = {
    "eppKedaSaturation": {"enabled": True},
    "namespace": {"name": "bench"},
    "model_id_label": "m",
}


def test_turning_dry_run_off_removes_this_stacks_shadow():
    cmd = _Cmd()
    DeployModelserviceStep()._delete_stale_dry_run_shadows(cmd, _LIVE)
    assert cmd.calls == [
        (
            "delete",
            "deployment",
            "-l",
            f"{GATE}-model=m",
            "--namespace",
            "bench",
            "--ignore-not-found=true",
        )
    ]


def test_a_dry_run_keeps_its_shadow():
    cmd = _Cmd()
    config = {**_LIVE, "eppKedaSaturation": {"enabled": True, "dryRun": True}}
    DeployModelserviceStep()._delete_stale_dry_run_shadows(cmd, config)
    assert cmd.calls == []


class _Logger:
    def __getattr__(self, _name):
        return lambda *a, **k: None


@dataclass
class _Context:
    rendered_stacks: list[Path]
    deployed_methods: list[str] = field(default_factory=lambda: ["modelservice"])
    is_openshift: bool = False
    deep_clean: bool = False
    remove_prism: bool = False
    logger: _Logger = field(default_factory=_Logger)


def test_teardown_deletes_a_shadow_left_from_an_earlier_dry_run(tmp_path):
    """Whatever the config at teardown time, a shadow does not outlive it."""
    stack = tmp_path / "s1"
    stack.mkdir()
    (stack / "config.yaml").write_text(
        yaml.safe_dump({"huggingface": {"secretName": "hf"}})
    )
    cmd = _Cmd(
        workloads=[
            _deployment("m-decode-dryrun", {GATE: "true", "llm-d.ai/model": "m"}, 3, 0),
            _deployment("unrelated", {"app": "x"}, 1, 1),
        ]
    )
    DeleteResourcesStep()._normal_clean(cmd, _Context([stack]), ["bench"], [])
    assert "deployment/m-decode-dryrun" in cmd.deleted()
    assert "deployment/unrelated" not in cmd.deleted()


def _deployment(name, labels, replicas, ready):
    return {
        "kind": "Deployment",
        "metadata": {"name": name},
        "spec": {"replicas": replicas, "template": {"metadata": {"labels": labels}}},
        "status": {"readyReplicas": ready},
    }


def test_replica_time_series_records_the_dry_run_decision(tmp_path):
    deployments = {
        "items": [
            _deployment(
                "m-decode",
                {
                    "llm-d.ai/inferenceServing": "true",
                    "llm-d.ai/model": "m",
                    "llm-d.ai/role": "decode",
                },
                2,
                2,
            ),
            _deployment(
                "m-decode-dryrun",
                {GATE: "true", "llm-d.ai/model": "m", "llm-d.ai/role": "decode-dryrun"},
                7,
                0,
            ),
            _deployment("unrelated", {"app": "x"}, 1, 1),
        ]
    }
    fake_kubectl = tmp_path / "kubectl"
    payload = tmp_path / "deployments.json"
    payload.write_text(json.dumps(deployments))
    fake_kubectl.write_text(
        "#!/usr/bin/env bash\n"
        f'if [[ "$*" == *deployments,statefulsets* ]]; then cat {payload}; else echo \'{{"items":[]}}\'; fi\n'
    )
    fake_kubectl.chmod(0o755)

    env = {
        **os.environ,
        "KUBECTL_CMD": str(fake_kubectl),
        "LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR": str(tmp_path),
        "LLMDBENCH_HARNESS_STACK_NAME": "m",
    }
    subprocess.run(
        [
            "bash",
            "-c",
            f'source <(sed "/^# Main command dispatcher/,\\$d" {COLLECT_METRICS}); collect_replica_status',
        ],
        env=env,
        check=True,
        capture_output=True,
    )

    snapshot = json.loads(
        (tmp_path / "metrics" / "processed" / "replica_status.json").read_text()
    )
    controllers = {c["name"]: c for c in snapshot["controllers"]}
    assert set(controllers) == {"m-decode", "m-decode-dryrun"}
    assert controllers["m-decode-dryrun"]["desired_replicas"] == 7
    assert controllers["m-decode-dryrun"]["ready_replicas"] == 0
    assert controllers["m-decode-dryrun"]["role"] == "decode-dryrun"
