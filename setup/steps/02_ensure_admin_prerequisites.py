#!/usr/bin/env python3

import os
import sys
import subprocess
import tempfile
import re
import pykube
from pathlib import Path

# Add project root to path for imports
current_file = Path(__file__).resolve()
project_root = current_file.parents[1]
sys.path.insert(0, str(project_root))

try:
    from functions import (
        announce,
        llmdbench_execute_cmd,
        environment_variable_to_dict,
        kube_connect,
        kubectl_get,
        kubectl_apply,
        is_openshift,
        add_scc_to_service_account,
        ensure_user_workload_monitoring,
    )
except ImportError as e:
    # Fallback for when dependencies are not available
    print(f"Warning: Could not import required modules: {e}")
    print("This script requires the llm-d environment to be properly set up.")
    print("Please run: ./setup/install_deps.sh")
    sys.exit(1)

try:
    from kubernetes import client, config
    import requests
except ImportError as e:
    print(f"Warning: Could not import required modules: {e}")
    print("Please install required dependencies: pip install kubernetes requests")
    sys.exit(1)

GATEWAY_API_CRDS = [
    "gatewayclasses.gateway.networking.k8s.io",
    "gateways.gateway.networking.k8s.io",
    "grpcroutes.gateway.networking.k8s.io",
    "httproutes.gateway.networking.k8s.io",
    "referencegrants.gateway.networking.k8s.io",
]

GATEWAY_API_EXTENSION_CRDS = [
    "inferenceobjectives.inference.networking.k8s.io",
    "inferencepoolimports.inference.networking.k8s.io",
    "inferencepools.inference.networking.k8s.io",
]

KGATEWAY_CRDS = [
    "backends.gateway.kgateway.dev",
    "directresponses.gateway.kgateway.dev",
    "gatewayextensions.gateway.kgateway.dev",
    "gatewayparameters.gateway.kgateway.dev",
    "httplistenerpolicies.gateway.kgateway.dev",
    "trafficpolicies.gateway.kgateway.dev",
]

ISTIO_CRDS = [
    "authorizationpolicies.security.istio.io",
    "destinationrules.networking.istio.io",
    "envoyfilters.networking.istio.io",
    "gateways.networking.istio.io",
    "peerauthentications.security.istio.io",
    "proxyconfigs.networking.istio.io",
    "requestauthentications.security.istio.io",
    "sidecars.networking.istio.io",
    "telemetries.telemetry.istio.io",
    "virtualservices.networking.istio.io",
    "wasmplugins.extensions.istio.io",
    "workloadgroups.networking.istio.io",
]

LWS_CRDS = [
    "leaderworkersets.leaderworkerset.x-k8s.io",
]


def any_crds_missing(expected_crds: list, existing_crds: list) -> bool:
    """Return True if any of the expected CRDs are not in the existing list."""
    return not set(expected_crds).issubset(existing_crds)


def ensure_helm_repository(
    helm_cmd: str, chart_name: str, repo_url: str, dry_run: bool, verbose: bool
) -> int:
    """
    Ensure helm repository is added and updated.

    Args:
        helm_cmd: Helm command to use
        chart_name: Name of the chart/repository
        repo_url: URL of the helm repository
        dry_run: If True, only print what would be executed
        verbose: If True, print detailed output

    Returns:
        int: 0 for success, non-zero for failure
    """
    # Add helm repository
    add_cmd = f"{helm_cmd} repo add {chart_name} {repo_url} --force-update"
    result = llmdbench_execute_cmd(
        actual_cmd=add_cmd, dry_run=dry_run, verbose=verbose, silent=not verbose
    )
    if result != 0:
        announce(f"ERROR: Failed to add helm repository (exit code: {result})")
        return result

    # Update helm repositories
    update_cmd = f"{helm_cmd} repo update"
    result = llmdbench_execute_cmd(
        actual_cmd=update_cmd, dry_run=dry_run, verbose=verbose, silent=not verbose
    )
    if result != 0:
        announce(f"ERROR: Failed to update helm repositories (exit code: {result})")
        return result

    return 0


def get_latest_chart_version(
    helm_cmd: str, helm_repo: str, dry_run: bool, verbose: bool
) -> str:
    """
    Get the latest version of a helm chart from repository.

    Args:
        helm_cmd: Helm command to use
        helm_repo: Name of the helm repository
        dry_run: If True, return placeholder version
        verbose: If True, print detailed output

    Returns:
        str: Latest chart version or empty string if not found
    """
    if dry_run:
        announce("---> would search helm repository for latest chart version")
        return "dry-run-version"

    try:
        # Run helm search repo command
        search_cmd = f"{helm_cmd} search repo {helm_repo}"
        result = subprocess.run(
            search_cmd.split(),
            capture_output=True,
            shell=True,
            executable="/bin/bash",
            text=True,
            timeout=30,
        )

        if result.returncode != 0:
            if verbose:
                announce(f"ERROR: Helm search failed: {result.stderr}")
            return ""

        lines = result.stdout.strip().split("\n")
        if len(lines) < 2:
            return ""

        last_line = lines[-1]
        parts = last_line.split()
        if len(parts) >= 2:
            version = parts[1]
            if verbose:
                announce(f"---> found chart version: {version}")
            return version

        return ""

    except subprocess.TimeoutExpired:
        announce("❌ Helm search command timed out")
        return ""
    except Exception as e:
        announce(f"❌ Error searching for chart version: {e}")
        return ""


def install_gateway_api_crds(
    ev: dict, dry_run: bool, verbose: bool, should_install: bool
) -> int:
    """
    Install Gateway API crds.

    Args:
        ev: Environment variables dictionary
        dry_run: If True, only print what would be executed
        verbose: If True, print detailed output

    Returns:
        int: 0 for success, non-zero for failure
    """
    ecode = 0
    announce(
        f"🚀 Installing Kubernetes Gateway API ({ev['gateway_api_crd_revision']}) CRDs..."
    )
    if should_install:
        install_crds_cmd = f"{ev['control_kcmd']} apply -k https://github.com/kubernetes-sigs/gateway-api/config/crd/?ref={ev['gateway_api_crd_revision']}"
        ecode = llmdbench_execute_cmd(
            actual_cmd=install_crds_cmd,
            dry_run=ev["control_dry_run"],
            verbose=ev["control_verbose"],
        )
        if ecode != 0:
            announce(
                f'ERROR: Failed while running "{install_crds_cmd}" (exit code: {ecode})'
            )
        else:
            announce(
                f"✅ Kubernetes Gateway API ({ev['gateway_api_crd_revision']}) CRDs installed"
            )
    else:
        announce(
            f"✅ Kubernetes Gateway API (unknown version) CRDs already installed (*.gateway.networking.k8s.io CRDs found)"
        )

    return ecode


def install_gateway_api_extension_crds(
    ev: dict, dry_run: bool, verbose: bool, should_install: bool
) -> int:
    """
    Install Gateway API inference extension crds.

    Args:
        ev: Environment variables dictionary
        dry_run: If True, only print what would be executed
        verbose: If True, print detailed output

    Returns:
        int: 0 for success, non-zero for failure
    """
    ecode = 0
    announce(
        f"🚀 Installing Kubernetes Gateway API inference extension ({ev['gateway_api_inference_extension_crd_revision']}) CRDs..."
    )
    if should_install:
        install_crds_cmd = f"{ev['control_kcmd']} apply -k https://github.com/kubernetes-sigs/gateway-api-inference-extension/config/crd/?ref={ev['gateway_api_inference_extension_crd_revision']}"
        ecode = llmdbench_execute_cmd(
            actual_cmd=install_crds_cmd,
            dry_run=ev["control_dry_run"],
            verbose=ev["control_verbose"],
        )
        if ecode != 0:
            announce(
                f'ERROR: Failed while running "{install_crds_cmd}" (exit code: {ecode})'
            )
        announce(
            f"✅ Kubernetes Gateway API inference extension CRDs {ev['gateway_api_inference_extension_crd_revision']} installed"
        )
    else:
        announce(
            f"✅ Kubernetes Gateway API inference extension (unknown version) CRDs already installed (*.inference.networking.x-k8s.io CRDs found)"
        )

    return ecode


def install_kgateway(
    ev: dict, dry_run: bool, verbose: bool, should_install: bool
) -> int:
    """
    Install gateway control plane.
    Uses helmfile from: https://raw.githubusercontent.com/llm-d-incubation/llm-d-infra/refs/heads/main/quickstart/gateway-control-plane-providers/kgateway.helmfile.yaml

    Args:
        ev: Environment variables dictionary
        dry_run: If True, only print what would be executed
        verbose: If True, print detailed output

    Returns:
        int: 0 for success, non-zero for failure
    """
    try:
        helm_base_dir = Path(ev["control_work_dir"]) / "setup" / "helm"
        helm_base_dir.mkdir(parents=True, exist_ok=True)
        helmfile_path = helm_base_dir / f'helmfile-{ev["current_step"]}.yaml'
        with open(helmfile_path, "w") as f:
            ns = ev["gateway_provider_kgateway_namespace"]
            f.write(
                f"""
releases:
  - name: kgateway-crds
    chart: {ev["gateway_provider_kgateway_helm_repository_url"]}/kgateway-crds
    namespace: {ns}
    version: {ev["gateway_provider_kgateway_chart_version"]}
    installed: true
    labels:
      type: gateway-provider
      kind: gateway-crds

  - name: kgateway
    chart: {ev["gateway_provider_kgateway_helm_repository_url"]}/kgateway
    version: {ev["gateway_provider_kgateway_chart_version"]}
    namespace: {ns}
    installed: true
    needs:
      - {ns}/kgateway-crds
    values:
      - inferenceExtension:
          enabled: true
        securityContext:
          allowPrivilegeEscalation: false
          capabilities:
            drop: ["ALL"]
        podSecurityContext:
          seccompProfile:
            type: "RuntimeDefault"
          runAsNonRoot: true
    labels:
      type: gateway-provider
      kind: gateway-control-plane
"""
            )

    except Exception as e:
        announce(f'ERROR: Unable to create helmfile "{helmfile_path}"')
        return 1

    ecode = 0

    announce(
        f"🚀 Installing kgateway helm charts from {ev['gateway_provider_kgateway_helm_repository_url']} ({ev['gateway_provider_kgateway_chart_version']})"
    )
    if should_install:
        install_cmd = f"helmfile apply -f {helmfile_path}"
        ecode = llmdbench_execute_cmd(
            actual_cmd=install_cmd,
            dry_run=ev["control_dry_run"],
            verbose=ev["control_verbose"],
        )
        if ecode != 0:
            announce(
                f'ERROR: Failed while running "{install_cmd}" (exit code: {ecode})'
            )
        announce(
            f"✅ kgateway ({ev['gateway_provider_kgateway_chart_version']}) installed"
        )
    else:
        announce(
            f"✅ kgateway (unknown version) already installed (*.kgateway.dev CRDs found)"
        )

    return ecode


def install_istio(ev: dict, dry_run: bool, verbose: bool, should_install: bool) -> int:
    """
    Install gateway control plane.

    Args:
        ev: Environment variables dictionary
        dry_run: If True, only print what would be executed
        verbose: If True, print detailed output

    Returns:
        int: 0 for success, non-zero for failure
    """
    try:
        helm_base_dir = Path(ev["control_work_dir"]) / "setup" / "helm"
        helm_base_dir.mkdir(parents=True, exist_ok=True)
        helmfile_path = helm_base_dir / f'helmfile-{ev["current_step"]}.yaml'
        with open(helmfile_path, "w") as f:
            ns = ev["gateway_provider_istio_namespace"]
            f.write(
                f"""
repositories:
  - name: istio
    url: {ev["gateway_provider_istio_helm_repository_url"]}
releases:
  - name: istio-base
    chart: istio/base
    version: {ev["gateway_provider_istio_chart_version"]}
    namespace: {ns}
    installed: true
    labels:
      type: gateway-provider
      kind: gateway-crds

  - name: istiod
    chart: istio/istiod
    version: {ev["gateway_provider_istio_chart_version"]}
    namespace: {ns}
    installed: true
    needs:
      - {ns}/istio-base
    values:
      - meshConfig:
          defaultConfig:
            proxyMetadata:
              ENABLE_GATEWAY_API_INFERENCE_EXTENSION: true
        pilot:
          env:
            ENABLE_GATEWAY_API_INFERENCE_EXTENSION: true
        tag: {ev["gateway_provider_istio_chart_version"]}
        hub: "docker.io/istio"
    labels:
      type: gateway-provider
      kind: gateway-control-plane
"""
            )

    except Exception as e:
        announce(f'ERROR: Unable to create helmfile "{helmfile_path}"')
        return 1

    ecode = 0
    if should_install:
        install_cmd = f"helmfile apply -f {helmfile_path}"

        announce(
            f"🚀 Installing istio helm charts from {ev['gateway_provider_istio_helm_repository_url']} ({ev['gateway_provider_istio_chart_version']})"
        )
        ecode = llmdbench_execute_cmd(
            actual_cmd=install_cmd,
            dry_run=ev["control_dry_run"],
            verbose=ev["control_verbose"],
        )
        if ecode != 0:
            announce(
                f'ERROR: Failed while running "{install_cmd}" (exit code: {ecode})'
            )
        announce(f"✅ istio ({ev['gateway_provider_istio_chart_version']}) installed")
    else:
        announce(
            f"✅ istio (unknown version) already installed (*.istio.io CRDs found)"
        )

    return ecode


def install_lws(ev: dict, dry_run: bool, verbose: bool, should_install: bool) -> int:
    """
    Install LeaderWorkerSet (LWS) controller via helm.
    Required for multi-node model deployments.

    Args:
        ev: Environment variables dictionary
        dry_run: If True, only print what would be executed
        verbose: If True, print detailed output
        should_install: If True, perform the installation; otherwise skip

    Returns:
        int: 0 for success, non-zero for failure
    """
    ecode = 0
    announce(
        f"🚀 Installing LeaderWorkerSet (LWS) controller ({ev['lws_chart_version']})..."
    )
    if should_install:
        install_cmd = (
            f"{ev['control_hcmd']} install lws oci://registry.k8s.io/lws/charts/lws"
            f" --version={ev['lws_chart_version']}"
            f" --namespace {ev['lws_namespace']}"
            f" --create-namespace"
            f" --wait --timeout 300s"
        )
        ecode = llmdbench_execute_cmd(
            actual_cmd=install_cmd,
            dry_run=ev["control_dry_run"],
            verbose=ev["control_verbose"],
        )
        if ecode != 0:
            # If install fails, it might already exist but with a different release - try upgrade
            announce(
                f"⚠️ helm install failed (exit code: {ecode}), attempting helm upgrade..."
            )
            upgrade_cmd = (
                f"{ev['control_hcmd']} upgrade lws oci://registry.k8s.io/lws/charts/lws"
                f" --version={ev['lws_chart_version']}"
                f" --namespace {ev['lws_namespace']}"
                f" --wait --timeout 300s"
            )
            ecode = llmdbench_execute_cmd(
                actual_cmd=upgrade_cmd,
                dry_run=ev["control_dry_run"],
                verbose=ev["control_verbose"],
            )
            if ecode != 0:
                announce(f"ERROR: Failed to install/upgrade LWS (exit code: {ecode})")
                return ecode
        announce(
            f"✅ LeaderWorkerSet (LWS) controller ({ev['lws_chart_version']}) installed"
        )
    else:
        announce(
            f"✅ LeaderWorkerSet (LWS) controller (unknown version) already installed (leaderworkersets.leaderworkerset.x-k8s.io CRD found)"
        )

    return ecode


def ensure_namespaces(
    api: pykube.HTTPClient,
    ev: dict,
    dry_run: bool,
) -> int:
    """
    Pre-create namespaces that the pipeline will need.
    Uses kubectl_apply which is idempotent (create or update).

    Args:
        api: pykube HTTP client
        ev: Environment variables dictionary
        dry_run: If True, only print what would be executed

    Returns:
        int: 0 for success, non-zero for failure
    """
    for ns_key in ["vllm_common_namespace", "harness_namespace"]:
        ns_name = ev.get(ns_key, "")
        if not ns_name:
            continue
        announce(f'🔍 Ensuring namespace "{ns_name}" exists...')
        namespace_yaml = f"""apiVersion: v1
kind: Namespace
metadata:
  name: {ns_name}
  namespace: {ns_name}
"""
        kubectl_apply(api=api, manifest_data=namespace_yaml, dry_run=dry_run)

    announce("✅ Namespaces prepared.")
    return 0


def ensure_scc_assignments(
    api: pykube.HTTPClient,
    ev: dict,
    dry_run: bool,
) -> int:
    """
    Assign required SCCs to the common service account on OpenShift.
    Skips silently on non-OpenShift clusters.
    Uses add_scc_to_service_account which is idempotent.

    Args:
        api: pykube HTTP client
        ev: Environment variables dictionary
        dry_run: If True, only print what would be executed

    Returns:
        int: 0 for success, non-zero for failure
    """
    if not is_openshift(api):
        announce("⏭️ Not an OpenShift cluster, skipping SCC assignments.")
        return 0

    sa = ev["vllm_common_service_account"]
    ns = ev["vllm_common_namespace"]

    for scc_name in ["anyuid", "privileged"]:
        add_scc_to_service_account(api, scc_name, sa, ns, dry_run)

    announce(
        f'✅ SCC assignments for service account "{sa}" in namespace "{ns}" complete.'
    )
    return 0


def ensure_monitoring(
    api: pykube.HTTPClient,
    ev: dict,
    dry_run: bool,
    verbose: bool,
) -> int:
    """
    Ensure OpenShift user workload monitoring is configured.
    Only applies to OpenShift clusters with modelservice deployments.

    Args:
        api: pykube HTTP client
        ev: Environment variables dictionary
        dry_run: If True, only print what would be executed
        verbose: If True, print detailed output

    Returns:
        int: 0 for success, non-zero for failure
    """
    if not ev.get("control_environment_type_modelservice_active"):
        return 0

    return ensure_user_workload_monitoring(
        api=api,
        ev=ev,
        work_dir=ev["control_work_dir"],
        current_step=ev["current_step"],
        kubectl_cmd=ev["control_kcmd"],
        dry_run=dry_run,
        verbose=verbose,
    )


def install_gateway_control_plane(
    ev: dict,
    crds: list,
    dry_run: bool,
    verbose: bool,
) -> int:
    """
    Install gateway control plane.

    Args:
        ev: Environment variables dictionary
        dry_run: If True, only print what would be executed
        verbose: If True, print detailed output

    Returns:
        int: 0 for success, non-zero for failure
    """
    provider = ev["vllm_modelservice_gateway_class_name"]

    if provider == "kgateway":
        should_install = any_crds_missing(KGATEWAY_CRDS, crds)
        success = install_kgateway(ev, dry_run, verbose, should_install)
    elif provider == "istio":
        should_install = any_crds_missing(ISTIO_CRDS, crds)
        success = install_istio(ev, dry_run, verbose, should_install)
    elif provider == "gke":
        success = 0
    else:
        success = 0

    if success == 0:
        announce(f"✅ Gateway control plane (provider {provider}) installed.")
    else:
        announce(f"ERROR: Gateway control plane (provider {provider}) not installed.")
    return success


def ensure_admin_prerequisites(
    api: pykube.HTTPClient, ev: dict, dry_run: bool, verbose: bool
) -> int:
    """
    Main function to ensure all admin-level cluster prerequisites are met.
    Consolidates CRD installations, system helm charts, namespace creation,
    SCC assignments, and monitoring configuration.

    Args:
        api: pykube HTTP client
        ev: Environment variables dictionary
        dry_run: If True, only print what would be executed
        verbose: If True, print detailed output

    Returns:
        int: 0 for success, non-zero for failure
    """

    if ev["control_environment_type_modelservice_active"]:

        # Ensure helm repository
        result = ensure_helm_repository(
            ev["control_hcmd"],
            ev["vllm_modelservice_chart_name"],
            ev["vllm_modelservice_helm_repository_url"],
            dry_run,
            verbose,
        )
        if result != 0:
            return result

        if not dry_run:
            # Auto-detect chart version if needed
            if ev["vllm_modelservice_chart_version"] == "auto":
                detected_version = get_latest_chart_version(
                    ev["control_hcmd"],
                    ev["vllm_modelservice_helm_repository"],
                    dry_run,
                    verbose,
                )
                if not detected_version:
                    announce(
                        "❌ Unable to find a version for model service helm chart!"
                    )
                    return 1
                ev["vllm_modelservice_chart_version"] = detected_version

            announce(
                f'🔍 Ensuring gateway infrastructure (provider {ev["vllm_modelservice_gateway_class_name"]}) is setup...'
            )

            if ev["user_is_admin"]:
                _, crd_names = kubectl_get(
                    api=api,
                    object_api="",
                    object_kind="CustomResourceDefinition",
                    object_name="",
                )

                # Install Kubernetes Gateway API CRDs
                result = install_gateway_api_crds(
                    ev, dry_run, verbose, any_crds_missing(GATEWAY_API_CRDS, crd_names)
                )
                if result != 0:
                    return result

                # Install Kubernetes Gateway API inference extension CRDs
                result = install_gateway_api_extension_crds(
                    ev,
                    dry_run,
                    verbose,
                    any_crds_missing(GATEWAY_API_EXTENSION_CRDS, crd_names),
                )
                if result != 0:
                    return result

                # Install Gateway control plane (kgateway, istio or gke)
                result = install_gateway_control_plane(ev, crd_names, dry_run, verbose)
                if result != 0:
                    return result

                # Install LeaderWorkerSet (LWS) if multi-node is enabled
                if ev["vllm_modelservice_multinode"]:
                    result = install_lws(
                        ev, dry_run, verbose, any_crds_missing(LWS_CRDS, crd_names)
                    )
                    if result != 0:
                        return result

            else:
                announce(
                    "❗No privileges to setup gateway infrastructure. Will assume an admin already performed this action."
                )

    if ev["user_is_admin"]:
        announce("🔍 Ensuring monitoring configuration...")
        result = ensure_monitoring(api, ev, dry_run, verbose)
        if result != 0:
            announce("⚠️ Failed to configure monitoring. Continuing...")
    else:
        announce(
            "❗No privileges to configure monitoring. Will assume an admin already performed this action."
        )

    if ev["user_is_admin"]:
        announce("🔍 Pre-creating namespaces...")
        result = ensure_namespaces(api, ev, dry_run)
        if result != 0:
            return result
    else:
        announce(
            "❗No privileges to pre-create namespaces. Will assume namespaces already exist."
        )

    if ev["user_is_admin"]:
        announce("🔍 Ensuring SCC assignments...")
        result = ensure_scc_assignments(api, ev, dry_run)
        if result != 0:
            return result
    else:
        announce(
            "❗No privileges to assign SCCs. Will assume an admin already performed this action."
        )

    return 0


def main():
    """Main function following the pattern from other Python steps"""

    ev = {"current_step_name": os.path.splitext(os.path.basename(__file__))[0]}
    environment_variable_to_dict(ev)

    if ev["control_dry_run"]:
        announce("DRY RUN enabled. No actual changes will be made.")

    api, client = kube_connect(f'{ev["control_work_dir"]}/environment/context.ctx')

    return ensure_admin_prerequisites(
        api, ev, ev["control_dry_run"], ev["control_verbose"]
    )


if __name__ == "__main__":
    sys.exit(main())
