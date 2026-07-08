"""Helpers for EPP+KEDA saturation autoscaling setup (no WVA controller).

This module orchestrates the installation of EPP+KEDA autoscaling resources:
ServiceMonitor, EPP metrics RBAC, TriggerAuthentication, and per-stack ScaledObject.
No WVA controller, no VariantAutoscaling CR, no prometheus-adapter.

Mirrors the wva.py structure for stack discovery and namespace-level orchestration.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from llmdbenchmark.executor.command import CommandExecutor
from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.standup.keda_prometheus_auth import (
    verify_keda_installed,  # noqa: F401
    extract_prometheus_ca_cert,  # noqa: F401
    create_prometheus_auth_secret,
    apply_namespace_label,
    _find_yaml,
    _has_yaml_content,
)


def stacks_enabling_epp_keda_saturation(
    rendered_stacks: list[Path],
) -> list[tuple[Path, dict]]:
    """Return (stack_path, plan_config) pairs for stacks with eppKedaSaturation.enabled."""
    pairs: list[tuple[Path, dict]] = []
    for stack_path in rendered_stacks:
        cfg_file = stack_path / "config.yaml"
        if not cfg_file.exists():
            continue
        try:
            with open(cfg_file, encoding="utf-8") as fh:
                cfg = yaml.safe_load(fh) or {}
        except (OSError, yaml.YAMLError):
            continue
        if cfg.get("eppKedaSaturation", {}).get("enabled", False):
            pairs.append((stack_path, cfg))
    return pairs


def unique_epp_keda_saturation_namespaces(
    stacks: list[tuple[Path, dict]],
) -> dict[str, tuple[Path, dict]]:
    """Group stacks by eppKedaSaturation.namespace (falling back to namespace.name).

    Returns a mapping {epp_keda_ns: (stack_path, plan_config)} so the caller
    can install infrastructure once per namespace.
    """
    result: dict[str, tuple[Path, dict]] = {}
    for stack_path, cfg in stacks:
        epp_keda_cfg = cfg.get("eppKedaSaturation", {}) or {}
        epp_keda_ns = epp_keda_cfg.get("namespace") or cfg.get("namespace", {}).get(
            "name", ""
        )
        if not epp_keda_ns:
            continue
        if epp_keda_ns not in result:
            result[epp_keda_ns] = (stack_path, cfg)
    return result


def install_epp_keda_saturation_for_namespace(
    cmd: CommandExecutor,
    context: ExecutionContext,
    stack_path: Path,
    epp_keda_namespace: str,
    prom_ca_cert: str | None,
    errors: list,
) -> None:
    """Install EPP+KEDA saturation autoscaling resources for a namespace.

    Applies:
      1. Namespace label (openshift.io/user-monitoring)
      2. Prometheus bearer token Secret
      3. TriggerAuthentication CR
      4. EPP ServiceMonitor + RBAC

    No WVA controller install or VariantAutoscaling — this mode is controller-free.
    """
    context.logger.log_info(
        f"🎯 Setting up EPP+KEDA saturation autoscaling for ns/{epp_keda_namespace}"
    )

    apply_namespace_label(
        cmd, stack_path, epp_keda_namespace, ns_template_stem="23_wva-namespace"
    )

    create_prometheus_auth_secret(
        cmd,
        context,
        stack_path,
        epp_keda_namespace,
        prom_ca_cert,
        sa_name="wva-prometheus-auth",
        ta_template_stem="21_keda-triggerauthentication",
        errors=errors,
    )

    epp_monitoring_yaml = _find_yaml(
        stack_path, "29_epp-keda-saturation-epp-monitoring"
    )
    if not epp_monitoring_yaml:
        context.logger.log_warning(
            f"EPP monitoring template (29_epp-keda-saturation-epp-monitoring) not found for ns/{epp_keda_namespace}. "
            "ServiceMonitor and EPP RBAC will not be applied."
        )
        return

    if not _has_yaml_content(epp_monitoring_yaml):
        return

    context.logger.log_info(
        f"📊 Applying EPP ServiceMonitor and metrics RBAC into ns/{epp_keda_namespace}"
    )
    result = cmd.kube("apply", "-f", str(epp_monitoring_yaml), check=False)
    if not result.success:
        errors.append(
            f"Failed to apply EPP monitoring resources in ns/{epp_keda_namespace}: {result.stderr}"
        )
