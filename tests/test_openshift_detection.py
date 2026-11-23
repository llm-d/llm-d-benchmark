"""OpenShift detection falls back to the group's resource list when the
clusterversion list is refused -- the API server refuses such a list for
a namespace-level user even when the resource does not exist, and a
stray CRD can create the group on a plain cluster. Any other answer
means not OpenShift.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from llmdbenchmark.utilities import cluster


def _patch(
    monkeypatch,
    list_status: int | None = None,
    list_error: Exception | None = None,
    resource_names: tuple[str, ...] | None = None,
    resource_list_error: Exception | None = None,
):
    custom = MagicMock()
    if list_status is not None:
        custom.list_cluster_custom_object.side_effect = cluster.ApiException(
            status=list_status
        )
    elif list_error is not None:
        custom.list_cluster_custom_object.side_effect = list_error
    api_client = MagicMock()
    if resource_list_error is not None:
        api_client.call_api.side_effect = resource_list_error
    else:
        resources = [{"name": name} for name in (resource_names or ())]
        api_client.call_api.return_value = ({"resources": resources}, 200, None)
    monkeypatch.setattr(
        cluster,
        "client",
        MagicMock(CustomObjectsApi=lambda _ac: custom),
    )
    monkeypatch.setattr(cluster, "_KUBE_AVAILABLE", True, raising=False)
    return api_client


def test_refused_list_falls_back_to_the_resource_list(monkeypatch):
    api_client = _patch(
        monkeypatch, list_status=403, resource_names=("clusterversions",)
    )

    assert cluster.is_openshift(api_client)
    # the generated apis always authenticate; a bare call_api would send
    # the request anonymously and fail for token users
    assert api_client.call_api.call_args.kwargs["auth_settings"] == ["BearerToken"]


@pytest.mark.parametrize(
    "kwargs",
    [
        # a plain cluster where some operator installed another
        # config.openshift.io CRD
        {"list_status": 403, "resource_names": ("infrastructures",)},
        # the group itself is absent
        {"list_status": 403, "resource_list_error": cluster.ApiException(404)},
        # discovery is broken
        {"list_status": 403, "resource_list_error": cluster.ApiException(503)},
        # the clusterversions resource is absent
        {"list_status": 404, "resource_names": ("clusterversions",)},
        # the list call itself did not reach the server
        {"list_error": RuntimeError("connection reset")},
    ],
)
def test_anything_but_a_refusal_means_not_openshift(monkeypatch, kwargs):
    api_client = _patch(monkeypatch, **kwargs)

    assert not cluster.is_openshift(api_client)
    if kwargs.get("list_status") == 404:
        api_client.call_api.assert_not_called()


def test_allowed_clusterversion_list_short_circuits(monkeypatch):
    api_client = _patch(monkeypatch)

    assert cluster.is_openshift(api_client)
    api_client.call_api.assert_not_called()


def test_stale_check_keeps_the_stored_context_when_the_read_fails():
    from pathlib import Path

    # a missing kubectl/oc reads as failure, not as a different cluster
    class _Cmd:
        def kube(self, *args, **_kwargs):
            if "--kubeconfig" in args:
                return SimpleNamespace(success=False, stdout="", stderr="boom")
            return SimpleNamespace(success=True, stdout="{}", stderr="")

    assert not cluster._is_context_stale(_Cmd(), Path("/tmp/ctx"), object())
