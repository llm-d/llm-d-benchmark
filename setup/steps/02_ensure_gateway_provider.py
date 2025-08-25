#!/usr/bin/env python3

import os
import sys
import subprocess
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


def setup_gateway_infrastructure(
    infra_dir: str,
    hf_token: str,
    llmd_opts: str,
    dry_run: bool,
    verbose: bool
) -> int:
    """
    Set up gateway infrastructure using llm-d-infra installer.

    Args:
        infra_dir: Path to llm-d-infra directory
        hf_token: Hugging Face token
        llmd_opts: Options to pass to llmd-infra-installer.sh
        dry_run: If True, only print what would be executed
        verbose: If True, print detailed output

    Returns:
        int: 0 for success, non-zero for failure
    """
    infra_path = Path(infra_dir) / "llm-d-infra" / "quickstart"
    installer_script = infra_path / "llmd-infra-installer.sh"

    if not dry_run and not installer_script.exists():
        announce(f"❌ llmd-infra-installer.sh not found at {installer_script}")
        return 1

    # Set up environment and command
    env = os.environ.copy()
    env['HF_TOKEN'] = hf_token
    cmd = f"./llmd-infra-installer.sh {llmd_opts}"

    if verbose:
        announce(f"---> changing to directory: {infra_path}")
        announce(f"---> executing: {cmd}")

    if dry_run:
        announce(f"---> would execute in {infra_path}: {cmd}")
        return 0

    try:
        # Change to quickstart directory and run installer
        original_cwd = os.getcwd()
        os.chdir(infra_path)

        result = subprocess.run(
            cmd,
            shell=True,
            executable="/bin/bash",
            env=env,
            capture_output=not verbose,
            text=True
        )

        # Restore original directory
        os.chdir(original_cwd)

        if result.returncode != 0:
            announce(f"❌ llmd-infra-installer.sh failed (exit code: {result.returncode})")
            if not verbose and result.stderr:
                announce(f"Error output: {result.stderr}")
            return result.returncode

        return 0

    except Exception as e:
        # Ensure we restore directory even on exception
        os.chdir(original_cwd)
        announce(f"❌ Error running llmd-infra-installer.sh: {e}")
        return 1


def check_istio_crds_version(kubectl_cmd: str, dry_run: bool, verbose: bool) -> bool:
    """
    Check if Istio CRDs have v1 API version for workload entries.

    Args:
        kubectl_cmd: kubectl command to use
        dry_run: If True, return placeholder result
        verbose: If True, print detailed output

    Returns:
        bool: True if v1 CRDs exist, False otherwise
    """
    if dry_run:
        announce("---> would check Istio CRD versions")
        return True  # Assume OK in dry run

    try:
        # Equivalent to: kubectl get crd -o "custom-columns=NAME:.metadata.name,VERSIONS:spec.versions[*].name" | grep -E "workload.*istio.*v1,"
        cmd = [
            kubectl_cmd, "get", "crd",
            "-o", "custom-columns=NAME:.metadata.name,VERSIONS:spec.versions[*].name"
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        if result.returncode != 0:
            if verbose:
                announce(f"❌ Failed to get CRDs: {result.stderr}")
            return False

        # Check for workload entries with istio and v1 API
        pattern = r'workload.*istio.*v1,'
        has_v1_crds = bool(re.search(pattern, result.stdout))

        if verbose:
            status = "found" if has_v1_crds else "not found"
            announce(f"---> Istio workload CRDs with v1 API: {status}")

        return has_v1_crds

    except subprocess.TimeoutExpired:
        announce("❌ kubectl get crd command timed out")
        return False
    except Exception as e:
        announce(f"❌ Error checking Istio CRDs: {e}")
        return False


def apply_istio_crds(kubectl_cmd: str, dry_run: bool, verbose: bool) -> int:
    """
    Apply Istio CRDs from GitHub if needed.

    Args:
        kubectl_cmd: kubectl command to use
        dry_run: If True, only print what would be executed
        verbose: If True, print detailed output

    Returns:
        int: 0 for success, non-zero for failure
    """
    crd_url = "https://raw.githubusercontent.com/istio/istio/refs/tags/1.23.1/manifests/charts/base/crds/crd-all.gen.yaml"

    # Use llmdbench_execute_cmd to handle retries and dry run consistently
    cmd = f"{kubectl_cmd} apply -f {crd_url}"

    announce("📜 Applying more recent CRDs (v1.23.1) from istio...")
    result = llmdbench_execute_cmd(
        actual_cmd=cmd,
        dry_run=dry_run,
        verbose=verbose,
        silent=not verbose,
        attempts=3,        # 3 retries as in original
        delay=0            # No retry delay
    )

    if result == 0:
        announce("✅ More recent CRDs from istio applied successfully")
    else:
        announce(f"❌ Failed to apply Istio CRDs (exit code: {result})")

    return result


def ensure_istio_crds(kubectl_cmd: str, dry_run: bool, verbose: bool) -> int:
    """
    Ensure Istio CRDs are up to date.

    Args:
        kubectl_cmd: kubectl command to use
        dry_run: If True, only print what would be executed
        verbose: If True, print detailed output

    Returns:
        int: 0 for success, non-zero for failure
    """
    has_v1_crds = check_istio_crds_version(kubectl_cmd, dry_run, verbose)

    if not has_v1_crds:
        return apply_istio_crds(kubectl_cmd, dry_run, verbose)
    else:
        announce("⏭️  The CRDs from istio present are recent enough, skipping application of newer CRDs")
        return 0


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
    # Check if modelservice is active
    modelservice_active = ev.get("control_environment_type_modelservice_active", "0") == "1"

    if not modelservice_active:
        deploy_methods = ev.get("deploy_methods", "unknown")
        announce(f"⏭️ Environment types are \"{deploy_methods}\". Skipping this step.")
        return 0

    # Extract required environment variables
    #FIXME (we shouldn't have to unpack all these variables here)
    helm_cmd = ev.get("control_hcmd", "helm")
    kubectl_cmd = ev.get("control_kcmd", "kubectl")
    chart_name = ev.get("vllm_modelservice_chart_name", "")
    repo_url = ev.get("vllm_modelservice_helm_repository_url", "")
    chart_version = ev.get("vllm_modelservice_chart_version", "")
    helm_repo = ev.get("vllm_modelservice_helm_repository", "")
    gateway_class = ev.get("vllm_modelservice_gateway_class_name", "")
    release_name = ev.get("vllm_modelservice_release", "")
    common_namespace = ev.get("vllm_common_namespace", "")
    work_dir = ev.get("control_work_dir", ".")
    infra_dir = ev.get("infra_dir", "")
    hf_token = ev.get("hf_token", "")
    user_is_admin = ev.get("user_is_admin", "0") == "1"

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
        announce(f"🔍 Ensuring gateway infrastructure ({gateway_class}) is setup...")

        # Check if helm infra chart exists (equivalent to: helm list | grep infra-$RELEASE)
        try:
            list_cmd = f"{helm_cmd} list"
            result = subprocess.run(list_cmd.split(), capture_output=True, text=True, timeout=30)
            has_infra_chart = f"infra-{release_name}" in result.stdout
        except Exception:
            has_infra_chart = False

        if user_is_admin:
            # Set up llm-d-infra options
            llmd_opts = f"--namespace {common_namespace} --gateway {gateway_class} --context {work_dir}/environment/context.ctx --release infra-{release_name}"
            announce(f"🚀 Calling llm-d-infra with options \"{llmd_opts}\"...")

            # Execute llm-d-infra installer
            result = setup_gateway_infrastructure(infra_dir, hf_token, llmd_opts, dry_run, verbose)
            if result != 0:
                return result

            announce("✅ llm-d-infra prepared namespace")

            # Ensure Istio CRDs are up to date
            result = ensure_istio_crds(kubectl_cmd, dry_run, verbose)
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
