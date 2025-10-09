import os
import sys
import yaml
from pathlib import Path
import pykube
from pykube.exceptions import PyKubeError

# Add project root to path for imports
current_file = Path(__file__).resolve()
project_root = current_file.parents[1]
sys.path.insert(0, str(project_root))

try:
    from functions import (announce,
                           capacity_planner_sanity_check,
                           check_affinity,
                           llmdbench_execute_cmd,
                           environment_variable_to_dict,
                           kube_connect,
                           apply_configmap,
                           is_openshift)
except ImportError as e:
    # Fallback for when dependencies are not available
    print(f"Warning: Could not import required modules: {e}")
    print("This script requires the llm-d environment to be properly set up.")
    print("Please run: ./setup/install_deps.sh")
    sys.exit(1)


def create_monitoring_configmap() -> dict:
    """
    Create OpenShift monitoring ConfigMap using native Python dict structure.

    Returns:
        dict: ConfigMap structure for enabling user workload monitoring
    """
    return {
        'apiVersion': 'v1',
        'kind': 'ConfigMap',
        'metadata': {
            'name': 'cluster-monitoring-config',
            'namespace': 'openshift-monitoring'
        },
        'data': {
            'config.yaml': 'enableUserWorkload: true'
        }
    }


def write_configmap_yaml(configmap: dict, output_path: Path, dry_run: bool, verbose: bool) -> bool:
    """
    Write ConfigMap to YAML file using Python yaml library.

    Args:
        configmap: ConfigMap dictionary structure
        output_path: Path where to write the YAML file
        dry_run: If True, only print what would be written
        verbose: If True, print detailed output

    Returns:
        bool: True if successful, False otherwise
    """
    if dry_run:
        announce(f"---> would write ConfigMap YAML to {output_path}")
        if verbose:
            yaml_content = yaml.dump(configmap, default_flow_style=False)
            announce(f"YAML content would be:\n{yaml_content}")
        return True

    try:
        # Create directory if needed using native Python
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if verbose:
            announce(f"---> writing ConfigMap YAML to {output_path}")

        # Write YAML using Python yaml library instead of heredoc
        with open(output_path, 'w') as f:
            yaml.dump(configmap, f, default_flow_style=False)

        if verbose:
            announce(f"---> successfully wrote YAML file")

        return True

    except IOError as e:
        announce(f"❌ Failed to write YAML file: {e}")
        return False
    except yaml.YAMLError as e:
        announce(f"❌ Failed to generate YAML: {e}")
        return False


def ensure_user_workload_monitoring(
    api: pykube.HTTPClient,
    ev: dict,
    work_dir: str,
    current_step: str,
    kubectl_cmd: str,
    dry_run: bool,
    verbose: bool
) -> int:
    """
    Ensure OpenShift user workload monitoring is configured using native Python.

    Args:
        api: pykube.HTTPClient
        ev: Environment variables dictionary
        work_dir: Working directory for file creation
        current_step: Current step name for file naming
        kubectl_cmd: kubectl or oc command to use
        dry_run: If True, only print what would be executed
        verbose: If True, print detailed output

    Returns:
        int: 0 for success, non-zero for failure
    """
    announce("🔍 Checking for OpenShift user workload monitoring enablement...")

    if is_openshift(api) :
        if ev["deploy_methods"] != "modelservice" :
            announce("⏭️ Standup method is not \"modelservice\", skipping user workload monitoring enablement")
    else :
        announce("⏭️ Not an OpenShift Cluster, skipping user workload monitoring enablement")
        return 0

    try:
        # Create ConfigMap structure using native Python
        configmap = create_monitoring_configmap()

        # Determine output file path using pathlib
        work_path = Path(work_dir)
        yaml_dir = work_path / "setup" / "yamls"
        yaml_file = yaml_dir / f"{current_step}_cluster-monitoring-config_configmap.yaml"

        # Write YAML file using Python yaml library
        if not write_configmap_yaml(configmap, yaml_file, dry_run, verbose):
            return 1

        # Apply ConfigMap using kubectl/oc
        result = apply_configmap(yaml_file, kubectl_cmd, dry_run, verbose)
        if result != 0:
            announce(f"❌ Failed to apply ConfigMap (exit code: {result})")
            return result

        announce("✅ OpenShift user workload monitoring enabled")
        return 0

    except Exception as e:
        announce(f"❌ Error setting up user workload monitoring: {e}")
        return 1


def main():
    """Main function following the pattern from other Python steps"""

    # Set current step name for logging/tracking
    os.environ["LLMDBENCH_CURRENT_STEP"] = os.path.splitext(os.path.basename(__file__))[0]

    ev = {}
    environment_variable_to_dict(ev)

    env_cmd=f'source "{ev["control_dir"]}/env.sh"'
    result = llmdbench_execute_cmd(actual_cmd=env_cmd, dry_run=ev["control_dry_run"], verbose=ev["control_verbose"])
    if result != 0:
        announce(f"❌ Failed while running \"{env_cmd}\" (exit code: {result})")
        exit(result)

    environment_variable_to_dict(ev)

    api = kube_connect(f'{ev["control_work_dir"]}/environment/context.ctx')
    if ev["control_dry_run"] :
        announce("DRY RUN enabled. No actual changes will be made.")

    # Check affinity
    if not check_affinity(ev):
        announce("❌ Failed to check affinity")
        return 1
    capacity_planner_sanity_check(ev)

    if not ev["control_environment_type_modelservice_active"]:
        deploy_methods = ev.get("deploy_methods", "unknown")
        announce(f"⏭️ Environment types are \"{deploy_methods}\". Skipping this step.")
        return 0

    # Execute the main logic
    return ensure_user_workload_monitoring(
        api=api,
        ev=ev,
        work_dir=ev["control_work_dir"],
        current_step=ev["current_step"],
        kubectl_cmd=ev["control_kcmd"],
        dry_run=ev["control_dry_run"],
        verbose=ev["control_verbose"]
    )

if __name__ == "__main__":
    sys.exit(main())
