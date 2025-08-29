#!/usr/bin/env python3

import os
import sys
import subprocess
import tempfile
import re
from pathlib import Path

# Add project root to path for imports
current_file = Path(__file__).resolve()
project_root = current_file.parents[1]
sys.path.insert(0, str(project_root))

try:
    from functions import announce, llmdbench_execute_cmd, environment_variable_to_dict
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


def ensure_helm_repository(
    helm_cmd: str,
    chart_name: str,
    repo_url: str,
    dry_run: bool,
    verbose: bool
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
        actual_cmd=add_cmd,
        dry_run=dry_run,
        verbose=verbose,
        silent=not verbose
    )
    if result != 0:
        announce(f"❌ Failed to add helm repository (exit code: {result})")
        return result

    # Update helm repositories
    update_cmd = f"{helm_cmd} repo update"
    result = llmdbench_execute_cmd(
        actual_cmd=update_cmd,
        dry_run=dry_run,
        verbose=verbose,
        silent=not verbose
    )
    if result != 0:
        announce(f"❌ Failed to update helm repositories (exit code: {result})")
        return result

    return 0


def get_latest_chart_version(
    helm_cmd: str,
    helm_repo: str,
    dry_run: bool,
    verbose: bool
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
            timeout=30
        )

        if result.returncode != 0:
            if verbose:
                announce(f"❌ Helm search failed: {result.stderr}")
            return ""

        # Parse output to get version (equivalent to: tail -1 | awk '{print $2}')
        lines = result.stdout.strip().split('\n')
        if len(lines) < 2:  # Need at least header + 1 data line
            return ""

        # Get last line and extract version (second column)
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
        ev : dict,
        dry_run : bool,
        verbose : bool,
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
    try:
        crd_version = ev.get("gateway_api_crd_revision")
        kubectl_cmd = ev.get("control_kcmd", "kubectl")
        install_crds_cmd = f"{kubectl_cmd} apply -k https://github.com/kubernetes-sigs/gateway-api/config/crd/?ref={crd_version}"

        announce(f"🚀 Installing Kubernetes Gateway API ({crd_version}) CRDs...")
        llmdbench_execute_cmd(install_crds_cmd, dry_run, verbose)
        announce("✅ Kubernetes Gateway API CRDs installed")
        return 0

    except Exception as e:
        announce(f"❌ Error installing Kubernetes Gateway API CRDs: {e}")
        return 1


def install_gateway_api_extension_crds(
        ev : dict,
        dry_run : bool,
        verbose : bool,
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
    try:
        crd_version = ev.get("gateway_api_inference_extension_crd_revision")
        kubectl_cmd = ev.get("control_kcmd", "kubectl")
        install_crds_cmd = f"{kubectl_cmd} apply -k https://github.com/kubernetes-sigs/gateway-api-inference-extension/config/crd/?ref={crd_version}"

        announce(f"🚀 Installing Kubernetes Gateway API inference extension ({crd_version}) CRDs...")
        llmdbench_execute_cmd(install_crds_cmd, dry_run, verbose)
        announce("✅ Kubernetes Gateway API inference extension CRDs installed")
        return 0

    except Exception as e:
        announce(f"❌ Error installing Kubernetes Gateway API CRDs: {e}")
        return 1


def install_kgateway(
        ev : dict,
        dry_run : bool,
        verbose : bool,
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
        with open(helmfile_path, 'w') as f:
            f.write("""
releases:
  - name: kgateway-crds
    chart: oci://cr.kgateway.dev/kgateway-dev/charts/kgateway-crds
    namespace: kgateway-system
    version: v2.0.3
    installed: true
    labels:
      type: gateway-provider
      kind: gateway-crds

  - name: kgateway
    chart: oci://cr.kgateway.dev/kgateway-dev/charts/kgateway
    version: v2.0.3
    namespace: kgateway-system
    installed: true
    needs:
      - kgateway-system/kgateway-crds
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
""")
        install_cmd = f"helmfile apply -f {helmfile_path}"

        announce(f"🚀 Installing kgateway")
        llmdbench_execute_cmd(install_cmd, dry_run, verbose)
        announce("✅ kgateway installed")
        return 0

    except Exception as e:
        announce(f"❌ Error installing Kubernetes Gateway API CRDs: {e}")
        return 1

    finally:
        True

def install_istio(
        ev : dict,
        dry_run : bool,
        verbose : bool,
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
    try:
        helm_base_dir = Path(ev["control_work_dir"]) / "setup" / "helm"
        helm_base_dir.mkdir(parents=True, exist_ok=True)
        helmfile_path = helm_base_dir / f'helmfile-{ev["current_step"]}.yaml'
        with open(helmfile_path, 'w') as f:
            f.write("""
releases:
  - name: istio-base
    chart: oci://gcr.io/istio-testing/charts/base
    version: 1.28-alpha.89f30b26ba71bf5e538083a4720d0bc2d8c06401
    namespace: istio-system
    installed: true
    labels:
      type: gateway-provider
      kind: gateway-crds

  - name: istiod
    chart: oci://gcr.io/istio-testing/charts/istiod
    version: 1.28-alpha.89f30b26ba71bf5e538083a4720d0bc2d8c06401
    namespace: istio-system
    installed: true
    needs:
      - istio-system/istio-base
    values:
      - meshConfig:
          defaultConfig:
            proxyMetadata:
              SUPPORT_GATEWAY_API_INFERENCE_EXTENSION: true
        pilot:
          env:
            SUPPORT_GATEWAY_API_INFERENCE_EXTENSION: true
        tag: 1.28-alpha.89f30b26ba71bf5e538083a4720d0bc2d8c06401
        hub: "gcr.io/istio-testing"
    labels:
      type: gateway-provider
      kind: gateway-control-plane
""")

        install_cmd = f"helmfile apply -f {helmfile_path}"

        announce(f"🚀 Installing istio")
        llmdbench_execute_cmd(install_cmd, dry_run, verbose)
        announce("✅ istio installed")
        return 0

    except Exception as e:
        announce(f"❌ Error installing Kubernetes Gateway API CRDs: {e}")
        return 1

    finally:
        True

def install_gateway_control_plane(
        ev : dict,
        dry_run : bool,
        verbose : bool,
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
    if ev["vllm_modelservice_gateway_class_name"] == 'kgateway':
        success = install_kgateway(ev, dry_run, verbose)
    elif ev["vllm_modelservice_gateway_class_name"] == 'istio':
        success = install_istio(ev, dry_run, verbose)
    elif ev["vllm_modelservice_gateway_class_name"] == 'gke':
        success = 0

    if success == 0:
        announce(f'✅ Gateway control plane (provider {ev["vllm_modelservice_gateway_class_name"]}) installed.')
    else:
        announce(f'❌ Gateway control plane (provider {ev["vllm_modelservice_gateway_class_name"]}) not installed.')
    return success


def ensure_gateway_provider(
    ev: dict,
    dry_run: bool,
    verbose: bool
) -> int:
    """
    Main function to ensure gateway provider setup.

    Args:
        ev: Environment variables dictionary
        dry_run: If True, only print what would be executed
        verbose: If True, print detailed output

    Returns:
        int: 0 for success, non-zero for failure
    """

    if not ev["control_environment_type_modelservice_active"]:
        deploy_methods = ev.get("deploy_methods", "unknown")
        announce(f"⏭️ Environment types are \"{deploy_methods}\". Skipping this step.")
        return 0

    # Extract required environment variables
    #FIXME (we shouldn't have to unpack all these variables here)
    helm_cmd = ev.get("control_hcmd", "helm")
    chart_name = ev.get("vllm_modelservice_chart_name", "")
    repo_url = ev.get("vllm_modelservice_helm_repository_url", "")
    chart_version = ev.get("vllm_modelservice_chart_version", "")
    helm_repo = ev.get("vllm_modelservice_helm_repository", "")
    gateway_class = ev.get("vllm_modelservice_gateway_class_name", "")
    release_name = ev.get("vllm_modelservice_release", "")

    # Step 1: Ensure helm repository
    result = ensure_helm_repository(helm_cmd, chart_name, repo_url, dry_run, verbose)
    if result != 0:
        return result

    # Step 2: Handle chart version and infrastructure (only if not dry run)
    if not dry_run:
        # Auto-detect chart version if needed
        if chart_version == "auto":
            detected_version = get_latest_chart_version(helm_cmd, helm_repo, dry_run, verbose)
            if not detected_version:
                announce("❌ Unable to find a version for model service helm chart!")
                return 1
            # Update environment variable for use by other scripts
            os.environ["LLMDBENCH_VLLM_MODELSERVICE_CHART_VERSION"] = detected_version

        # Check gateway infrastructure setup
        announce(f'🔍 Ensuring gateway infrastructure (provider {ev["vllm_modelservice_gateway_class_name"]}) is setup...')

        if ev["user_is_admin"] :
            # Install Kubernetes Gateway API crds
            result = install_gateway_api_crds(ev, dry_run, verbose)
            if result != 0:
                return result

            # Install Kubernetes Gateway API inference extension crds
            result = install_gateway_api_extension_crds(ev, dry_run, verbose)
            if result != 0:
                return result

            # Install Gateway control plane (kgateway, istio or gke)
            result = install_gateway_control_plane(ev, dry_run, verbose)
            if result != 0:
                return result

        else:
            announce("❗No privileges to setup Gateway Provider. Will assume a user with proper privileges already performed this action.")

    return 0


def main():
    """Main function following the pattern from other Python steps"""

    # Set current step name for logging/tracking
    os.environ["LLMDBENCH_CURRENT_STEP"] = os.path.splitext(os.path.basename(__file__))[0]

    ev = {}
    environment_variable_to_dict(ev)

    if ev["control_dry_run"]:
        announce("DRY RUN enabled. No actual changes will be made.")

    # Execute the main logic
    return ensure_gateway_provider(ev, ev["control_dry_run"], ev["control_verbose"])


if __name__ == "__main__":
    sys.exit(main())
