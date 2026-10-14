"""Tests for autoscaler-aware smoketests: a workload an autoscaler holds at
zero passes the health check, the inference test triggers the scale-up through
the router, and replica counts use the autoscaler's [min, max] window."""

from __future__ import annotations

import json
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import yaml

# Stub planner so we can import smoketest modules (see
# test_smoketest_inference.py for the same pattern + rationale).
if "planner" not in sys.modules:
    planner_stub = types.ModuleType("planner")
    capacity_stub = types.ModuleType("planner.capacity_planner")
    capacity_stub.__getattr__ = lambda name: lambda *a, **kw: None  # type: ignore[attr-defined]
    sys.modules["planner"] = planner_stub
    sys.modules["planner.capacity_planner"] = capacity_stub

from llmdbenchmark.smoketests import autoscaling
from llmdbenchmark.smoketests import base as base_module
from llmdbenchmark.smoketests.base import BaseSmoketest, SmoketestReport
from llmdbenchmark.smoketests.validators import fma
from llmdbenchmark.smoketests.validators.cpu import CpuValidator
from llmdbenchmark.smoketests.validators.gpu import GpuValidator
from llmdbenchmark.smoketests.validators.spyre import SpyreValidator

MISSING = "error: the server doesn't have a resource type"
COMPLETION = json.dumps({"choices": [{"text": " Washington"}]})
DECODE_LABELS = {"llm-d.ai/model": "qwen", "llm-d.ai/role": "decode"}
MS_CONFIG = {
    "model": {"name": "Qwen/Qwen3-0.6B"},
    "model_id_label": "qwen",
    "decode": {"replicas": 1},
}
PD_CONFIG = {**MS_CONFIG, "prefill": {"enabled": True, "replicas": 1}}


def _scaledobject(target, minimum=0, maximum=2, name="so"):
    return {
        "metadata": {"name": name},
        "spec": {
            "scaleTargetRef": {"name": target},
            "minReplicaCount": minimum,
            "maxReplicaCount": maximum,
        },
    }


def _hpa(target, minimum=1, maximum=4, name="hpa", owned_by_keda=False):
    metadata = {"name": name}
    if owned_by_keda:
        metadata["ownerReferences"] = [{"kind": "ScaledObject", "name": "so"}]
    return {
        "metadata": metadata,
        "spec": {
            "scaleTargetRef": {"kind": "Deployment", "name": target},
            "minReplicas": minimum,
            "maxReplicas": maximum,
        },
    }


def _deployment(name, replicas, labels):
    return {
        "kind": "Deployment",
        "metadata": {"name": name},
        "spec": {"replicas": replicas, "template": {"metadata": {"labels": labels}}},
    }


def _scale_up(deployments):
    """How an autoscaler reacts to the request: zeroed Deployments go to 1."""
    for d in deployments:
        if d["spec"]["replicas"] == 0:
            d["spec"]["replicas"] = 1


class FakeCluster:
    """Answers the smoketest's kubectl calls from in-memory state.

    ``resources`` maps a resource to its items, or to an error string; ``pods``
    maps a role to its pod phases. ``after_curl`` runs on the Deployments
    after each successful request.
    """

    dry_run = False

    def __init__(self, resources, curl_ok=True, pods=None, after_curl=_scale_up):
        self.resources = resources
        self.curl_ok = curl_ok
        self.pods = pods or {}
        self.after_curl = after_curl
        self.curls: list[str] = []

    def _pods(self, args):
        selector = args[args.index("-l") + 1].split(",")
        role = next((r for r in self.pods if f"llm-d.ai/role={r}" in selector), None)
        phases = self.pods.get(role, [])
        output = " ".join(args)
        if "status.podIP" in output:
            return " ".join(f"10.1.0.{i}" for i, _ in enumerate(phases))
        if "custom-columns" in output:
            return "\n".join(f"{role}-{i} true" for i, _ in enumerate(phases))
        return " ".join(phases)

    @staticmethod
    def _result(success=True, stdout="", stderr=""):
        return SimpleNamespace(
            success=success, stdout=stdout, stderr=stderr, dry_run=False
        )

    def kube(self, *args, check=False):
        if args[0] == "run":
            self.curls.append(args[-1])
            if not self.curl_ok:
                return self._result(False, stderr="curl: (28) Operation timed out")
            self.after_curl(self.resources.get("deployments,statefulsets", []))
            return self._result(stdout=f"{COMPLETION}\n200")
        if args[0] == "get" and args[1] == "pods":
            return self._result(stdout=self._pods(args))
        if args[0] == "get":
            value = self.resources.get(args[1], MISSING)
            if isinstance(value, str):
                return self._result(False, stderr=value)
            return self._result(stdout=json.dumps({"items": value}))
        return self._result()


def _keda_cluster(target="qwen-decode", replicas=0, labels=None, minimum=0, **kw):
    """A KEDA ScaledObject over one Deployment (decode at 0 by default)."""
    return FakeCluster(
        {
            "scaledobjects.keda.sh": [_scaledobject(target, minimum=minimum)],
            "deployments,statefulsets": [
                _deployment(
                    target, replicas, DECODE_LABELS if labels is None else labels
                )
            ],
        },
        **kw,
    )


def _context(cmd, methods):
    ctx = MagicMock(container_only=False, dry_run=False, is_openshift=False)
    ctx.deployed_methods = methods
    ctx.require_cmd.return_value = cmd
    ctx.require_namespace.return_value = "ns"
    return ctx


def _stack(tmp_path, config):
    stack = tmp_path / "stack"
    stack.mkdir()
    (stack / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    return stack


def _checks(report):
    return {c.name: c for c in report.checks}


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


class TestDiscovery:
    def test_keda_target_at_zero(self):
        target = autoscaling.discover_scale_targets(_keda_cluster(), "ns")[
            "Deployment/qwen-decode"
        ]

        assert target.autoscaler == "keda"
        assert target.scaled_to_zero
        assert target.matches("llm-d.ai/model=qwen,llm-d.ai/role=decode")
        assert not target.matches("llm-d.ai/role=prefill")

    def test_keda_owned_hpa_is_not_reported_twice(self):
        cluster = FakeCluster(
            {
                "scaledobjects.keda.sh": [_scaledobject("qwen-decode")],
                "horizontalpodautoscalers": [
                    _hpa("qwen-decode", owned_by_keda=True),
                    _hpa("other", minimum=2, maximum=5),
                ],
                "deployments,statefulsets": [
                    _deployment("qwen-decode", 0, DECODE_LABELS),
                    _deployment("other", 3, {}),
                ],
            }
        )

        targets = autoscaling.discover_scale_targets(cluster, "ns")

        assert targets["Deployment/qwen-decode"].min_replicas == 0
        other = targets["Deployment/other"]
        assert (other.autoscaler, other.min_replicas, other.max_replicas) == (
            "hpa",
            2,
            5,
        )
        assert not other.scaled_to_zero

    def test_missing_crd_is_silent_and_denied_read_warns(self):
        logger = MagicMock()
        cluster = FakeCluster(
            {"horizontalpodautoscalers": 'Error: forbidden: cannot list "hpa"'}
        )

        assert autoscaling.discover_scale_targets(cluster, "ns", logger) == {}

        (warning,), _ = logger.log_warning.call_args
        assert "horizontalpodautoscalers" in warning
        assert logger.log_warning.call_count == 1  # not for the missing CRD

    def test_no_executor_or_dry_run_discovers_nothing(self):
        assert autoscaling.discover_scale_targets(None, "ns") == {}
        cluster = FakeCluster({})
        cluster.dry_run = True
        assert autoscaling.discover_scale_targets(cluster, "ns") == {}


# ---------------------------------------------------------------------------
# BaseSmoketest
# ---------------------------------------------------------------------------


@pytest.fixture
def base(monkeypatch):
    monkeypatch.setattr(
        BaseSmoketest,
        "discover_endpoint",
        lambda self, *a, **kw: ("10.0.0.9", "80", False),
    )
    monkeypatch.setattr(BaseSmoketest, "_print_demo_command", lambda *a, **kw: None)
    return BaseSmoketest.__new__(BaseSmoketest)


@pytest.fixture
def no_serving_probes(monkeypatch):
    def _fail(*a, **kw):
        pytest.fail("serving probes must be deferred while serving is at 0")

    monkeypatch.setattr(BaseSmoketest, "_check_health", _fail)
    monkeypatch.setattr(BaseSmoketest, "_wait_for_model_ready", _fail)
    monkeypatch.setattr(base_module, "test_model_serving", _fail)


class TestBaseHealthAtZero:
    # draining: spec.replicas is already 0 but the old pod still terminates.
    @pytest.mark.parametrize("pods", [[], ["Running"]], ids=["no-pods", "draining"])
    def test_zeroed_role_passes_and_defers_serving_checks(
        self, base, no_serving_probes, tmp_path, pods
    ):
        cluster = _keda_cluster(pods={"decode": pods})

        report = base.run_health_checks(
            _context(cluster, ["modelservice"]), _stack(tmp_path, MS_CONFIG)
        )

        assert report.passed, report.errors()
        checks = _checks(report)
        assert "serving_checks_deferred" in checks
        assert ("still draining" in checks["decode_scaled_to_zero"].message) == bool(
            pods
        )

    def test_no_pods_without_autoscaler_still_fails(self, base, monkeypatch, tmp_path):
        # The run continues past the role check; skip its 120s /health poll.
        monkeypatch.setattr(BaseSmoketest, "_check_health", lambda *a, **kw: None)

        report = base.run_health_checks(
            _context(FakeCluster({}), ["modelservice"]), _stack(tmp_path, MS_CONFIG)
        )

        assert not report.passed
        assert "decode_pods_exist" in _checks(report)

    def test_zeroed_decode_defers_probes_but_checks_prefill_pods(
        self, base, no_serving_probes, tmp_path
    ):
        cluster = _keda_cluster(pods={"prefill": ["Running"]})

        report = base.run_health_checks(
            _context(cluster, ["modelservice"]), _stack(tmp_path, PD_CONFIG)
        )

        assert report.passed, report.errors()
        checks = _checks(report)
        assert {"decode_scaled_to_zero", "prefill_pods_running"} <= set(checks)
        assert "'decode'" in checks["serving_checks_deferred"].message

    def test_zeroed_prefill_keeps_serving_probes(self, base, monkeypatch, tmp_path):
        # prefill is at zero, decode is serving: the endpoint can still answer.
        probed: list[str] = []
        monkeypatch.setattr(
            BaseSmoketest, "_check_health", lambda *a, **kw: probed.append("health")
        )
        monkeypatch.setattr(
            BaseSmoketest,
            "_wait_for_model_ready",
            lambda *a, **kw: probed.append("model_ready"),
        )
        monkeypatch.setattr(base_module, "test_model_serving", lambda *a, **kw: None)
        cluster = _keda_cluster(
            "qwen-prefill",
            labels={"llm-d.ai/model": "qwen", "llm-d.ai/role": "prefill"},
            pods={"decode": ["Running"]},
        )

        report = base.run_health_checks(
            _context(cluster, ["modelservice"]), _stack(tmp_path, PD_CONFIG)
        )

        assert report.passed, report.errors()
        names = set(_checks(report))
        assert {"prefill_scaled_to_zero", "decode_pods_running"} <= names
        assert "service_endpoint" in names
        assert "serving_checks_deferred" not in names
        assert probed == ["health", "model_ready"]


class TestBaseInferenceAtZero:
    @pytest.mark.parametrize(
        ("smoketest", "timeout"),
        [({}, 600), ({"coldStartTimeout": 900}, 900)],
        ids=["default", "configured"],
    )
    def test_one_request_through_router_with_cold_start_timeout(
        self, base, tmp_path, smoketest, timeout
    ):
        cluster = _keda_cluster()
        config = {**MS_CONFIG, "harness": {"smoketest": smoketest}}

        report = base.run_inference_test(
            _context(cluster, ["modelservice"]), _stack(tmp_path, config)
        )

        assert report.passed, report.errors()
        (curl,) = cluster.curls
        assert f"--max-time {timeout}" in curl
        assert "http://10.0.0.9:80/v1/completions" in curl
        check = _checks(report)["scale_from_zero_qwen-decode"]
        assert "scaled 0 -> 1 by keda" in check.message

    @pytest.mark.parametrize(
        ("after_curl", "actual", "reason"),
        [
            # The follow-up read cannot find the workload any more.
            (lambda d: d.clear(), "unknown", "not verified"),
            # The request succeeded but nothing scaled up.
            (lambda d: None, "0", "did not come from a scale-up"),
        ],
        ids=["replicas-unknown", "still-zero"],
    )
    def test_scale_up_must_be_observed(
        self, base, tmp_path, after_curl, actual, reason
    ):
        cluster = _keda_cluster(after_curl=after_curl)

        report = base.run_inference_test(
            _context(cluster, ["modelservice"]), _stack(tmp_path, MS_CONFIG)
        )

        assert not report.passed
        check = _checks(report)["scale_from_zero_qwen-decode"]
        assert check.actual == actual
        assert reason in check.message

    def test_failed_cold_start_explains_the_timeout(self, base, tmp_path):
        cluster = _keda_cluster(curl_ok=False)

        report = base.run_inference_test(
            _context(cluster, ["modelservice"]), _stack(tmp_path, MS_CONFIG)
        )

        assert not report.passed
        assert len(cluster.curls) == 1  # no retry restarting the cold start
        assert "harness.smoketest.coldStartTimeout" in report.errors()[0]

    def test_serving_workload_keeps_default_timeout(self, base, tmp_path):
        cluster = _keda_cluster(replicas=1, minimum=1)

        report = base.run_inference_test(
            _context(cluster, ["modelservice"]), _stack(tmp_path, MS_CONFIG)
        )

        assert report.passed, report.errors()
        assert "--max-time 120" in cluster.curls[0]
        assert not any(name.startswith("scale_from_zero") for name in _checks(report))


@pytest.mark.parametrize(
    ("resources", "pod_count", "passed", "expected"),
    [
        (
            {"horizontalpodautoscalers": [_hpa("qwen-decode", 1, 3)]},
            1,
            True,
            "1..3 (hpa window)",
        ),
        (
            {"horizontalpodautoscalers": [_hpa("qwen-decode", 1, 3)]},
            4,
            False,
            "1..3 (hpa window)",
        ),
        ({}, 1, False, "2"),  # no autoscaler: the plan's replicas are strict
    ],
    ids=["in-window", "outside-window", "no-autoscaler"],
)
def test_replica_window(monkeypatch, resources, pod_count, passed, expected):
    resources = {
        **resources,
        "deployments,statefulsets": [
            _deployment("qwen-decode", pod_count, DECODE_LABELS)
        ],
    }
    pods = [
        {
            "metadata": {"name": f"d-{i}", "namespace": "ns"},
            "spec": {"nodeName": "n", "containers": [{"name": "vllm", "args": []}]},
        }
        for i in range(pod_count)
    ]
    monkeypatch.setattr(BaseSmoketest, "get_pod_specs", lambda *a, **kw: pods)
    report = SmoketestReport()

    BaseSmoketest.__new__(BaseSmoketest).validate_role_pods(
        cmd=FakeCluster(resources),
        namespace="ns",
        config={
            "model": {"maxModelLen": 16384, "blockSize": 16},
            "vllmCommon": {"flags": {}},
            "decode": {"replicas": 2},
        },
        role="decode",
        model_short="qwen",
        report=report,
    )

    check = _checks(report)["decode_replicas"]
    assert check.passed == passed, check.message
    assert check.expected == expected


# ---------------------------------------------------------------------------
# FmaValidator
# ---------------------------------------------------------------------------

FMA_GUIDE_CONFIG = {
    "model": {"name": "Qwen/Qwen3-0.6B"},
    "kustomize": {"guideName": "fast-model-actuation/fast-model-actuation-keda"},
}
REQUESTER_LABELS = {"llm-d.ai/guide": "fast-model-actuation-keda"}


def _fma_method_config(plan_replicas=1):
    return {
        "model": {"name": "Qwen/Qwen3-0.6B"},
        "model_id_label": "qwen",
        "fma": {"requester": {"replicas": plan_replicas}},
    }


def _launcher(name="launcher-a", node="node-a", statuses=()):
    return {
        "metadata": {"name": name, "labels": {}},
        "spec": {"nodeName": node},
        "status": {"containerStatuses": list(statuses)},
    }


@pytest.fixture
def fma_validator(monkeypatch):
    monkeypatch.setattr(fma, "_fma_pods", lambda *a, **kw: [_launcher()])
    monkeypatch.setattr(
        BaseSmoketest,
        "discover_endpoint",
        lambda self, *a, **kw: ("10.0.0.7", "80", False),
    )
    monkeypatch.setattr(BaseSmoketest, "_print_demo_command", lambda *a, **kw: None)
    validator = fma.FmaValidator.__new__(fma.FmaValidator)
    validator._fma_wait_serving = lambda *a, **kw: pytest.fail(
        "no launcher is bound at zero"
    )
    return validator


class TestFmaAtZero:
    def test_health_passes_and_idle_launcher_crash_warns(
        self, fma_validator, monkeypatch, tmp_path
    ):
        crashed = {
            "name": "inference-server",
            "ready": False,
            "state": {"waiting": {"reason": "CrashLoopBackOff"}},
        }
        monkeypatch.setattr(
            fma,
            "_fma_pods",
            lambda *a, **kw: [_launcher("launcher-bad", "node-bad", [crashed])],
        )
        ctx = _context(
            _keda_cluster("fma-requester", labels=REQUESTER_LABELS), ["kustomize"]
        )

        report = fma_validator.run_health_checks(
            ctx, _stack(tmp_path, FMA_GUIDE_CONFIG)
        )

        assert report.passed, report.errors()
        check = _checks(report)["fma_requester_scaled_to_zero"]
        assert "Deployment/fma-requester" in check.message
        (warning,), _ = ctx.logger.log_warning.call_args
        assert "launcher-bad/inference-server" in warning
        assert "on node node-bad" in warning

    # plan-0: the plan starts the requester at 0, yet it is autoscaled, so the
    # scale-up must still be exercised rather than skipped.
    @pytest.mark.parametrize(
        ("requester", "labels", "methods", "config"),
        [
            ("fma-requester", REQUESTER_LABELS, ["kustomize"], FMA_GUIDE_CONFIG),
            ("fma-requester-qwen", {}, ["modelservice", "fma"], _fma_method_config(1)),
            ("fma-requester-qwen", {}, ["modelservice", "fma"], _fma_method_config(0)),
        ],
        ids=["guide-label", "fma-method-by-name", "fma-method-plan-0"],
    )
    def test_inference_goes_through_the_router(
        self, fma_validator, tmp_path, requester, labels, methods, config
    ):
        cluster = _keda_cluster(requester, labels=labels)

        report = fma_validator.run_inference_test(
            _context(cluster, methods), _stack(tmp_path, config)
        )

        assert report.passed, report.errors()
        (curl,) = cluster.curls
        assert "http://10.0.0.7:80/v1/completions" in curl
        assert "--max-time 600" in curl
        assert f"scale_from_zero_{requester}" in _checks(report)

    def test_fma_only_requester_at_zero_skips_without_router(
        self, fma_validator, tmp_path
    ):
        cluster = _keda_cluster("fma-requester-qwen", labels={})

        report = fma_validator.run_inference_test(
            _context(cluster, ["fma"]), _stack(tmp_path, _fma_method_config())
        )

        assert report.passed, report.errors()
        assert cluster.curls == []
        assert "fma_inference_skipped_no_router" in _checks(report)

    def test_other_stacks_requester_at_zero_is_ignored(self, fma_validator, tmp_path):
        # Shared namespace: another stack's requester is at 0, this one serves.
        fma_validator._fma_wait_serving = lambda *a, **kw: None
        cluster = FakeCluster(
            {
                "scaledobjects.keda.sh": [
                    _scaledobject("fma-requester-other", name="so-other"),
                    _scaledobject("fma-requester-qwen", name="so-qwen"),
                ],
                "deployments,statefulsets": [
                    _deployment("fma-requester-other", 0, {}),
                    _deployment("fma-requester-qwen", 1, {}),
                ],
            }
        )

        report = fma_validator.run_inference_test(
            _context(cluster, ["fma"]), _stack(tmp_path, _fma_method_config())
        )

        assert cluster.curls == []  # no cold-start request via the router
        names = set(_checks(report))
        assert "fma_inference_endpoint" in names
        assert not any(n.startswith("scale_from_zero") for n in names)

    def test_serving_requester_keeps_the_launcher_path(self, fma_validator, tmp_path):
        fma_validator._fma_wait_serving = lambda *a, **kw: None
        cluster = _keda_cluster("fma-requester", replicas=1, labels=REQUESTER_LABELS)

        report = fma_validator.run_inference_test(
            _context(cluster, ["kustomize"]), _stack(tmp_path, FMA_GUIDE_CONFIG)
        )

        assert "fma_inference_endpoint" in _checks(report)


# ---------------------------------------------------------------------------
# Config validation (cpu / gpu / spyre examples)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cls", [CpuValidator, GpuValidator, SpyreValidator])
@pytest.mark.parametrize("zeroed", [True, False], ids=["zeroed", "no-autoscaler"])
def test_config_validation_without_serving_pods(cls, zeroed, monkeypatch, tmp_path):
    monkeypatch.setattr(BaseSmoketest, "get_pod_specs", lambda *a, **kw: [])
    cluster = _keda_cluster() if zeroed else FakeCluster({})

    report = cls.__new__(cls).run_config_validation(
        _context(cluster, ["modelservice"]), _stack(tmp_path, MS_CONFIG)
    )

    assert report.passed == zeroed, report.errors()
    if zeroed:
        assert (
            "Deployment/qwen-decode"
            in _checks(report)["serving_scaled_to_zero"].message
        )
    else:
        assert "No decode or standalone pods found" in report.errors()[0]
