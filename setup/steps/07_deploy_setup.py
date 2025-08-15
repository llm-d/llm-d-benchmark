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
from functions import announce, llmdbench_execute_cmd, model_attribute


def main():
    """Set up helm repositories and create helmfile configurations for model deployments."""
    os.environ["CURRENT_STEP_NAME"] = os.path.splitext(os.path.basename(__file__))[0]
    
    # Parse environment variables
    ev = {}
    for key in dict(os.environ).keys():
        if "LLMDBENCH_" in key:
            ev.update({key.split("LLMDBENCH_")[1].lower(): os.environ.get(key)})
    
    # Check if modelservice environment is active
    if int(ev.get("control_environment_type_modelservice_active", 0)) == 1:
        
        # Add and update helm repository
        announce("🔧 Setting up llm-d-modelservice helm repository...")
        
        # Add helm repository
        helm_repo_add_cmd = (
            f"{ev['control_hcmd']} repo add {ev['vllm_modelservice_chart_name']} "
            f"{ev['vllm_modelservice_helm_repository_url']} --force-update"
        )
        llmdbench_execute_cmd(
            actual_cmd=helm_repo_add_cmd,
            dry_run=int(ev.get("control_dry_run", 0)),
            verbose=int(ev.get("control_verbose", 0))
        )
        
        # Update helm repositories
        helm_repo_update_cmd = f"{ev['control_hcmd']} repo update"
        llmdbench_execute_cmd(
            actual_cmd=helm_repo_update_cmd,
            dry_run=int(ev.get("control_dry_run", 0)),
            verbose=int(ev.get("control_verbose", 0))
        )
        
        # Auto-detect chart version if needed
        if ev.get("vllm_modelservice_chart_version") == "auto":
            announce("🔍 Auto-detecting modelservice chart version...")
            try:
                helm_search_cmd = [
                    ev['control_hcmd'], 'search', 'repo', ev['vllm_modelservice_helm_repository']
                ]
                result = subprocess.run(
                    helm_search_cmd, 
                    capture_output=True, 
                    text=True, 
                    check=False
                )
                
                if result.returncode == 0 and result.stdout.strip():
                    lines = result.stdout.strip().split('\n')
                    if len(lines) > 1:  # Skip header line
                        last_line = lines[-1]
                        version = last_line.split()[1] if len(last_line.split()) > 1 else ""
                        if version:
                            ev["vllm_modelservice_chart_version"] = version
                            os.environ["LLMDBENCH_VLLM_MODELSERVICE_CHART_VERSION"] = version
                            announce(f"📦 Auto-detected chart version: {version}")
                        else:
                            announce("❌ Unable to parse version from helm search output")
                    else:
                        announce("❌ No charts found in helm search output")
                else:
                    announce("❌ Unable to find a version for model service helm chart!")
                    
            except Exception as e:
                announce(f"❌ Error auto-detecting chart version: {e}")
        
        # Create base helm directory structure
        helm_base_dir = Path(ev["control_work_dir"]) / "setup" / "helm" / ev["vllm_modelservice_release"]
        helm_base_dir.mkdir(parents=True, exist_ok=True)
        
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
    url: https://llm-d-incubation.github.io/llm-d-modelservice/

releases:
  - name: infra-{ev['vllm_modelservice_release']}
    namespace: {ev['vllm_common_namespace']}
    chart: {ev['vllm_infra_chart_name']}
    version: {ev['vllm_infra_chart_version']}
    installed: true
    labels:
      managedBy: llm-d-infra-installer

  - name: {ev['vllm_common_namespace']}-{model_id_label}-ms
    namespace: {ev['vllm_common_namespace']}
    chart: {ev['vllm_modelservice_helm_repository']}/{ev['vllm_modelservice_chart_name']}
    version: {ev['vllm_modelservice_chart_version']}
    installed: true
    needs:
      -  {ev['vllm_common_namespace']}/infra-{ev['vllm_modelservice_release']}
    values:
      - {model_num}/ms-values.yaml
    labels:
      managedBy: helmfile

  - name: {ev['vllm_common_namespace']}-{model_id_label}-gaie
    namespace: {ev['vllm_common_namespace']}
    chart: {ev['vllm_gaie_chart_name']}
    version: {ev['vllm_gaie_chart_version']}
    installed: true
    needs:
      -  {ev['vllm_common_namespace']}/infra-{ev['vllm_modelservice_release']}
    values:
      - {model_num}/gaie-values.yaml
    labels:
      managedBy: helmfile
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
        
        announce("✅ Completed gaie deployment")
    else:
        deploy_methods = ev.get("deploy_methods", "")
        announce(f"⏭️ Environment types are \"{deploy_methods}\". Skipping this step.")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())