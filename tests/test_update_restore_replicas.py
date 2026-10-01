"""Tests for the replica restore that step 08 runs on update.

Validates that:
- a Deployment scaled by hand is scaled back to the configured count
- a sibling stack in the same namespace is never touched
- a Deployment at the configured count is left alone
- autoscaled, multinode and FMA stacks are never scaled
- a failed scale is an error
"""

from __future__ import annotations

import json
import types
from unittest.mock import MagicMock

import pytest

from llmdbenchmark.standup.steps.step_08_deploy_modelservice import (
    DeployModelserviceStep,
)


class _Result:
    def __init__(self, stdout="", success=True, stderr=""):
        self.stdout = stdout
        self.success = success
        self.stderr = stderr


def _deployment(model, role, replicas):
    """Shaped like the chart's output: the labels are on the pod template only."""
    return {
        "metadata": {"name": f"{model}-{role}", "labels": {"helm.sh/chart": "ms"}},
        "spec": {
            "replicas": replicas,
            "template": {
                "metadata": {"labels": {"llm-d.ai/model": model, "llm-d.ai/role": role}}
            },
        },
    }


class _FakeCmd:
    def __init__(self, live, scale_ok=True):
        # A sibling stack shares the namespace and must never be scaled.
        self.items = [_deployment("sibling", "decode", 0)] + [
            _deployment("m", role, n) for role, counts in live.items() for n in counts
        ]
        self.scale_ok = scale_ok
        self.scaled = []

    def kube(self, *args, **_kwargs):
        if args[0] == "get":
            assert "-l" not in args, "the Deployment itself carries no such label"
            return _Result(json.dumps({"items": self.items}))
        if args[0] == "scale":
            self.scaled.append((args[2], args[3]))
            return _Result(success=self.scale_ok, stderr="forbidden")
        raise AssertionError(args)


def _config(**overrides):
    config = {
        "model_id_label": "m",
        "decode": {"replicas": 1},
        "prefill": {"enabled": False, "replicas": 0},
    }
    config.update(overrides)
    return config


def _restore(cmd, config):
    context = types.SimpleNamespace(logger=MagicMock())
    errors = []
    DeployModelserviceStep()._restore_replicas(cmd, context, config, "ns", errors)
    return errors


def test_a_hand_scaled_decode_is_scaled_back():
    cmd = _FakeCmd({"decode": [0]})
    assert _restore(cmd, _config()) == []
    assert cmd.scaled == [("m-decode", "--replicas=1")]


def test_the_configured_count_is_left_alone():
    cmd = _FakeCmd({"decode": [1]})
    _restore(cmd, _config())
    assert cmd.scaled == []


def test_prefill_is_restored_when_enabled():
    cmd = _FakeCmd({"decode": [1], "prefill": [0]})
    _restore(cmd, _config(prefill={"enabled": True, "replicas": 2}))
    assert cmd.scaled == [("m-prefill", "--replicas=2")]


@pytest.mark.parametrize(
    "overrides",
    [
        {"wva": {"enabled": True}},
        {"multinode": {"enabled": True}},
        {"fma": {"enabled": True}},
    ],
)
def test_a_count_owned_elsewhere_is_not_scaled(overrides):
    cmd = _FakeCmd({"decode": [0]})
    _restore(cmd, _config(**overrides))
    assert cmd.scaled == []


def test_a_failed_scale_is_an_error():
    cmd = _FakeCmd({"decode": [0]}, scale_ok=False)
    assert _restore(cmd, _config())
