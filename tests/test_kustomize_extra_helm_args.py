"""Tests for appending ``kustomize.extraHelmValues`` / ``extraHelmSets`` to router commands.

The extra ``-f`` / ``--set`` arguments belong to ``helm install`` / ``helm upgrade``.
A guide's router step can also hold ``kubectl`` and ``kustomize build ... | kubectl
apply -f -`` commands (for example ``coord-disaggregation``'s HTTPRoute), and
appending ``-f <values.yaml>`` to those makes ``kubectl apply`` read the Helm values
file as a manifest.
"""

from __future__ import annotations

import pytest

from llmdbenchmark.standup.steps.step_05_kustomize_deploy import (
    KustomizeDeployStep,
)

VALUES = ["/abs/monitoring.values.yaml"]
SETS = {"router.epp.replicas": "1"}


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        (
            "helm install guide oci://charts/router -f base.values.yaml -n ns",
            "helm install guide oci://charts/router -f base.values.yaml -n ns"
            " -f /abs/monitoring.values.yaml --set router.epp.replicas=1",
        ),
        (
            "helm upgrade --install guide oci://charts/router -n ns",
            "helm upgrade --install guide oci://charts/router -n ns"
            " -f /abs/monitoring.values.yaml --set router.epp.replicas=1",
        ),
        (
            "helm install guide oci://charts/router \\\n    -n ns",
            "helm install guide oci://charts/router \\\n    -n ns"
            " -f /abs/monitoring.values.yaml --set router.epp.replicas=1",
        ),
    ],
)
def test_extra_helm_args_appended_to_helm_install_and_upgrade(command, expected):
    assert (
        KustomizeDeployStep._inject_extra_helm_args(command, VALUES, SETS) == expected
    )


@pytest.mark.parametrize(
    "command",
    [
        "kustomize build /repo/guides/g/router/httproute/base/ | envsubst"
        " | kubectl apply -n ns -f -",
        "kubectl apply -n ns -k /repo/guides/g/manifests",
        "kubectl -n ns rollout status deploy/llm-d-router-epp --timeout=300s",
        "helm repo add llm-d https://example.invalid/charts",
    ],
)
def test_extra_helm_args_not_appended_to_other_commands(command):
    assert KustomizeDeployStep._inject_extra_helm_args(command, VALUES, SETS) == command
