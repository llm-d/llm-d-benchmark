"""Tests for Gateway API CRD handling in admin prerequisites."""

from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import yaml

_STEP_PATH = (
    Path(__file__).resolve().parent.parent
    / "llmdbenchmark"
    / "standup"
    / "steps"
    / "step_02_admin_prerequisites.py"
)
_spec = importlib.util.spec_from_file_location(
    "step_02_admin_prerequisites_isolated", _STEP_PATH
)
_module = importlib.util.module_from_spec(_spec)
sys.modules["step_02_admin_prerequisites_isolated"] = _module
_spec.loader.exec_module(_module)
AdminPrerequisitesStep = _module.AdminPrerequisitesStep
_crd_names_from_manifest = _module._crd_names_from_manifest
_crds_match_version = _module._crds_match_version

GATEWAY_CRDS = [
    "gatewayclasses.gateway.networking.k8s.io",
    "httproutes.gateway.networking.k8s.io",
]
INFERENCE_EXTENSION_CRDS = [
    "inferencepools.inference.networking.k8s.io",
    "inferencepoolimports.inference.networking.x-k8s.io",
]


def _crd_manifest(names: list[str]) -> str:
    return "\n---\n".join(
        yaml.safe_dump(
            {
                "apiVersion": "apiextensions.k8s.io/v1",
                "kind": "CustomResourceDefinition",
                "metadata": {"name": name},
            }
        )
        for name in names
    )


@dataclass
class _Result:
    success: bool = True
    stdout: str = ""
    stderr: str = ""


@dataclass
class _Cmd:
    calls: list[tuple[str, ...]] = field(default_factory=list)
    logger: MagicMock = field(default_factory=MagicMock)

    def kube(self, *args: str, **_: Any) -> _Result:
        self.calls.append(tuple(args))
        if args[0] == "kustomize":
            return _Result(stdout=_crd_manifest(GATEWAY_CRDS))
        if args[:2] == ("apply", "--dry-run=client"):
            return _Result(stdout=_crd_manifest(INFERENCE_EXTENSION_CRDS))
        if args[:3] == ("apply", "--server-side", "-k"):
            return _Result(
                success=False,
                stderr=(
                    'Apply failed with 1 conflict: conflict with "kube-addon-manager": '
                    ".metadata.annotations.gateway.networking.k8s.io/bundle-version"
                ),
            )
        return _Result(success=True)

    def helm(self, *args: str, **_: Any) -> _Result:
        self.calls.append(("helm", *args))
        return _Result(success=True)


def _plan_config() -> dict[str, Any]:
    return {
        "gatewayApiCrd": {
            "revision": "v1.5.1",
            "crdUrlTemplate": (
                "github.com/kubernetes-sigs/gateway-api/config/crd?ref={revision}"
            ),
            "inferenceExtensionRevision": "v1.6.0",
            "inferenceExtensionUrlTemplate": (
                "https://example.invalid/{revision}/manifests.yaml"
            ),
        },
        "helmRepositories": {},
        "monitoring": {},
    }


def _context(methods: list[str], cmd: _Cmd) -> MagicMock:
    context = MagicMock()
    context.deployed_methods = methods
    context.dry_run = False
    context.non_admin = False
    context.kustomize_skip_infra = False
    context.require_cmd.return_value = cmd
    context.logger = MagicMock()
    return context


def test_standalone_only_does_not_install_gateway_api_crds() -> None:
    cmd = _Cmd()
    step = AdminPrerequisitesStep()
    step._load_plan_config = MagicMock(return_value=_plan_config())
    step._get_existing_crds = MagicMock(
        return_value=["gatewayclasses.gateway.networking.k8s.io"]
    )
    step._apply_namespace_yaml = MagicMock()
    step._apply_openshift_sccs = MagicMock()

    result = step.execute(_context(["standalone"], cmd))

    assert result.success
    assert ("apply", "--server-side", "-k") not in [call[:3] for call in cmd.calls]


def test_modelservice_installs_missing_gateway_api_crds() -> None:
    cmd = _Cmd()
    step = AdminPrerequisitesStep()

    errors: list[str] = []

    step._install_gateway_api_crds(
        cmd,
        _plan_config(),
        errors,
        existing_crds=[],
    )

    assert ("apply", "--server-side", "-k") in [call[:3] for call in cmd.calls]
    assert errors


def test_get_existing_crds_includes_bundle_versions() -> None:
    cmd = MagicMock()
    cmd.kube.return_value = _Result(
        stdout=json.dumps(
            {
                "items": [
                    {
                        "metadata": {
                            "name": "gateways.gateway.networking.k8s.io",
                            "annotations": {
                                "gateway.networking.k8s.io/bundle-version": "v1.4.0"
                            },
                        }
                    },
                    {
                        "metadata": {
                            "name": "leaderworkersets.leaderworkerset.x-k8s.io",
                            "labels": {"app.kubernetes.io/version": "0.7.0"},
                        }
                    },
                ]
            }
        )
    )
    context = MagicMock(dry_run=False)

    inventory = AdminPrerequisitesStep()._get_existing_crds(cmd, context)

    assert inventory == {
        "gateways.gateway.networking.k8s.io": "v1.4.0",
        "leaderworkersets.leaderworkerset.x-k8s.io": "0.7.0",
    }
    cmd.kube.assert_called_once_with("get", "crd", "-o", "json")


def test_matching_crd_bundle_version_skips_install() -> None:
    cmd = _Cmd()
    step = AdminPrerequisitesStep()
    inventory = dict.fromkeys(GATEWAY_CRDS, "v1.5.1")
    errors: list[str] = []

    step._install_gateway_api_crds(cmd, _plan_config(), errors, inventory)

    assert ("apply", "--server-side", "-k") not in [call[:3] for call in cmd.calls]
    assert errors == []


def test_outdated_crd_bundle_version_warns_without_reinstalling() -> None:
    cmd = _Cmd()
    step = AdminPrerequisitesStep()
    inventory = dict.fromkeys(GATEWAY_CRDS, "v1.4.0")
    errors: list[str] = []

    step._install_gateway_api_crds(cmd, _plan_config(), errors, inventory)

    assert ("apply", "--server-side", "-k") not in [call[:3] for call in cmd.calls]
    assert errors == []
    assert any(
        "version does not match" in call.args[0]
        for call in cmd.logger.log_warning.call_args_list
    )


def test_unknown_crd_version_preserves_existence_only_compatibility() -> None:
    inventory = dict.fromkeys(GATEWAY_CRDS)

    assert _crds_match_version(GATEWAY_CRDS, inventory, "v1.5.1")


def test_helm_chart_version_is_normalized() -> None:
    inventory = {"example.test": "agentgateway-crds-1.2.3"}

    assert _crds_match_version(["example.test"], inventory, "v1.2.3")


def test_crd_names_are_extracted_from_rendered_manifest() -> None:
    manifest = _crd_manifest(GATEWAY_CRDS)
    manifest += "\n---\napiVersion: v1\nkind: Service\nmetadata:\n  name: ignored\n"

    assert _crd_names_from_manifest(manifest) == GATEWAY_CRDS


def test_crd_names_are_extracted_from_kubernetes_list() -> None:
    manifest = yaml.safe_dump(
        {
            "apiVersion": "v1",
            "kind": "List",
            "items": [
                {
                    "apiVersion": "apiextensions.k8s.io/v1",
                    "kind": "CustomResourceDefinition",
                    "metadata": {"name": name},
                }
                for name in INFERENCE_EXTENSION_CRDS
            ],
        }
    )

    assert _crd_names_from_manifest(manifest) == INFERENCE_EXTENSION_CRDS


def test_new_crd_does_not_overwrite_existing_cluster_managed_crds() -> None:
    new_crd = "grpcroutes.gateway.networking.k8s.io"
    cmd = _Cmd()
    step = AdminPrerequisitesStep()
    cmd.kube = MagicMock(
        return_value=_Result(stdout=_crd_manifest([*GATEWAY_CRDS, new_crd]))
    )
    errors: list[str] = []

    step._install_gateway_api_crds(
        cmd,
        _plan_config(),
        errors,
        dict.fromkeys(GATEWAY_CRDS, "v1.5.1"),
    )

    assert cmd.kube.call_args_list[0].args[0] == "kustomize"
    assert cmd.kube.call_count == 1
    assert any(
        new_crd in call.args[0] for call in cmd.logger.log_warning.call_args_list
    )
    assert errors == []


def test_gateway_crd_discovery_failure_preserves_installed_crds() -> None:
    cmd = _Cmd()
    step = AdminPrerequisitesStep()
    cmd.kube = MagicMock(return_value=_Result(success=False))
    errors: list[str] = []

    step._install_gateway_api_crds(
        cmd,
        _plan_config(),
        errors,
        dict.fromkeys(GATEWAY_CRDS, "v1.5.1"),
    )

    assert cmd.kube.call_count == 1
    assert "leaving" in cmd.logger.log_warning.call_args_list[-1].args[0]
    assert errors == []


def test_gateway_crd_discovery_failure_installs_when_group_is_absent() -> None:
    cmd = _Cmd()
    step = AdminPrerequisitesStep()
    cmd.kube = MagicMock(side_effect=[_Result(success=False), _Result(success=True)])
    errors: list[str] = []

    step._install_gateway_api_crds(cmd, _plan_config(), errors, {})

    assert cmd.kube.call_args_list[1].args[:3] == (
        "apply",
        "--server-side",
        "-k",
    )
    assert errors == []


def test_inference_extension_uses_discovered_crds() -> None:
    cmd = _Cmd()
    step = AdminPrerequisitesStep()
    errors: list[str] = []

    step._install_gateway_api_extension_crds(
        cmd,
        _plan_config(),
        errors,
        dict.fromkeys(INFERENCE_EXTENSION_CRDS, "v1.6.0"),
    )

    assert ("apply", "--dry-run=client") in [call[:2] for call in cmd.calls]
    assert ("apply", "-f") not in [call[:2] for call in cmd.calls]
    assert errors == []


def test_inference_extension_discovery_failure_preserves_installed_group() -> None:
    cmd = _Cmd()
    step = AdminPrerequisitesStep()
    cmd.kube = MagicMock(return_value=_Result(success=False))
    errors: list[str] = []

    step._install_gateway_api_extension_crds(
        cmd,
        _plan_config(),
        errors,
        {"inferencepoolimports.inference.networking.x-k8s.io": None},
    )
    assert cmd.kube.call_count == 1
    assert errors == []


def test_epponly_does_not_add_unused_istio_repo() -> None:
    cmd = _Cmd()
    step = AdminPrerequisitesStep()
    errors: list[str] = []
    plan = {
        "gateway": {"className": "epponly"},
        "helmRepositories": {
            "istio": {"url": "https://istio.example/charts"},
            "llmDInfra": {"url": "https://llm-d.example/charts"},
        },
    }

    step._add_helm_repos(cmd, plan, errors)

    helm_calls = [call for call in cmd.calls if call and call[0] == "helm"]
    assert not any("istio" in call for call in helm_calls)
    assert any("llmDInfra" in call for call in helm_calls)
    assert errors == []


def _validate_monitoring_crds(
    plan: dict[str, Any],
    methods: list[str],
    existing_crds: list[str] | None = None,
) -> tuple[_Cmd, list[str]]:
    cmd = _Cmd()
    errors: list[str] = []
    AdminPrerequisitesStep()._validate_monitoring_crds(
        cmd,
        _context(methods, cmd),
        plan,
        existing_crds=existing_crds or [],
        errors=errors,
    )
    return cmd, errors


def test_direct_metrics_scraping_does_not_require_prometheus_operator_crds() -> None:
    plan = {
        "monitoring": {
            "podmonitor": {"enabled": False},
            "metricsScrapeEnabled": True,
        }
    }

    cmd, errors = _validate_monitoring_crds(plan, ["standalone"])

    assert errors == []
    cmd.logger.log_info.assert_not_called()


def test_podmonitor_requires_only_its_own_crd() -> None:
    plan = {"monitoring": {"podmonitor": {"enabled": True}}}

    _, errors = _validate_monitoring_crds(plan, ["standalone"])

    assert len(errors) == 1
    assert "podmonitors.monitoring.coreos.com" in errors[0]
    assert "servicemonitors.monitoring.coreos.com" not in errors[0]


def test_modelservice_router_monitoring_requires_only_servicemonitor_crd() -> None:
    plan = {
        "monitoring": {"podmonitor": {"enabled": False}},
        "router": {"monitoring": {"prometheus": {"enabled": True}}},
    }

    _, errors = _validate_monitoring_crds(plan, ["modelservice"])

    assert len(errors) == 1
    assert "servicemonitors.monitoring.coreos.com" in errors[0]
    assert "podmonitors.monitoring.coreos.com" not in errors[0]


def test_gmp_router_monitoring_does_not_require_servicemonitor_crd() -> None:
    plan = {
        "monitoring": {"podmonitor": {"enabled": False}},
        "router": {
            "monitoring": {
                "provider": {"name": "gmp"},
                "prometheus": {"enabled": True},
            }
        },
    }

    _, errors = _validate_monitoring_crds(plan, ["modelservice"])

    assert errors == []


def test_gke_router_defaults_to_gmp_without_servicemonitor_crd() -> None:
    plan = {
        "monitoring": {"podmonitor": {"enabled": False}},
        "provider": {"name": "gke"},
        "router": {"monitoring": {"prometheus": {"enabled": True}}},
    }

    _, errors = _validate_monitoring_crds(plan, ["modelservice"])

    assert errors == []


def test_unset_router_monitoring_uses_chart_disabled_default() -> None:
    plan = {
        "monitoring": {"podmonitor": {"enabled": False}},
        "router": {},
    }

    _, errors = _validate_monitoring_crds(plan, ["modelservice"])

    assert errors == []


def test_explicit_prometheus_operator_overrides_gke_monitoring_default() -> None:
    plan = {
        "monitoring": {"podmonitor": {"enabled": False}},
        "provider": {"name": "gke"},
        "router": {
            "monitoring": {
                "provider": {"name": "prometheusoperator"},
                "prometheus": {"enabled": True},
            }
        },
    }

    _, errors = _validate_monitoring_crds(plan, ["modelservice"])

    assert len(errors) == 1
    assert "servicemonitors.monitoring.coreos.com" in errors[0]


def test_standalone_does_not_require_router_servicemonitor_crd() -> None:
    plan = {
        "monitoring": {
            "podmonitor": {"enabled": False},
            "metricsScrapeEnabled": True,
        },
        "router": {"monitoring": {"prometheus": {"enabled": True}}},
    }

    _, errors = _validate_monitoring_crds(plan, ["standalone"])

    assert errors == []


def test_both_operator_resources_report_both_missing_crds() -> None:
    plan = {
        "monitoring": {"podmonitor": {"enabled": True}},
        "router": {"monitoring": {"prometheus": {"enabled": True}}},
    }

    _, errors = _validate_monitoring_crds(plan, ["modelservice"])

    assert len(errors) == 1
    assert "podmonitors.monitoring.coreos.com" in errors[0]
    assert "servicemonitors.monitoring.coreos.com" in errors[0]


def test_null_router_monitoring_blocks_do_not_crash_validation() -> None:
    plan = {
        "monitoring": {"podmonitor": {"enabled": False}},
        "router": {"monitoring": None},
    }

    _, errors = _validate_monitoring_crds(plan, ["modelservice"])

    assert errors == []


def test_enabled_operator_resources_accept_their_matching_crds() -> None:
    plan = {
        "monitoring": {"podmonitor": {"enabled": True}},
        "router": {"monitoring": {"prometheus": {"enabled": True}}},
    }
    existing_crds = [
        "podmonitors.monitoring.coreos.com",
        "servicemonitors.monitoring.coreos.com",
    ]

    cmd, errors = _validate_monitoring_crds(
        plan, ["modelservice"], existing_crds=existing_crds
    )

    assert errors == []
    cmd.logger.log_info.assert_called_once_with("✅ Monitoring CRDs present on cluster")


def test_gke_monitoring_guidance_distinguishes_google_podmonitoring() -> None:
    context = MagicMock()
    context.is_gke = True

    guidance = AdminPrerequisitesStep()._monitoring_guidance(context)

    assert "monitoring.googleapis.com/v1 PodMonitoring" in guidance
    assert "Direct harness scraping can remain enabled" in guidance
    assert "natively scrape PodMonitor" not in guidance
