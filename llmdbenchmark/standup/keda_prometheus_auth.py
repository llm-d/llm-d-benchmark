"""Shared helpers for KEDA Prometheus authentication (bearer token + CA cert).

These functions are generic and used by both WVA and EPP+KEDA autoscaling modes.
They handle extraction of Prometheus CA certs, KEDA CRD verification, and creation
of bearer-token secrets with TriggerAuthentication CRs.
"""

from __future__ import annotations

import base64
import tempfile
from pathlib import Path

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
        except Exception as exc:
            logger.log_warning(f"Failed to decode thanos-querier-tls CA cert: {exc}")
    elif not result.success:
        logger.log_debug(
            f"Could not read secret/thanos-querier-tls in openshift-monitoring: "
            f"{result.stderr.strip()[:300]}"
        )

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
        "Autoscaling will not work until KEDA is installed by a cluster admin. "
        "Install via: helm install keda kedacore/keda -n keda --create-namespace"
    )
    return False


def create_prometheus_auth_secret(
    cmd: CommandExecutor,
    context: ExecutionContext,
    stack_path: Path,
    target_namespace: str,
    prom_ca_cert: str | None,
    sa_name: str = "wva-prometheus-auth",
    ta_template_stem: str = "21_keda-triggerauthentication",
    errors: list | None = None,
) -> None:
    """Create a per-namespace Prometheus bearer token Secret + TriggerAuthentication.

    Mints a token from the specified ServiceAccount, stores it alongside the CA cert
    in a Secret, and applies the TriggerAuthentication CR that KEDA's ScaledObject
    will reference for metric queries.

    Args:
        cmd: CommandExecutor instance.
        context: ExecutionContext instance.
        stack_path: Path to the rendered stack (for finding the TA template).
        target_namespace: Kubernetes namespace for the Secret and TA.
        prom_ca_cert: PEM-encoded CA certificate for Prometheus (optional).
        sa_name: ServiceAccount name to mint the token from
            (default: "wva-prometheus-auth").
        ta_template_stem: Template filename stem to locate the TA YAML
            (default: "21_keda-triggerauthentication").
        errors: List to append error messages to (optional).
    """
    if errors is None:
        errors = []

    if not prom_ca_cert:
        context.logger.log_warning(
            f"Prometheus CA cert is missing for ns/{target_namespace}. "
            "KEDA will not be able to query Prometheus."
        )
        return

    token_result = cmd.kube(
        "create",
        "token",
        sa_name,
        "-n",
        target_namespace,
        check=False,
    )
    if not token_result.success:
        errors.append(
            f"Failed to mint bearer token for ns/{target_namespace}: "
            f"{token_result.stderr}"
        )
        return

    bearer_token = token_result.stdout.strip()
    if not bearer_token:
        errors.append(f"Bearer token is empty for ns/{target_namespace}")
        return

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
        target_namespace,
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
            target_namespace,
            check=False,
        )
        if not apply_result.success:
            errors.append(
                f"Failed to apply prometheus-auth Secret in ns/{target_namespace}: "
                f"{apply_result.stderr}"
            )
    else:
        errors.append(
            f"Failed to generate prometheus-auth Secret for ns/{target_namespace}: "
            f"{secret_result.stderr}"
        )
        return

    ta_yaml = _find_yaml(stack_path, ta_template_stem)
    if not ta_yaml:
        context.logger.log_warning(
            f"TriggerAuthentication template not found for ns/{target_namespace}. "
            "KEDA ScaledObject will fail to authenticate."
        )
        return

    result = cmd.kube("apply", "-f", str(ta_yaml), "-n", target_namespace, check=False)
    if not result.success:
        errors.append(
            f"Failed to apply TriggerAuthentication in ns/{target_namespace}: "
            f"{result.stderr}"
        )


def apply_namespace_label(
    cmd: CommandExecutor,
    stack_path: Path,
    target_namespace: str,
    ns_template_stem: str = "23_wva-namespace",
) -> None:
    """Apply the rendered namespace YAML (Namespace + user-monitoring label)."""
    ns_yaml = _find_yaml(stack_path, ns_template_stem)
    if ns_yaml and _has_yaml_content(ns_yaml):
        cmd.kube("apply", "-f", str(ns_yaml), check=False)


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
