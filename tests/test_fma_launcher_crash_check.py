"""Tests for the FMA validator's launcher crash check (fma_launcher_no_crash).

The guide runs a pool of launchers (one per GPU node) and only the bound one
serves the model. A crash on an idle pool member (e.g. a node whose GPU fails
NVML init) must warn and name the node, not fail the stack; a crash on the
bound launcher must still fail it.
"""

from __future__ import annotations

import json
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

# Stub planner so we can import smoketest modules (see
# test_smoketest_inference.py for the same pattern + rationale).
if "planner" not in sys.modules:
    planner_stub = types.ModuleType("planner")
    capacity_stub = types.ModuleType("planner.capacity_planner")
    capacity_stub.__getattr__ = lambda name: lambda *a, **kw: None  # type: ignore[attr-defined]
    sys.modules["planner"] = planner_stub
    sys.modules["planner.capacity_planner"] = capacity_stub

from llmdbenchmark.smoketests.validators import fma

CRASHED = {
    "name": "inference-server",
    "ready": False,
    "restartCount": 5,
    "state": {"waiting": {"reason": "CrashLoopBackOff"}},
    "lastState": {"terminated": {"reason": "Error", "exitCode": 1}},
}
HEALTHY = {
    "name": "inference-server",
    "ready": True,
    "restartCount": 0,
    "state": {"running": {}},
}


def _launcher(name, node, *, bound=False, container=HEALTHY):
    labels = {"app.kubernetes.io/component": "launcher"}
    if bound:
        labels[fma._FMA_BOUND_LABEL] = "fma-requester-abc"
    return {
        "metadata": {"name": name, "labels": labels},
        "spec": {"nodeName": node},
        "status": {
            "phase": "Running",
            "podIP": f"10.0.0.{len(name)}",
            "containerStatuses": [container],
        },
    }


class _FakeCmd:
    """Answers `kubectl get pods -l <selector>` from a fixed pod list.

    *launcher_listings* overrides successive launcher-selector listings: each
    entry is a pod list, or None for a failed kubectl call. Once exhausted,
    listings fall back to *pods*.
    """

    dry_run = False

    def __init__(self, pods, launcher_listings=None):
        self.pods = pods
        self.launcher_listings = list(launcher_listings or [])

    def kube(self, *args, check=False):
        selector = args[args.index("-l") + 1]
        items = self.pods
        if fma._FMA_BOUND_LABEL in selector:
            items = [
                p for p in items if fma._FMA_BOUND_LABEL in p["metadata"]["labels"]
            ]
        elif self.launcher_listings:
            items = self.launcher_listings.pop(0)
            if items is None:
                return SimpleNamespace(success=False, stdout="")
        return SimpleNamespace(success=True, stdout=json.dumps({"items": items}))


@pytest.fixture
def run(tmp_path):
    def _run(pods, launcher_listings=None):
        ctx = MagicMock()
        ctx.deployed_methods = ["kustomize"]
        ctx.require_cmd.return_value = _FakeCmd(pods, launcher_listings)
        ctx.require_namespace.return_value = "ns"
        validator = fma.FmaValidator.__new__(fma.FmaValidator)
        validator._FMA_SERVING_TIMEOUT = 0
        validator._fma_wait_signature_annotation = lambda *a, **kw: True
        validator._wait_for_model_ready = lambda *a, **kw: None
        report = validator.run_health_checks(ctx, tmp_path)
        checks = {c.name: c for c in report.checks}
        return report, checks, ctx.logger

    return _run


def test_idle_launcher_crash_warns_and_passes(run):
    report, checks, logger = run(
        [
            _launcher("launcher-bound", "node-a", bound=True),
            _launcher("launcher-idle", "node-bad", container=CRASHED),
        ]
    )

    assert report.passed, report.errors()
    assert checks["fma_launcher_no_crash"].passed
    (warning,), _ = logger.log_warning.call_args
    assert "launcher-idle/inference-server" in warning
    assert "on node node-bad" in warning


def test_bound_launcher_crash_fails_with_node(run):
    report, checks, _ = run(
        [
            _launcher("launcher-bound", "node-bad", bound=True, container=CRASHED),
            _launcher("launcher-idle", "node-a"),
        ]
    )

    assert not report.passed
    message = checks["fma_launcher_no_crash"].message
    assert "launcher-bound/inference-server (CrashLoopBackOff" in message
    assert "on node node-bad" in message
    assert "launcher-idle" not in message


def test_no_binding_lists_crashed_launchers(run):
    report, checks, _ = run([_launcher("launcher-idle", "node-bad", container=CRASHED)])

    assert not report.passed
    assert "fma_launcher_no_crash" not in checks
    message = checks["fma_launcher_bound"].message
    assert "launcher-idle/inference-server" in message
    assert "on node node-bad" in message


def test_all_healthy_passes_without_warning(run):
    report, checks, logger = run(
        [
            _launcher("launcher-bound", "node-a", bound=True),
            _launcher("launcher-idle", "node-b"),
        ]
    )

    assert report.passed, report.errors()
    assert checks["fma_launcher_no_crash"].passed
    logger.log_warning.assert_not_called()


def test_failed_refresh_fails_instead_of_reusing_snapshot(run):
    bound = _launcher("launcher-bound", "node-bad", bound=True, container=CRASHED)
    # Pre-wait snapshot: the launcher is not labeled bound yet.
    snapshot = [_launcher("launcher-bound", "node-bad", container=CRASHED)]
    report, checks, logger = run([bound], launcher_listings=[snapshot, None])

    assert not report.passed
    assert not checks["fma_launcher_pods_listed"].passed
    assert "fma_launcher_no_crash" not in checks
    logger.log_warning.assert_not_called()


def test_serving_pod_counts_as_bound_without_label_in_listing(run):
    bound = _launcher("launcher-bound", "node-bad", bound=True, container=CRASHED)
    unlabeled = _launcher("launcher-bound", "node-bad", container=CRASHED)
    report, checks, logger = run([bound], launcher_listings=[[bound], [unlabeled]])

    assert not report.passed
    assert "launcher-bound/inference-server" in checks["fma_launcher_no_crash"].message
    logger.log_warning.assert_not_called()


def test_serving_pod_missing_from_listing_is_still_checked(run):
    bound = _launcher("launcher-bound", "node-bad", bound=True, container=CRASHED)
    report, checks, _ = run([bound], launcher_listings=[[bound], []])

    assert not report.passed
    assert "on node node-bad" in checks["fma_launcher_no_crash"].message


def test_classification_uses_post_binding_state(run):
    # Binding happens during _fma_wait_serving: the pre-wait listing has no
    # bound label, the post-wait listing does. Classifying the pre-wait
    # listing would call this crash idle and only warn.
    unbound = _launcher("launcher-a", "node-bad", container=CRASHED)
    bound = _launcher("launcher-a", "node-bad", bound=True, container=CRASHED)
    idle = _launcher("launcher-b", "node-ok")
    report, checks, logger = run(
        [bound, idle], launcher_listings=[[unbound, idle], [bound, idle]]
    )

    assert not report.passed
    message = checks["fma_launcher_no_crash"].message
    assert "launcher-a/inference-server (CrashLoopBackOff" in message
    assert "on node node-bad" in message
    logger.log_warning.assert_not_called()
