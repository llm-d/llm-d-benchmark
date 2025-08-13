import os
import sys
import yaml
from pathlib import Path

# Add project root to path for imports
current_file = Path(__file__).resolve()
project_root = current_file.parents[1]
sys.path.insert(0, str(project_root))

try:
    from functions import announce, llmdbench_execute_cmd
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
            'config.yaml': 'enableUserWorkload: true\n'
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


def apply_configmap(yaml_file: Path, kubectl_cmd: str, dry_run: bool, verbose: bool) -> int:
    """
    Apply ConfigMap using kubectl/oc command.
    
    Args:
        yaml_file: Path to the YAML file to apply
        kubectl_cmd: kubectl or oc command to use
        dry_run: If True, only print what would be executed
        verbose: If True, print detailed output
    
    Returns:
        int: Command exit code (0 for success)
    """
    cmd = f"{kubectl_cmd} apply -f {yaml_file}"
    
    return llmdbench_execute_cmd(
        actual_cmd=cmd,
        dry_run=dry_run,
        verbose=verbose,
        silent=not verbose
    )


def ensure_user_workload_monitoring(
    is_openshift: bool,
    work_dir: str,
    current_step: str,
    kubectl_cmd: str,
    dry_run: bool,
    verbose: bool
) -> int:
    """
    Ensure OpenShift user workload monitoring is configured using native Python.
    
    Args:
        is_openshift: Whether this is an OpenShift cluster
        work_dir: Working directory for file creation
        current_step: Current step name for file naming
        kubectl_cmd: kubectl or oc command to use
        dry_run: If True, only print what would be executed
        verbose: If True, print detailed output
    
    Returns:
        int: 0 for success, non-zero for failure
    """
    announce("🔍 Checking for OpenShift user workload monitoring enablement...")
    
    if not is_openshift:
        announce("⏭️  Not an OpenShift Cluster, skipping user workload monitoring enablement")
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
    os.environ["CURRENT_STEP_NAME"] = os.path.splitext(os.path.basename(__file__))[0]
    
    # Parse environment variables into ev dictionary (following established pattern)
    ev = {}
    for key, value in os.environ.items():
        if "LLMDBENCH_" in key:
            ev[key.split("LLMDBENCH_")[1].lower()] = value
    
    # Extract required environment variables with defaults
    is_openshift = ev.get("control_deploy_is_openshift", "0") == "1"
    work_dir = ev.get("control_work_dir", ".")
    current_step = ev.get("current_step", os.path.splitext(os.path.basename(__file__))[0])
    kubectl_cmd = ev.get("control_kcmd", "kubectl")
    dry_run = ev.get("control_dry_run") == '1'
    verbose = ev.get("control_verbose") == '1'
    
    if dry_run:
        announce("DRY RUN enabled. No actual changes will be made.")
    
    # Execute the main logic
    return ensure_user_workload_monitoring(
        is_openshift=is_openshift,
        work_dir=work_dir,
        current_step=current_step,
        kubectl_cmd=kubectl_cmd,
        dry_run=dry_run,
        verbose=verbose
    )


if __name__ == "__main__":
    sys.exit(main())