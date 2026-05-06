"""Shared helpers for installing the Workload Variant Autoscaler (WVA).

The WVA controller and its runtime dependencies (prometheus-adapter,
prometheus-ca ConfigMap, thanos-querier ClusterRole) are cluster/admin-scoped
and must be provisioned *before* any per-stack work runs. These helpers are
called from ``step_02_admin_prerequisites`` once per unique ``wva.namespace``
across all rendered stacks. Per-stack resources (VariantAutoscaling + HPA)
are rendered from ``27_wva-variantautoscaling.yaml.j2`` /
``28_wva-hpa.yaml.j2`` and applied in ``step_09``.

Helpers live in this module (rather than in a step class) so both the
admin step and per-stack step can import them without a cyclic
dependency.
"""

from __future__ import annotations

import base64
import tempfile
from pathlib import Path

import yaml

from llmdbenchmark.executor.command import CommandExecutor
from llmdbenchmark.executor.context import ExecutionContext


def extract_prometheus_ca_cert(
    cmd: CommandExecutor, logger
) -> str | None:
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


def install_wva_for_namespace(  # pylint: disable=too-many-arguments,too-many-locals
    cmd: CommandExecutor,
    context: ExecutionContext,
    plan_config: dict,
    stack_path: Path,
    wva_namespace: str,
    prom_ca_cert: str | None,
    errors: list,
) -> None:
    """Install the WVA controller helm release into *wva_namespace*.

    Loads the rendered ``19_wva-values.yaml`` from *stack_path*, patches
    the runtime-computed ``caCert`` into it, and runs
    ``helm upgrade --install workload-variant-autoscaler``. Idempotent
    across calls (subsequent calls for the same namespace are a helm
    upgrade).
    """
    wva_values_yaml = _find_yaml(stack_path, "19_wva-values")
    if not wva_values_yaml:
        errors.append(
            "WVA values template (19_wva-values) not found -- cannot install WVA"
        )
        return

    wva_config = yaml.safe_load(wva_values_yaml.read_text(encoding="utf-8"))
    if not wva_config:
        errors.append("WVA values template rendered empty -- is wva.enabled set?")
        return

    if prom_ca_cert and "wva" in wva_config and "prometheus" in wva_config["wva"]:
        wva_config["wva"]["prometheus"]["caCert"] = prom_ca_cert

    tmp_dir = Path(tempfile.mkdtemp())
    wva_values_path = tmp_dir / "wva_config.yaml"
    wva_values_path.write_text(
        yaml.dump(wva_config, sort_keys=False), encoding="utf-8"
    )

    wva_chart = plan_config.get("helmRepositories", {}).get("wva", {})
    chart_url = wva_chart.get("url", "")
    chart_version = plan_config.get("chartVersions", {}).get("wva", "")

    if not (chart_url and chart_version):
        errors.append(
            "WVA chart URL or version not configured -- "
            "check helmRepositories.wva and chartVersions.wva"
        )
        return

    context.logger.log_info(
        f"📦 Installing WVA controller v{chart_version} into ns/{wva_namespace}"
    )
    result = cmd.helm(
        "upgrade",
        "--install",
        "workload-variant-autoscaler",
        chart_url,
        "--version",
        chart_version,
        "--namespace",
        wva_namespace,
        "--create-namespace",
        "-f",
        str(wva_values_path),
    )
    if not result.success:
        errors.append(f"Failed to install WVA: {result.stderr}")
        return

    # Wait for the controller pod(s) to actually become Ready before
    # returning, with live ⏳ progress output (same helper used by
    # step_08 for gateway and step_09 for decode/prefill). Without this,
    # step_03 returns success while the controller is still scheduling
    # / pulling images, and downstream steps race against pod startup.
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


def install_prometheus_adapter(  # pylint: disable=too-many-arguments
    cmd: CommandExecutor,
    context: ExecutionContext,
    plan_config: dict,
    stack_path: Path,
    monitoring_ns: str,
    prom_ca_cert: str,
    errors: list,
) -> None:
    """Install the prometheus-adapter helm chart + prometheus-ca ConfigMap.

    Cluster-scoped dependency for WVA: runs once up front regardless of
    how many model namespaces host WVA controllers.
    """
    tmp_dir = Path(tempfile.mkdtemp())
    cert_path = tmp_dir / "prometheus-ca.crt"
    cert_path.write_text(prom_ca_cert, encoding="utf-8")

    # Create-or-update the prometheus-ca ConfigMap in the monitoring ns.
    result = cmd.kube(
        "create",
        "configmap",
        "prometheus-ca",
        f"--from-file=ca.crt={cert_path}",
        "--dry-run=client",
        "-o",
        "yaml",
        namespace=monitoring_ns,
        check=False,
    )
    if result.success and result.stdout.strip():
        cm_yaml_path = tmp_dir / "prometheus-ca-configmap.yaml"
        cm_yaml_path.write_text(result.stdout, encoding="utf-8")
        apply_result = cmd.kube(
            "apply",
            "-f",
            str(cm_yaml_path),
            namespace=monitoring_ns,
            check=False,
        )
        if not apply_result.success:
            context.logger.log_warning(
                f"prometheus-ca ConfigMap apply failed: {apply_result.stderr}"
            )
    elif not result.success:
        context.logger.log_warning(
            f"prometheus-ca ConfigMap creation failed: {result.stderr}"
        )

    # prometheus-adapter is a cluster-scoped install (it owns a ClusterRole
    # `prometheus-adapter-resource-reader` that can only belong to a single
    # helm release cluster-wide). If another tenant in the cluster already
    # installed it — common on shared OCP clusters — we can't
    # `helm upgrade --install` ours on top without stealing ownership of
    # that ClusterRole, so we reuse the existing release.
    #
    # We intentionally do NOT probe the external-metrics discovery API here
    # to confirm the existing install serves `wva_desired_replicas`: the
    # adapter only lists a metric in discovery once its `seriesQuery`
    # returns at least one matching series from Prometheus, and at
    # admin-install time no VariantAutoscaling exists yet, so no series is
    # being produced. A discovery miss at this point is ambiguous (rule
    # missing vs no VA yet) and previously misled users into "fixing" a
    # non-existent rule problem. Rule correctness is verified later in
    # the smoketest (post-VA-apply) where a discovery miss is unambiguous.
    existing_release, existing_ns = _find_existing_prometheus_adapter_release(cmd)
    if existing_release:
        context.logger.log_info(
            f"ℹ️  prometheus-adapter is already installed cluster-wide "
            f"(release={existing_release!r}, namespace={existing_ns!r}). "
            "Reusing it — rule correctness will be validated by the "
            "smoketest after the VariantAutoscaling is applied."
        )
    else:
        repo_url = _require_config(
            plan_config, "helmRepositories", "prometheusAdapter", "url",
        )
        chart_name = _require_config(
            plan_config, "helmRepositories", "prometheusAdapter", "name",
        )
        repo_alias = "prometheus-community"

        cmd.helm("repo", "add", repo_alias, repo_url, check=False)
        cmd.helm("repo", "update", check=False)

        adapter_values = _find_yaml(stack_path, "21_prometheus-adapter-values")
        if not adapter_values:
            errors.append(
                "prometheus-adapter values template (21_prometheus-adapter-values) "
                "not found"
            )
            return

        # Pin prometheus-adapter chart version (from chartVersions.prometheusAdapter
        # in defaults.yaml) so newer releases can't break the external-metric
        # rule format the WVA chart emits. Upstream README uses this same pin.
        adapter_version = plan_config.get("chartVersions", {}).get(
            "prometheusAdapter", ""
        )

        context.logger.log_info(
            f"📦 Installing prometheus-adapter"
            f"{' v' + adapter_version if adapter_version else ''} "
            f"into ns/{monitoring_ns}"
        )
        version_args = ("--version", adapter_version) if adapter_version else ()
        result = cmd.helm(
            "upgrade",
            "--install",
            "prometheus-adapter",
            f"{repo_alias}/{chart_name}",
            *version_args,
            "--namespace",
            monitoring_ns,
            "--create-namespace",
            "-f",
            str(adapter_values),
        )
        if not result.success:
            errors.append(f"Failed to install prometheus-adapter: {result.stderr}")
        else:
            # Live progress wait so we don't race step_03 ahead of the
            # adapter actually serving the external-metrics API.
            wait = cmd.wait_for_pods(
                label="app.kubernetes.io/name=prometheus-adapter",
                namespace=monitoring_ns,
                timeout=300,
                poll_interval=5,
                description=f"prometheus-adapter in ns/{monitoring_ns}",
            )
            if not wait.success:
                errors.append(
                    f"prometheus-adapter pods did not become Ready in "
                    f"ns/{monitoring_ns}: {wait.stderr}"
                )

    rbac_yaml = _find_yaml(stack_path, "22_prometheus-rbac")
    if rbac_yaml and _has_yaml_content(rbac_yaml):
        result = cmd.kube("apply", "-f", str(rbac_yaml), check=False)
        if not result.success:
            context.logger.log_warning(
                f"ClusterRole creation failed (non-fatal): {result.stderr}"
            )
    else:
        context.logger.log_warning(
            "prometheus RBAC template (22_prometheus-rbac) not found"
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


def _find_existing_prometheus_adapter_release(
    cmd: CommandExecutor,
) -> tuple[str | None, str | None]:
    """Return (release_name, release_namespace) if prometheus-adapter is
    already installed *anywhere* in the cluster, else (None, None).

    Uses the cluster-scoped ``prometheus-adapter-resource-reader``
    ClusterRole as the probe — the chart always creates it with a unique
    name, so its helm ownership annotations point at the one release that
    currently owns the install. Any tenant that ran
    ``helm install prometheus-adapter ...`` (including llm-d-slo-queueing-test,
    kube-prometheus-stack subchart, etc.) will show up here.
    """
    result = cmd.kube(
        "get",
        "clusterrole",
        "prometheus-adapter-resource-reader",
        "-o",
        "jsonpath="
        "'{.metadata.annotations.meta\\.helm\\.sh/release-name}"
        "|{.metadata.annotations.meta\\.helm\\.sh/release-namespace}'",
        check=False,
    )
    if not result.success or not result.stdout.strip():
        return None, None

    payload = result.stdout.strip().strip("'")
    release_name, _, release_ns = payload.partition("|")
    release_name = release_name or None
    release_ns = release_ns or None
    return release_name, release_ns


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
