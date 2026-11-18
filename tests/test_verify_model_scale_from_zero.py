"""verify_model wakes a scaled-to-zero stack before probing /v1/models."""

from unittest.mock import MagicMock

from llmdbenchmark.run.steps import step_04_verify_model as step
from llmdbenchmark.smoketests.autoscaling import ScaleTarget
from llmdbenchmark.smoketests import base
from llmdbenchmark.smoketests.base import BaseSmoketest

PLAN = {"kustomize": {"guideName": "fast-model-actuation/fast-model-actuation-keda"}}
URL = "http://fast-model-actuation-keda-epp:80"


def _target(replicas):
    return ScaleTarget(
        kind="Deployment",
        name="fma-requester",
        autoscaler="keda",
        owner="scaledobject/q",
        min_replicas=0,
        max_replicas=2,
        current_replicas=replicas,
        pod_labels={"llm-d.ai/guide": "fast-model-actuation-keda"},
    )


def _wake(monkeypatch, replicas):
    calls = []
    monkeypatch.setattr(
        base, "discover_scale_targets", lambda *a: {"t": _target(replicas)}
    )
    monkeypatch.setattr(
        BaseSmoketest,
        "_curl_post",
        staticmethod(lambda *a: calls.append(a) or ("", None)),
    )
    context = MagicMock(deployed_methods=["kustomize"], namespace="ns")
    step.VerifyModelStep._wake_from_zero(MagicMock(), context, PLAN, "ns", URL, "m")
    return calls


def test_scaled_to_zero_sends_inference_request(monkeypatch):
    calls = _wake(monkeypatch, 0)
    assert len(calls) == 1
    assert calls[0][2] == f"{URL}/v1/completions"


def test_running_stack_sends_nothing(monkeypatch):
    assert _wake(monkeypatch, 1) == []
