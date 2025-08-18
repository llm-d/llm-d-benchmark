#!/usr/bin/env python3

import os
import sys
from pathlib import Path

# Add project root to Python path
current_file = Path(__file__).resolve()
project_root = current_file.parents[1]
sys.path.insert(0, str(project_root))

# Import from functions.py
from functions import announce, llmdbench_execute_cmd, model_attribute, extract_environment, get_image


def main():
    """Deploy GAIE (Gateway API Inference Extension) components."""
    os.environ["CURRENT_STEP_NAME"] = os.path.splitext(os.path.basename(__file__))[0]
    
    # Parse environment variables
    ev = {}
    for key in dict(os.environ).keys():
        if "LLMDBENCH_" in key:
            ev.update({key.split("LLMDBENCH_")[1].lower(): os.environ.get(key)})
    
    # Check if modelservice environment is active
    if int(ev.get("control_environment_type_modelservice_active", 0)) == 1:
        extract_environment()
        
        model_number = 0
        model_list = ev.get("deploy_model_list", "").replace(",", " ").split()
        
        for model in model_list:
            announce(f"🔄 Processing model {model_number + 1}/{len(model_list)}: {model}")
            
            # Get model attribute
            model_id_label = model_attribute(model, "modelid_label")
            os.environ["LLMDBENCH_DEPLOY_CURRENT_MODEL_ID_LABEL"] = model_id_label
            
            # Format model number with zero padding
            model_num = f"{model_number:02d}"
            
            # Create directory structure
            helm_dir = Path(ev["control_work_dir"]) / "setup" / "helm" / ev["vllm_modelservice_release"] / model_num
            helm_dir.mkdir(parents=True, exist_ok=True)
            
            # Read GAIE presets file content
            presets_path = Path(ev["vllm_modelservice_gaie_presets_full_path"])
            try:
                with open(presets_path, 'r') as f:
                    presets_content = f.read()
                # Indent each line with 6 spaces for YAML formatting
                indented_presets = '\n'.join(f"      {line}" for line in presets_content.splitlines())
            except FileNotFoundError:
                announce(f"⚠️ Warning: GAIE presets file not found at {presets_path}")
                indented_presets = ""
            
            # Get image tag
            image_tag = get_image(
                ev["llmd_inferencescheduler_image_registry"],
                ev["llmd_inferencescheduler_image_repo"], 
                ev["llmd_inferencescheduler_image_name"],
                ev["llmd_inferencescheduler_image_tag"],
                "1"
            )
            
            # Generate GAIE values YAML content
            gaie_values_content = f"""inferenceExtension:
  replicas: 1
  image:
    name: {ev['llmd_inferencescheduler_image_name']}
    hub: {ev['llmd_inferencescheduler_image_registry']}/{ev['llmd_inferencescheduler_image_repo']}
    tag: {image_tag}
    pullPolicy: Always
  extProcPort: 9002
  pluginsConfigFile: "{ev['vllm_modelservice_gaie_presets']}"

  # using upstream GIE default-plugins, see: https://github.com/kubernetes-sigs/gateway-api-inference-extension/blob/main/config/charts/inferencepool/templates/epp-config.yaml#L7C3-L56C33
  pluginsCustomConfig:
    {ev['vllm_modelservice_gaie_presets']}: |
{indented_presets}
inferencePool:
  targetPortNumber: {ev['vllm_common_inference_port']}
  modelServerType: vllm
  modelServers:
    matchLabels:
      llm-d.ai/inferenceServing: "true"
      llm-d.ai/model: {model_id_label}
"""
            
            # Write GAIE values file
            gaie_values_file = helm_dir / "gaie-values.yaml"
            with open(gaie_values_file, 'w') as f:
                f.write(gaie_values_content)
            
            # Deploy helm chart via helmfile
            announce(f"🚀 Installing helm chart \"gaie-{ev['vllm_modelservice_release']}\" via helmfile...")
            helmfile_cmd = (
                f"helmfile --namespace {ev['vllm_common_namespace']} "
                f"--kubeconfig {ev['control_work_dir']}/environment/context.ctx "
                f"--selector name={ev['vllm_common_namespace']}-{model_id_label}-gaie "
                f"apply -f {ev['control_work_dir']}/setup/helm/{ev['vllm_modelservice_release']}/helmfile-{model_num}.yaml "
                f"--skip-diff-on-install"
            )
            
            llmdbench_execute_cmd(
                actual_cmd=helmfile_cmd,
                dry_run=int(ev.get("control_dry_run", 0)),
                verbose=int(ev.get("control_verbose", 0))
            )
            
            announce(f"✅ {ev['vllm_common_namespace']}-{model_id_label}-gaie helm chart deployed successfully")
            
            # List relevant resources
            resource_list = "deployment,service,pods,secrets,inferencepools"
            if int(ev.get("control_deploy_is_openshift", 0)) == 1:
                resource_list += ",route"
            
            announce(f"ℹ️ A snapshot of the relevant (model-specific) resources on namespace \"{ev['vllm_common_namespace']}\":")
            
            if int(ev.get("control_dry_run", 0)) == 0:
                kubectl_cmd = f"{ev['control_kcmd']} get --namespace {ev['vllm_common_namespace']} {resource_list}"
                llmdbench_execute_cmd(
                    actual_cmd=kubectl_cmd,
                    dry_run=int(ev.get("control_dry_run", 0)),
                    verbose=int(ev.get("control_verbose", 0)),
                    fatal=False
                )
            
            # Clean up environment variable
            if "LLMDBENCH_DEPLOY_CURRENT_MODEL_ID_LABEL" in os.environ:
                del os.environ["LLMDBENCH_DEPLOY_CURRENT_MODEL_ID_LABEL"]
            
            model_number += 1
        
        announce("✅ Completed model deployment")
    else:
        deploy_methods = ev.get("deploy_methods", "")
        announce(f"⏭️ Environment types are \"{deploy_methods}\". Skipping this step.")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())