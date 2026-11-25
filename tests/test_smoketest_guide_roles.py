"""Tests for the role selectors the smoketest builds for kustomize guides.

llm-d guides label a model server that serves both phases
``llm-d.ai/role=prefill-decode`` (llm-d/llm-d#3101); earlier guides labeled it
``decode``. A decode check for a guide must find either, and P/D decode
workers, which keep ``decode``, must still be selected as before.
"""

from __future__ import annotations

import json
import shlex
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import yaml

# Stub planner so we can import the smoketest modules.
if "planner" not in sys.modules:
    planner_stub = types.ModuleType("planner")
    capacity_stub = types.ModuleType("planner.capacity_planner")
    capacity_stub.__getattr__ = lambda name: lambda *a, **kw: None  # type: ignore[attr-defined]
    planner_stub.__file__ = capacity_stub.__file__ = "<planner stub>"
    sys.modules["planner"] = planner_stub
    sys.modules["planner.capacity_planner"] = capacity_stub

from llmdbenchmark.smoketests import base as base_module
from llmdbenchmark.smoketests.autoscaling import ScaleTarget, selector_matches
from llmdbenchmark.smoketests.base import (
    BaseSmoketest,
    SmoketestReport,
    guide_role_selector,
)

MISSING = "error: the server doesn't have a resource type"
COMPLETION = json.dumps({"choices": [{"text": " Washington"}]})
DECODE_SET = "llm-d.ai/role in (decode,prefill-decode)"


def _pod(guide, role, phase="Running"):
    return {"labels": {"llm-d.ai/guide": guide, "llm-d.ai/role": role}, "phase": phase}


class GuideCluster:
    """Answers ``kubectl get pods -l`` by matching pod labels.

    The selector argument is unquoted with ``shlex`` as the local shell would,
    so an unquoted set-based selector fails here as it does in ``bash -c``.
    """

    dry_run = False

    def __init__(self, pods):
        self.pods = pods
        self.selectors: list[str] = []

    @staticmethod
    def _result(success=True, stdout="", stderr=""):
        return SimpleNamespace(
            success=success, stdout=stdout, stderr=stderr, dry_run=False
        )

    def kube(self, *args, check=False):
        if args[0] == "run":
            return self._result(stdout=f"{COMPLETION}\n200")
        if args[:2] != ("get", "pods"):
            return self._result(False, stderr=MISSING)
        (selector,) = shlex.split(args[args.index("-l") + 1])
        self.selectors.append(selector)
        pods = [p for p in self.pods if selector_matches(p["labels"], selector)]
        output = " ".join(args)
        if "status.podIP" in output:
            return self._result(
                stdout=" ".join(f"10.1.0.{i}" for i, _ in enumerate(pods))
            )
        if "custom-columns" in output:
            return self._result(
                stdout="\n".join(f"pod-{i} true" for i, _ in enumerate(pods))
            )
        if args[-1] == "json":
            return self._result(stdout=json.dumps({"items": []}))
        return self._result(stdout=" ".join(p["phase"] for p in pods))


def _context(cmd):
    ctx = MagicMock(container_only=False, dry_run=False, is_openshift=False)
    ctx.deployed_methods = ["kustomize"]
    ctx.require_cmd.return_value = cmd
    ctx.require_namespace.return_value = "ns"
    return ctx


def _stack(tmp_path, guide):
    stack = tmp_path / "stack"
    stack.mkdir()
    config = {
        "model": {"name": "Qwen/Qwen3-32B"},
        "model_id_label": "qwen",
        "kustomize": {"guideName": guide},
    }
    (stack / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    return stack


@pytest.fixture
def base(monkeypatch):
    monkeypatch.setattr(
        BaseSmoketest,
        "discover_endpoint",
        lambda self, *a, **kw: ("10.0.0.9", "80", False),
    )
    monkeypatch.setattr(BaseSmoketest, "_print_demo_command", lambda *a, **kw: None)
    monkeypatch.setattr(BaseSmoketest, "_check_health", lambda *a, **kw: None)
    monkeypatch.setattr(BaseSmoketest, "_wait_for_model_ready", lambda *a, **kw: None)
    monkeypatch.setattr(base_module, "test_model_serving", lambda *a, **kw: None)
    return BaseSmoketest.__new__(BaseSmoketest)


def _checks(report):
    return {c.name: c for c in report.checks}


# ---------------------------------------------------------------------------
# Selectors
# ---------------------------------------------------------------------------


class TestGuideRoleSelector:
    def test_decode_matches_both_labels(self):
        assert guide_role_selector("optimized-baseline", "decode") == (
            f"llm-d.ai/guide=optimized-baseline,{DECODE_SET}"
        )

    def test_other_roles_stay_exact(self):
        assert guide_role_selector("pd-disaggregation", "prefill") == (
            "llm-d.ai/guide=pd-disaggregation,llm-d.ai/role=prefill"
        )

    def test_no_role_selects_the_guide(self):
        assert guide_role_selector("fast-model-actuation/fma-keda", None) == (
            "llm-d.ai/guide=fma-keda"
        )


class TestSelectorMatches:
    @pytest.mark.parametrize(
        "labels, selector, expected",
        [
            ({"a": "1", "b": "2"}, "a=1,b=2", True),
            ({"a": "1", "b": "3"}, "a=1,b=2", False),
            ({"r": "decode"}, "r in (decode,prefill-decode)", True),
            ({"r": "prefill-decode"}, "r in (decode,prefill-decode)", True),
            ({"r": "prefill"}, "r in (decode,prefill-decode)", False),
            ({}, "r in (decode,prefill-decode)", False),
            (
                {"g": "x", "r": "decode"},
                f"g=x,{DECODE_SET.replace('llm-d.ai/role', 'r')}",
                True,
            ),
            (
                {"g": "y", "r": "decode"},
                f"g=x,{DECODE_SET.replace('llm-d.ai/role', 'r')}",
                False,
            ),
            ({"a": "1"}, "a!=2", False),  # unsupported form never matches
        ],
    )
    def test_terms(self, labels, selector, expected):
        assert selector_matches(labels, selector) is expected

    def test_scale_target_uses_set_terms(self):
        target = ScaleTarget(
            "Deployment",
            "decode",
            "keda",
            "so",
            0,
            2,
            current_replicas=0,
            pod_labels={"llm-d.ai/guide": "g", "llm-d.ai/role": "prefill-decode"},
        )
        assert target.matches(guide_role_selector("g", "decode"))
        assert not target.matches(guide_role_selector("g", "prefill"))


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


class TestGuideHealthCheck:
    @pytest.mark.parametrize("role", ["prefill-decode", "decode"])
    def test_monolithic_guide_finds_its_model_server(self, base, tmp_path, role):
        cluster = GuideCluster([_pod("optimized-baseline", role)])

        report = base.run_health_checks(
            _context(cluster), _stack(tmp_path, "optimized-baseline")
        )

        assert report.passed, report.errors()
        assert "decode_pods_running" in _checks(report)
        assert f"llm-d.ai/guide=optimized-baseline,{DECODE_SET}" in cluster.selectors

    def test_pd_guide_still_checks_prefill_and_decode(self, base, tmp_path):
        cluster = GuideCluster(
            [_pod("pd-disaggregation", "prefill"), _pod("pd-disaggregation", "decode")]
        )

        report = base.run_health_checks(
            _context(cluster), _stack(tmp_path, "pd-disaggregation")
        )

        assert report.passed, report.errors()
        assert {"prefill_pods_running", "decode_pods_running"} <= set(_checks(report))

    def test_missing_model_server_still_fails(self, base, tmp_path):
        cluster = GuideCluster([_pod("other-guide", "prefill-decode")])

        report = base.run_health_checks(
            _context(cluster), _stack(tmp_path, "optimized-baseline")
        )

        assert not report.passed
        assert "decode_pods_exist" in _checks(report)


def test_validate_role_pods_selects_both_decode_labels():
    cluster = GuideCluster([])
    smoketest = BaseSmoketest.__new__(BaseSmoketest)
    config = {"kustomize": {"enabled": True, "guideName": "tiered-prefix-cache"}}

    smoketest.validate_role_pods(
        cluster, "ns", config, "decode", "qwen", SmoketestReport()
    )

    assert cluster.selectors == [f"llm-d.ai/guide=tiered-prefix-cache,{DECODE_SET}"]
