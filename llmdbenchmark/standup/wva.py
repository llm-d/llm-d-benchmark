"""Shared helpers for installing the Workload Variant Autoscaler (WVA).

The WVA controller and its runtime dependencies (ServiceAccount, bearer token
Secret, thanos-querier ClusterRole) are cluster/admin-scoped and must be
provisioned *before* any per-stack work runs. KEDA itself is pre-installed by
cluster admins and not managed by this harness. These helpers are called from
``step_03_workload_monitoring`` once per unique ``wva.namespace`` across all
rendered stacks. Per-stack resources (VariantAutoscaling + ScaledObject) are
rendered from ``27_wva-variantautoscaling.yaml.j2`` / ``28_wva-scaledobject.yaml.j2``
and applied in ``step_09``.

Helpers live in this module (rather than in a step class) so both the
admin step and per-stack step can import them without a cyclic dependency.
"""

from __future__ import annotations

import base64
import tempfile
from pathlib import Path

import yaml

from llmdbenchmark.executor.command import CommandExecutor
from llmdbenchmark.executor.context import ExecutionContext


def extract_prometheus_ca_cert(cmd: CommandExecutor, logger) -> str | None:
    """Extract the Prometheus CA cert from the OpenShift monitoring stack.

    Tries (in order):
      1. ``thanos-querier-tls`` — the main cert used by the upstream WVA guide.
      2. The service-ca ConfigMap injected by OpenShift
         (``openshift-service-ca.crt``) — always readable by any pod/user
         with access to a namespace, so this is a safe fallback when
         secret read is blocked by RBAC.

    Returns the PEM-encoded cert, or ``None`` if nothing works. Logs the
    concrete failure reason so RBAC vs. missing-resource vs. decode-error
    can be told apart at the console.
    """
    # CommandExecutor.kube() concatenates argv with spaces and runs via
    # shell=True, so any backslash in a jsonpath arg is eaten by the shell
    # unless we single-quote the whole thing. `tls.crt` contains a literal
    # dot, which kubectl's jsonpath needs escaped as `tls\.crt`; we wrap
    # in single quotes so both the backslash and dot survive the shell.

    # Try 1: thanos-querier-tls (same source the upstream guide uses)
    result = cmd.kube(
        "get",
        "secret",
        "thanos-querier-tls",
        "--namespace",
        "openshift-monitoring",
        "-o",
        r"'jsonpath={.data.tls\.crt}'",
        check=False,
    )
    if result.success and result.stdout.strip():
        try:
            cert_bytes = base64.b64decode(result.stdout.strip())
            return _ensure_trailing_newline(cert_bytes.decode("utf-8"))
        except Exception as exc:  # noqa: BLE001 -- log and fall through
            logger.log_warning(f"Failed to decode thanos-querier-tls CA cert: {exc}")
    elif not result.success:
        logger.log_debug(
            f"Could not read secret/thanos-querier-tls in openshift-monitoring: "
            f"{result.stderr.strip()[:300]}"
        )

    # Try 2: openshift-service-ca.crt ConfigMap (present in every namespace
    # on OCP, contains the cluster service-ca used to sign internal certs
    # including thanos-querier). Readable by any authenticated user with
    # namespace access — no openshift-monitoring read permission needed.
    result = cmd.kube(
        "get",
        "configmap",
        "openshift-service-ca.crt",
        "-o",
        r"'jsonpath={.data.service-ca\.crt}'",
        check=False,
    )
    if result.success and result.stdout.strip():
        logger.log_info(
            "Using openshift-service-ca.crt ConfigMap as Prometheus CA fallback "
            "(thanos-querier-tls secret was not readable)"
        )
        return _ensure_trailing_newline(result.stdout)

    logger.log_debug(
        f"Could not read openshift-service-ca.crt ConfigMap: "
        f"{result.stderr.strip()[:300]}"
    )
    return None


def _ensure_trailing_newline(cert: str) -> str:
    """Return *cert* with a trailing newline (PEM convention)."""
    cert = cert.strip()
    return cert + "\n" if cert else ""


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


def verify_keda_installed(cmd: CommandExecutor, context: ExecutionContext) -> bool:
    """Verify that KEDA is installed on the cluster.

    Checks for the ScaledObject CRD (a portable probe that doesn't assume
    KEDA's namespace or release name). Logs a warning but returns anyway if
    missing — KEDA is treated as optional shared cluster infra, not required
    by this harness. If missing, the ScaledObjects will fail to reconcile
    and the smoketest will detect it.
    """
    result = cmd.kube(
        "get",
        "crd",
        "scaledobjects.keda.sh",
        check=False,
    )
    if result.success:
        context.logger.log_info("✓ KEDA ScaledObject CRD found")
        return True

    context.logger.log_warning(
        "KEDA is not installed on this cluster (ScaledObject CRD not found). "
        "WVA autoscaling will not work until KEDA is installed by a cluster admin. "
        "Install via: helm install keda kedacore/keda -n keda --create-namespace"
    )
    return False


def create_prometheus_auth_secret(
    cmd: CommandExecutor,
    context: ExecutionContext,
    stack_path: Path,
    wva_namespace: str,
    prom_ca_cert: str | None,
    errors: list,
) -> None:
    """Create a per-namespace Prometheus bearer token Secret + TriggerAuthentication.

    Mints a token from the ServiceAccount created in 23_wva-namespace.yaml.j2,
    stores it alongside the CA cert in a Secret, and applies the TriggerAuthentication
    CR that KEDA's ScaledObject will reference for metric queries.
    """
    if not prom_ca_cert:
        context.logger.log_warning(
            f"Prometheus CA cert is missing for ns/{wva_namespace}. "
            "KEDA will not be able to query Prometheus."
        )
        return

    # Mint a token from the ServiceAccount we created in 23_wva-namespace.yaml.j2
    token_result = cmd.kube(
        "create",
        "token",
        "wva-prometheus-auth",
        "-n",
        wva_namespace,
        check=False,
    )
    if not token_result.success:
        errors.append(
            f"Failed to mint bearer token for ns/{wva_namespace}: {token_result.stderr}"
        )
        return

    bearer_token = token_result.stdout.strip()
    if not bearer_token:
        errors.append(f"Bearer token is empty for ns/{wva_namespace}")
        return

    # Create the prometheus-auth Secret with dry-run + apply (idempotent pattern)
    tmp_dir = Path(tempfile.mkdtemp())
    cert_path = tmp_dir / "ca.crt"
    cert_path.write_text(prom_ca_cert, encoding="utf-8")

    secret_result = cmd.kube(
        "create",
        "secret",
        "generic",
        "prometheus-auth",
        f"--from-file=ca.crt={cert_path}",
        f"--from-literal=bearerToken={bearer_token}",
        "--dry-run=client",
        "-o",
        "yaml",
        "-n",
        wva_namespace,
        check=False,
    )
    if secret_result.success and secret_result.stdout.strip():
        secret_yaml_path = tmp_dir / "prometheus-auth-secret.yaml"
        secret_yaml_path.write_text(secret_result.stdout, encoding="utf-8")
        apply_result = cmd.kube(
            "apply",
            "-f",
            str(secret_yaml_path),
            "-n",
            wva_namespace,
            check=False,
        )
        if not apply_result.success:
            errors.append(
                f"Failed to apply prometheus-auth Secret in ns/{wva_namespace}: "
                f"{apply_result.stderr}"
            )
    else:
        errors.append(
            f"Failed to generate prometheus-auth Secret for ns/{wva_namespace}: "
            f"{secret_result.stderr}"
        )
        return

    # Apply the TriggerAuthentication template
    ta_yaml = _find_yaml(stack_path, "21_keda-triggerauthentication")
    if not ta_yaml:
        context.logger.log_warning(
            f"TriggerAuthentication template not found for ns/{wva_namespace}. "
            "KEDA ScaledObject will fail to authenticate."
        )
        return

    result = cmd.kube("apply", "-f", str(ta_yaml), "-n", wva_namespace, check=False)
    if not result.success:
        errors.append(
            f"Failed to apply TriggerAuthentication in ns/{wva_namespace}: "
            f"{result.stderr}"
        )


def apply_wva_namespace_label(
    cmd: CommandExecutor, stack_path: Path, wva_namespace: str
) -> None:
    """Apply the rendered 23_wva-namespace YAML (Namespace + user-monitoring label)."""
    ns_yaml = _find_yaml(stack_path, "23_wva-namespace")
    if ns_yaml and _has_yaml_content(ns_yaml):
        cmd.kube("apply", "-f", str(ns_yaml), check=False)


def stacks_enabling_wva(rendered_stacks: list[Path]) -> list[tuple[Path, dict]]:
    """Return (stack_path, plan_config) pairs for every rendered stack with wva.enabled."""
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


# --- internal helpers ------------------------------------------------------


def _find_yaml(stack_path: Path, stem_prefix: str) -> Path | None:
    """Locate a rendered YAML under *stack_path* by filename stem prefix."""
    for candidate in stack_path.glob(f"{stem_prefix}*.yaml"):
        return candidate
    return None


def _has_yaml_content(path: Path) -> bool:
    """Return True if *path* contains any non-comment YAML content."""
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8")
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        return True
    return False


def _require_config(cfg: dict, *keys: str):
    """Navigate dotted config path, raising if any segment is missing."""
    node = cfg
    for key in keys:
        if not isinstance(node, dict) or key not in node:
            dotted = ".".join(keys)
            raise KeyError(f"Required config key missing: {dotted}")
        node = node[key]
    return node
