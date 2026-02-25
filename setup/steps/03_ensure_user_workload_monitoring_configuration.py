import os
import sys
import yaml
from pathlib import Path

# Add project root to path for imports
current_file = Path(__file__).resolve()
project_root = current_file.parents[1]
sys.path.insert(0, str(project_root))

from functions import (announce,
                        capacity_planner_sanity_check,
                        check_accelerator,
                        check_network,
                        discover_node_resources,
                        llmdbench_execute_cmd,
                        environment_variable_to_dict,
                        kube_connect,
                        kubectl_apply,
                        ensure_user_workload_monitoring,
                        is_openshift)

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


def main():
    """Main function following the pattern from other Python steps"""

    ev = {'current_step_name': os.path.splitext(os.path.basename(__file__))[0] }
    environment_variable_to_dict(ev)

    env_cmd=f'source "{ev["control_dir"]}/env.sh"'
    result = llmdbench_execute_cmd(actual_cmd=env_cmd, dry_run=ev["control_dry_run"], verbose=ev["control_verbose"])
    if result != 0:
        announce(f"❌ Failed while running \"{env_cmd}\" (exit code: {result})")
        exit(result)

    environment_variable_to_dict(ev)

    api, client  = kube_connect(f'{ev["control_work_dir"]}/environment/context.ctx')
    if ev["control_dry_run"] :
        announce("DRY RUN enabled. No actual changes will be made.")

    if not discover_node_resources(ev):
        announce("ERROR: Failed to discover resources on nodes")
        return 1

    if not check_accelerator(ev):
        announce("ERROR: Failed to check accelerator")
        return 1

    if not check_network(ev):
        announce("ERROR: Failed to check network")
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
