"""Shared helpers for installing the Workload Variant Autoscaler (WVA).

The WVA controller and its runtime dependencies (ServiceAccount, bearer token
Secret, thanos-querier ClusterRole) are cluster/admin-scoped and must be
provisioned *before* any per-stack work runs. KEDA itself is pre-installed by
cluster admins and not managed by this harness. These helpers are called from
``step_03_workload_monitoring`` once per unique ``wva.namespace`` across all
rendered stacks. Per-stack ScaledObject is rendered from
``28_wva-scaledobject.yaml.j2`` and applied in ``step_09``.

Helpers live in this module (rather than in a step class) so both the
admin step and per-stack step can import them without a cyclic dependency.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import yaml

from llmdbenchmark.executor.command import CommandExecutor
from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.standup.keda_prometheus_auth import (
    create_prometheus_auth_secret as _create_prometheus_auth_secret,
    apply_namespace_label,
    _find_yaml,
    _has_yaml_content,
)


def install_wva_for_namespace(  # pylint: disable=too-many-arguments,too-many-locals,unused-argument
    cmd: CommandExecutor,
    context: ExecutionContext,
    plan_config: dict,
    stack_path: Path,
    wva_namespace: str,
    prom_ca_cert: str | None,
    errors: list,
) -> None:
    """Install the WVA controller via kustomize-built upstream manifests.

    Reads ``19_wva-kustomize.yaml`` (a Kustomization wrapper) from
    *stack_path* and applies it with ``kubectl apply -k``. The wrapper's
    ``resources:`` field references the upstream
    ``config/overlays/namespace-scoped/openshift`` overlay over a remote
    git URL, so kustomize fetches the upstream tree at apply time -- no
    local clone needed. The wrapper layers our namespace + image
    overrides on top.
    """
    kustomize_yaml = _find_yaml(stack_path, "19_wva-kustomize")
    if not kustomize_yaml:
        errors.append(
            "WVA kustomization template (19_wva-kustomize) not found "
            "-- cannot install WVA"
        )
        return

    if not _has_yaml_content(kustomize_yaml):
        # Template guarded by `wva.enabled` -- empty content means the
        # flag is off for this stack; nothing to install.
        return

    # `kubectl apply -k <dir>` requires the kustomization file to be
    # named exactly `kustomization.yaml`. Our rendered file uses the
    # numeric prefix convention (`19_wva-kustomize.yaml`); stage a copy
    # under the canonical name in a temp dir so kustomize finds it.
    tmp_dir = Path(tempfile.mkdtemp())
    (tmp_dir / "kustomization.yaml").write_text(
        kustomize_yaml.read_text(encoding="utf-8"), encoding="utf-8"
    )

    context.logger.log_info(
        f"📦 Installing WVA controller via kustomize into ns/{wva_namespace}"
    )
    result = cmd.kube(
        "apply",
        "-k",
        str(tmp_dir),
        check=False,
    )
    if not result.success:
        errors.append(f"Failed to install WVA: {result.stderr}")
        return

    # Wait for the controller pod(s) to actually become Ready before
    # returning, with live ⏳ progress output. Without this, step_03
    # returns success while the controller is still scheduling / pulling
    # images, and downstream steps race against pod startup.
    wait = cmd.wait_for_pods(
        label="control-plane=controller-manager",
        namespace=wva_namespace,
        timeout=300,
        poll_interval=5,
        description=f"WVA controller in ns/{wva_namespace}",
    )
    if not wait.success:
        errors.append(
            f"WVA controller pods did not become Ready in ns/{wva_namespace}: "
            f"{wait.stderr}"
        )


def create_prometheus_auth_secret(
    cmd: CommandExecutor,
    context: ExecutionContext,
    stack_path: Path,
    wva_namespace: str,
    prom_ca_cert: str | None,
    errors: list,
) -> None:
    """Create per-namespace Prometheus bearer token Secret + TriggerAuthentication.

    Wrapper for generic function with WVA-specific defaults (SA name and template stem).
    """
    _create_prometheus_auth_secret(
        cmd,
        context,
        stack_path,
        wva_namespace,
        prom_ca_cert,
        sa_name="wva-prometheus-auth",
        ta_template_stem="21_keda-triggerauthentication",
        errors=errors,
    )


def apply_wva_namespace_label(
    cmd: CommandExecutor, stack_path: Path, wva_namespace: str
) -> None:
    """Apply rendered 23_wva-namespace YAML (Namespace + user-monitoring label)."""
    apply_namespace_label(
        cmd, stack_path, wva_namespace, ns_template_stem="23_wva-namespace"
    )


def stacks_enabling_wva(rendered_stacks: list[Path]) -> list[tuple[Path, dict]]:
    """Return (stack_path, plan_config) pairs for each stack with wva.enabled."""
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
        if cfg.get("wva", {}).get("enabled", False):
            pairs.append((stack_path, cfg))
    return pairs


def unique_wva_namespaces(
    stacks: list[tuple[Path, dict]],
) -> dict[str, tuple[Path, dict]]:
    """Group stacks by their ``wva.namespace`` (falling back to ``namespace.name``).

    Returns a mapping ``{wva_namespace: (first_stack_path, first_plan_config)}``
    so the caller can install the controller once per namespace using that
    stack's rendered values.
    """
    result: dict[str, tuple[Path, dict]] = {}
    for stack_path, cfg in stacks:
        wva_cfg = cfg.get("wva", {})
        wva_ns = wva_cfg.get("namespace") or cfg.get("namespace", {}).get("name", "")
        if not wva_ns:
            continue
        if wva_ns not in result:
            result[wva_ns] = (stack_path, cfg)
    return result


def _require_config(cfg: dict, *keys: str):
    """Navigate dotted config path, raising if any segment is missing."""
    node = cfg
    for key in keys:
        if not isinstance(node, dict) or key not in node:
            dotted = ".".join(keys)
            raise KeyError(f"Required config key missing: {dotted}")
        node = node[key]
    return node
