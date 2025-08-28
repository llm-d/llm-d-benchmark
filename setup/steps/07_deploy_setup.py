#!/usr/bin/env python3

import os
import sys
import subprocess
from pathlib import Path

# Add project root to Python path
current_file = Path(__file__).resolve()
project_root = current_file.parents[1]
sys.path.insert(0, str(project_root))

# Import from functions.py
from functions import environment_variable_to_dict, announce, llmdbench_execute_cmd, model_attribute

def gateway_values(provider : str, host: str) -> str:
    if provider == "istio":
        return f"""gateway:
  gatewayClassName: istio
  destinationRule:
    enabled: true
    trafficPolicy:
      tls:
        mode: SIMPLE
        insecureSkipVerify: true
    host: {host}"""
    elif provider == "kgateway":
        return f"""gateway:
  gatewayClassName: kgateway
  service:
    type: NodePort
  destinationRule:
    host: {host}
  gatewayParameters:
    enabled: true
  """
    elif provider == "gke":
        return f"""gateway:
  gatewayClassName: gke-l7-regional-external-managed
  destinationRule: {host}

provider:
  name: gke"""
    else:
        return ""

def auto_detect_version(ev, chart, version_key, repo_key) -> int:
    if ev.get(version_key) == "auto":
        announce(f"🔍 Auto-detecting {chart} chart version...")

        try:
            #FIXME (USE llmdbench_execute_cmd)
            helm_search_cmd = f"{ev['control_hcmd']} search repo {ev[repo_key]}"
            result = subprocess.run(
                helm_search_cmd,
                capture_output=True,
                text=True,
                shell=True,
                executable="/bin/bash",
                check=False
            )

            if result.returncode == 0 and result.stdout.strip():
                lines = result.stdout.strip().split('\n')
                if len(lines) > 1:  # Skip header line
                    last_line = lines[-1]
                    version = last_line.split()[1] if len(last_line.split()) > 1 else ""
                    if version:
                        ev[version_key] = version
                        os.environ[f"LLMDBENCH_{version_key.upper()}"]
                        announce(f"📦 Auto-detected chart version: {version}")
                        return 0
                    else:
                        announce("❌ Unable to parse version from helm search output")
                        return 1
                else:
                    announce("❌ No charts found in helm search output")
                    return 1
            else:
                announce("❌ Unable to find a version for model service helm chart!")
                return 1

        except Exception as e:
            announce(f"❌ Error auto-detecting {chart} chart version: {e}")
            return 1
    return 0

def main():
    """Set up helm repositories and create helmfile configurations for model deployments."""
    os.environ["CURRENT_STEP_NAME"] = os.path.splitext(os.path.basename(__file__))[0]

    # Parse environment variables
    ev = {}
    environment_variable_to_dict(ev)

    # Check if modelservice environment is active
    if ev["control_environment_type_modelservice_active"]:

        # Add and update llm-d-modelservic helm repository
        announce("🔧 Setting up helm repositories ...")

        # Add llm-d-modelservice helm repository
        # TODO make this a function
        helm_repo_add_cmd = (
            f"{ev['control_hcmd']} repo add {ev['vllm_modelservice_chart_name']} "
            f"{ev['vllm_modelservice_helm_repository_url']} --force-update"
        )
        result = llmdbench_execute_cmd(
            actual_cmd=helm_repo_add_cmd,
            dry_run=int(ev.get("control_dry_run", 0)),
            verbose=int(ev.get("control_verbose", 0))
        )
        if result != 0:
            announce(f"❌ Failed setting up llm-d-modelservice helm repository with \"{helm_repo_add_cmd}\" (exit code: {result})")
            exit(result)

        # Add llm-d-infra helm repository
        helm_repo_add_cmd = (
            f"{ev['control_hcmd']} repo add {ev['vllm_infra_chart_name']} "
            f"{ev['vllm_infra_helm_repository_url']} --force-update"
        )
        result = llmdbench_execute_cmd(
            actual_cmd=helm_repo_add_cmd,
            dry_run=int(ev.get("control_dry_run", 0)),
            verbose=int(ev.get("control_verbose", 0))
        )
        if result != 0:
            announce(f"❌ Failed setting up llm-d-infra helm repository with \"{helm_repo_add_cmd}\" (exit code: {result})")
            exit(result)

        # Update helm repositories
        helm_repo_update_cmd = f"{ev['control_hcmd']} repo update"
        result = llmdbench_execute_cmd(
            actual_cmd=helm_repo_update_cmd,
            dry_run=int(ev.get("control_dry_run", 0)),
            verbose=int(ev.get("control_verbose", 0))
        )
        if result != 0:
            announce(f"❌ Failed setting up helm repositories with \"{helm_repo_update_cmd}\" (exit code: {result})")
            exit(result)

        # Auto-detect chart version if needed
        result = auto_detect_version(ev, ev['vllm_modelservice_chart_name'], "vllm_modelservice_chart_version", "vllm_modelservice_helm_repository")
        if 0 != result:
            exit(result)
        result = auto_detect_version(ev, ev['vllm_infra_chart_name'], "vllm_infra_chart_version", "vllm_infra_helm_repository")
        if 0 != result:
            exit(result)

        # Create base helm directory structure
        helm_base_dir = Path(ev["control_work_dir"]) / "setup" / "helm" / ev["vllm_modelservice_release"]
        helm_base_dir.mkdir(parents=True, exist_ok=True)

        # Create infra values file
        infra_value_file = Path(helm_base_dir / "infra.yaml" )
        with open(infra_value_file, 'w') as f:
            f.write(gateway_values(ev['vllm_modelservice_gateway_class_name'], f"gaie-inference-scheduling-epp.{ev['vllm_common_namespace']}.svc.cluster.local"))

        # Process each model
        model_number = 0
        model_list = ev.get("deploy_model_list", "").replace(",", " ").split()

        for model in model_list:
            # Get model attribute
            model_id_label = model_attribute(model, "modelid_label")
            os.environ["LLMDBENCH_DEPLOY_CURRENT_MODEL_ID_LABEL"] = model_id_label

            # Format model number with zero padding
            model_num = f"{model_number:02d}"

            # Create model-specific directory
            model_dir = helm_base_dir / model_num
            model_dir.mkdir(parents=True, exist_ok=True)

            # Generate helmfile YAML content
            helmfile_content = f"""repositories:
  - name: {ev['vllm_modelservice_helm_repository']}
    url: {ev['vllm_modelservice_helm_repository_url']}
  - name: {ev['vllm_infra_helm_repository']}
    url: {ev['vllm_infra_helm_repository_url']}

releases:
  - name: infra-{ev['vllm_modelservice_release']}
    namespace: {ev['vllm_common_namespace']}
    chart: {ev['vllm_infra_helm_repository']}/{ev['vllm_infra_chart_name']}
    version: {ev['vllm_infra_chart_version']}
    installed: true
    labels:
      type: infrastructure
      kind: inference-stack
    values:
      - infra.yaml

  - name: {model_id_label}-ms
    namespace: {ev['vllm_common_namespace']}
    chart: {ev['vllm_modelservice_helm_repository']}/{ev['vllm_modelservice_chart_name']}
    version: {ev['vllm_modelservice_chart_version']}
    installed: true
    needs:
      - {ev['vllm_common_namespace']}/infra-{ev['vllm_modelservice_release']}
      - {ev['vllm_common_namespace']}/{model_id_label}-gaie
    values:
      - {model_num}/ms-values.yaml
    labels:
      kind: inference-stack

  - name: {model_id_label}-gaie
    namespace: {ev['vllm_common_namespace']}
    chart: {ev['vllm_gaie_chart_name']}
    version: {ev['vllm_gaie_chart_version']}
    installed: true
    needs:
      -  {ev['vllm_common_namespace']}/infra-{ev['vllm_modelservice_release']}
    values:
      - {model_num}/gaie-values.yaml
    labels:
      kind: inference-stack
"""

            # Write helmfile configuration
            helmfile_path = helm_base_dir / f"helmfile-{model_num}.yaml"
            with open(helmfile_path, 'w') as f:
                f.write(helmfile_content)

            announce(f"📝 Created helmfile configuration for model {model} ({model_num})")

            # Clean up environment variable
            if "LLMDBENCH_DEPLOY_CURRENT_MODEL_ID_LABEL" in os.environ:
                del os.environ["LLMDBENCH_DEPLOY_CURRENT_MODEL_ID_LABEL"]

            model_number += 1

        announce(f"🚀 Installing helm chart \"infra-{ev['vllm_modelservice_release']}\" via helmfile...")
        install_cmd=f"helmfile --namespace {ev['vllm_common_namespace']} --kubeconfig {ev['control_work_dir']}/environment/context.ctx --selector name=infra-{ev['vllm_modelservice_release']} apply -f {ev['control_work_dir']}/setup/helm/{ev['vllm_modelservice_release']}/helmfile-00.yaml --skip-diff-on-install"
        result = llmdbench_execute_cmd(
            actual_cmd=install_cmd,
            dry_run=int(ev.get("control_dry_run", 0)),
            verbose=int(ev.get("control_verbose", 0))
        )
        if result != 0:
            announce(f"❌ Failed Failed installing chart \"infra-{ev['vllm_modelservice_release']}\" (exit code: {result})")
            exit(result)
        announce(f"✅ chart \"infra-{ev['vllm_modelservice_release']}\" deployed successfully")

        announce("✅ Completed gaie deployment")
    else:
        deploy_methods = ev.get("deploy_methods", "")
        announce(f"⏭️ Environment types are \"{deploy_methods}\". Skipping this step.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
