#!/usr/bin/env python3

import os
import sys
from pathlib import Path

# Add project root to Python path
current_file = Path(__file__).resolve()
project_root = current_file.parents[1]
sys.path.insert(0, str(project_root))

# Import from functions.py
from functions import (
    environment_variable_to_dict,
    announce,
    llmdbench_execute_cmd,
    model_attribute,
    extract_environment,
    get_image,
    add_config,
)


def provider(provider: str) -> str:
    if provider == "gke":
        return provider
    return "none"


def main():
    """Deploy GAIE (Gateway API Inference Extension) components."""
    os.environ["CURRENT_STEP_NAME"] = os.path.splitext(os.path.basename(__file__))[0]

    # Parse environment variables
    ev = {}
    environment_variable_to_dict(ev)

    # Check if modelservice environment is active
    if int(ev.get("control_environment_type_modelservice_active", 0)) == 1:
        extract_environment()

        model_number = 0
        model_list = ev.get("deploy_model_list", "").replace(",", " ").split()

        for model in model_list:
            announce(
                f"🔄 Processing model {model_number + 1}/{len(model_list)}: {model}"
            )

            # Get model attribute
            model_id_label = model_attribute(model, "modelid_label")
            os.environ["LLMDBENCH_DEPLOY_CURRENT_MODEL_ID_LABEL"] = model_id_label

            # Format model number with zero padding
            model_num = f"{model_number:02d}"

            # Create directory structure
            helm_dir = (
                Path(ev["control_work_dir"])
                / "setup"
                / "helm"
                / ev["vllm_modelservice_release"]
                / model_num
            )
            helm_dir.mkdir(parents=True, exist_ok=True)

            # A plugin config file is identified by ev["vllm_modelservice_gaie_plugins_configfile"]
            # The definition may be in the upstream GAIE configuration
            # In a benchmark provided configuration (in setup/presets/gaie)
            # In a user provided configuration in ev["vllm_modelservice_gaie_custom_plugins"]
            # If the user provides a configuration, this is used

            # default plugin_configuration is empty
            plugin_config = "{}"
            # look for benchmark provided ev["vllm_modelservice_gaie_plugins_configfile"]
            # expose it as ev["vllm_modelservice_gaie_presets_full_path"]
            if ev["vllm_modelservice_gaie_plugins_configfile"].startswith("/"):
                ev["vllm_modelservice_gaie_presets_full_path"] = ev[
                    "vllm_modelservice_gaie_plugins_configfile"
                ]
            else:
                configfile = ev["vllm_modelservice_gaie_plugins_configfile"]
                if not configfile.endswith(".yaml"):
                    configfile = configfile + ".yaml"
                ev["vllm_modelservice_gaie_presets_full_path"] = (
                    Path(ev["main_dir"]) / "setup" / "presets" / "gaie" / configfile
                )

            # If the (benchmark) plugin config file exists
            # and vllm_modelservice_gaie_custom_plugins is not defined
            # then use the file
            try:
                with open(ev["vllm_modelservice_gaie_presets_full_path"], "r") as f:
                    presets_content = f.read()
                if "vllm_modelservice_gaie_custom_plugins" not in ev:
                    plugin_config = (
                        f'{ev["vllm_modelservice_gaie_plugins_configfile"]}: |\n'
                        + "\n".join(
                            f"  {line}" for line in presets_content.splitlines()
                        )
                    )
            except FileNotFoundError:
                # The (benchmark) plugin config file does not exist
                # - use ev["vllm_modelservice_gaie_custom_plugins"] if it is defined
                if "vllm_modelservice_gaie_custom_plugins" in ev:
                    plugin_config = "\n".join(
                        f"{line}"
                        for line in ev[
                            "vllm_modelservice_gaie_custom_plugins"
                        ].splitlines()
                    )

            # Get image tag
            image_tag = get_image(
                ev["llmd_inferencescheduler_image_registry"],
                ev["llmd_inferencescheduler_image_repo"],
                ev["llmd_inferencescheduler_image_name"],
                ev["llmd_inferencescheduler_image_tag"],
                "1",
            )
            hf_token_env = ""
            if ev["hf_token"]:
                hf_token_env = f"""
  env:
    - name: HF_TOKEN
      valueFrom:
        secretKeyRef:
          name: {ev["vllm_common_hf_token_name"]}
          key: {ev["vllm_common_hf_token_key"]}"""

            # Generate GAIE values YAML content
            gaie_values_content = f"""inferenceExtension:
  replicas: 1
  image:
    name: {ev['llmd_inferencescheduler_image_name']}
    hub: {ev['llmd_inferencescheduler_image_registry']}/{ev['llmd_inferencescheduler_image_repo']}
    tag: {image_tag}
    pullPolicy: Always
  extProcPort: 9002
  extraContainerPorts:
    - name: zmq
      containerPort: 5557
      protocol: TCP
  extraServicePorts:
    - name: zmq
      port: 5557
      targetPort: 5557
      protocol: TCP
  {hf_token_env}
  pluginsConfigFile: "{ev['vllm_modelservice_gaie_plugins_configfile']}"
{add_config(plugin_config, 4, "pluginsCustomConfig:")}
inferencePool:
  targetPortNumber: {ev['vllm_common_inference_port']}
  modelServerType: vllm
  apiVersion: "inference.networking.x-k8s.io/v1alpha2"
  modelServers:
    matchLabels:
      llm-d.ai/inferenceServing: "true"
      llm-d.ai/model: {model_id_label}
provider:
  name: {provider(ev['vllm_modelservice_gateway_class_name'])}
"""
            # Write GAIE values file
            gaie_values_file = helm_dir / "gaie-values.yaml"
            with open(gaie_values_file, "w") as f:
                f.write(gaie_values_content)

            # Deploy helm chart via helmfile
            announce(
                f"🚀 Installing helm chart \"gaie-{ev['vllm_modelservice_release']}\" via helmfile..."
            )
            helmfile_cmd = (
                f"helmfile --namespace {ev['vllm_common_namespace']} "
                f"--kubeconfig {ev['control_work_dir']}/environment/context.ctx "
                f"--selector name={model_id_label}-gaie "
                f"apply -f {ev['control_work_dir']}/setup/helm/{ev['vllm_modelservice_release']}/helmfile-{model_num}.yaml "
                f"--skip-diff-on-install"
            )

            result = llmdbench_execute_cmd(
                actual_cmd=helmfile_cmd,
                dry_run=int(ev.get("control_dry_run", 0)),
                verbose=int(ev.get("control_verbose", 0)),
            )
            if result != 0:
                announce(
                    f"❌ Failed installing helm chart \"gaie-{ev['vllm_modelservice_release']}\" via helmfile with \"{helmfile_cmd}\" (exit code: {result})"
                )
                exit(result)

            announce(
                f"✅ {ev['vllm_common_namespace']}-{model_id_label}-gaie helm chart deployed successfully"
            )

            # List relevant resources
            resource_list = "deployment,service,pods,secrets,inferencepools"
            if int(ev.get("control_deploy_is_openshift", 0)) == 1:
                resource_list += ",route"

            announce(
                f"ℹ️ A snapshot of the relevant (model-specific) resources on namespace \"{ev['vllm_common_namespace']}\":"
            )

            if int(ev.get("control_dry_run", 0)) == 0:
                kubectl_cmd = f"{ev['control_kcmd']} get --namespace {ev['vllm_common_namespace']} {resource_list}"
                result = llmdbench_execute_cmd(
                    actual_cmd=kubectl_cmd,
                    dry_run=int(ev.get("control_dry_run", 0)),
                    verbose=int(ev.get("control_verbose", 0)),
                    fatal=False,
                )
                if result != 0:
                    announce(
                        f"❌ Failed to get a snapshot of the relevant (model-specific) resources on namespace \"{ev['vllm_common_namespace']}\" with \"{kubectl_cmd}\" (exit code: {result})"
                    )
                    exit(result)

            # Clean up environment variable
            if "LLMDBENCH_DEPLOY_CURRENT_MODEL_ID_LABEL" in os.environ:
                del os.environ["LLMDBENCH_DEPLOY_CURRENT_MODEL_ID_LABEL"]

            model_number += 1

        announce("✅ Completed model deployment")
    else:
        deploy_methods = ev.get("deploy_methods", "")
        announce(f'⏭️ Environment types are "{deploy_methods}". Skipping this step.')

    return 0


if __name__ == "__main__":
    sys.exit(main())
