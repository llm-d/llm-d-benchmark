"""A Gateway-mode kustomize guide is reached through its Gateway, not the EPP Service.

Kustomize guides that run the router in Gateway mode (for example
coord-disaggregation, whose clients go Gateway -> Coordinator -> EPP) name their
Gateway ``llm-d-inference-gateway``. The EPP Service carries no model traffic
there, so resolving the endpoint to ``<guide>-epp`` sent the smoketest (and the
benchmark) to a Service that never answers, and would bypass the Coordinator.
EPP-only guides, which have no Gateway, keep the ``<guide>-epp`` endpoint.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from llmdbenchmark.utilities.endpoint import find_kustomize_endpoint


@dataclass
class _Result:
    success: bool
    stdout: str = ""
    stderr: str = ""


@dataclass
class _Cmd:
    gateway: dict | None = None
    epp_ports: list | None = None
    calls: list = field(default_factory=list)

    def kube(self, *args: str, **_: Any) -> _Result:
        self.calls.append(args)
        if args[:3] == ("get", "gateway", "llm-d-inference-gateway"):
            return (
                _Result(True, json.dumps(self.gateway))
                if self.gateway is not None
                else _Result(False, stderr="NotFound")
            )
        if args[:2] == ("get", "service/coord-disaggregation-epp"):
            if self.epp_ports is None:
                return _Result(False, stderr="NotFound")
            return _Result(True, json.dumps({"spec": {"ports": self.epp_ports}}))
        return _Result(False, stderr="unexpected")


def _gateway(address: dict | None, https: bool = False) -> dict:
    listeners = {'k:{"name":"https"}': {}} if https else {'k:{"name":"http"}': {}}
    return {
        "metadata": {
            "managedFields": [{"fieldsV1": {"f:status": {"f:listeners": listeners}}}]
        },
        "status": {"addresses": [address] if address else []},
    }


def test_gateway_hostname_is_the_endpoint():
    cmd = _Cmd(
        gateway=_gateway(
            {
                "type": "Hostname",
                "value": "llm-d-inference-gateway-istio.ns.svc.cluster.local",
            }
        ),
        epp_ports=[{"name": "http", "port": 80, "targetPort": 8081}],
    )
    assert find_kustomize_endpoint(cmd, "ns", "coord-disaggregation") == (
        "llm-d-inference-gateway-istio.ns.svc.cluster.local",
        "llm-d-inference-gateway",
        "80",
    )


def test_gateway_ip_and_https_listener():
    cmd = _Cmd(gateway=_gateway({"type": "IPAddress", "value": "10.0.0.7"}, https=True))
    assert find_kustomize_endpoint(cmd, "ns", "coord-disaggregation") == (
        "10.0.0.7",
        "llm-d-inference-gateway",
        "443",
    )


def test_gateway_without_an_address_is_not_ready_rather_than_the_epp():
    cmd = _Cmd(gateway=_gateway(None), epp_ports=[{"name": "http", "port": 80}])
    ip, name, _ = find_kustomize_endpoint(cmd, "ns", "coord-disaggregation")
    assert ip is None and name == "llm-d-inference-gateway"


def test_no_gateway_keeps_the_epp_service():
    cmd = _Cmd(
        gateway=None, epp_ports=[{"name": "http", "port": 80, "targetPort": 8081}]
    )
    assert find_kustomize_endpoint(cmd, "ns", "coord-disaggregation") == (
        "coord-disaggregation-epp",
        "coord-disaggregation-epp",
        "80",
    )


class _Logger:
    def __init__(self):
        self.messages = []

    def log_info(self, msg, **_):
        self.messages.append(msg)


@dataclass
class _Ctx:
    deployed_endpoints: dict = field(default_factory=dict)
    logger: _Logger = field(default_factory=_Logger)


def test_standup_registers_the_gateway_for_the_smoketest():
    from llmdbenchmark.standup.steps.step_05_kustomize_deploy import KustomizeDeployStep

    cmd = _Cmd(
        gateway=_gateway(
            {
                "type": "Hostname",
                "value": "llm-d-inference-gateway-istio.ns.svc.cluster.local",
            }
        )
    )
    ctx = _Ctx()
    KustomizeDeployStep._register_endpoint(
        cmd, ctx, "ns", "coord-disaggregation", "stack-1"
    )
    assert ctx.deployed_endpoints == {
        "stack-1": "llm-d-inference-gateway-istio.ns.svc.cluster.local"
    }


def test_standup_falls_back_to_the_epp_name_when_nothing_is_found():
    from llmdbenchmark.standup.steps.step_05_kustomize_deploy import KustomizeDeployStep

    ctx = _Ctx()
    KustomizeDeployStep._register_endpoint(
        _Cmd(), ctx, "ns", "coord-disaggregation", "stack-1"
    )
    assert ctx.deployed_endpoints == {"stack-1": "coord-disaggregation-epp"}
