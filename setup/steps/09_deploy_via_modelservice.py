#!/usr/bin/env python3

import os
import sys
import yaml
from pathlib import Path
from jinja2 import Template

# Add project root to Python path
current_file = Path(__file__).resolve()
project_root = current_file.parents[1]
sys.path.insert(0, str(project_root))

# Import from functions.py
from functions import (
    announce, llmdbench_execute_cmd, model_attribute, extract_environment,
    check_storage_class, check_affinity, environment_variable_to_dict, render_string
)


def add_config_prep():
    """
    Set up default values for extra pod/container configurations.
    Equivalent to the bash add_config_prep function.
    """
    # Set default values for configuration
    os.environ.setdefault("LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_POD_CONFIG", "")
    os.environ.setdefault("LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_CONTAINER_CONFIG", "")
    os.environ.setdefault("LLMDBENCH_VLLM_MODELSERVICE_PREFILL_EXTRA_POD_CONFIG", "")
    os.environ.setdefault("LLMDBENCH_VLLM_MODELSERVICE_PREFILL_EXTRA_CONTAINER_CONFIG", "")
    return ""


def add_command(component_type: str) -> str:
    """
    Generate container command configuration for modelservice components.
    
    Args:
        component_type: Either "decode" or "prefill"
    
    Returns:
        YAML string for container command configuration
    """
    # Get the command line options for the component
    if component_type == "decode":
        command_options = render_string(os.environ.get("LLMDBENCH_VLLM_MODELSERVICE_DECODE_ARGS", ""))
    elif component_type == "prefill":
        command_options = render_string(os.environ.get("LLMDBENCH_VLLM_MODELSERVICE_PREFILL_ARGS", ""))
    else:
        return ""
    
    if not command_options:
        return ""
    
    # Split command options into individual arguments
    args = []
    for arg in command_options.split():
        if arg.strip():
            args.append(f'            - "{arg}"')
    
    if args:
        command_yaml = "          command:\n"
        command_yaml += "\n".join(args)
        return command_yaml
    
    return ""


def generate_ms_rules_yaml(ev: dict, model_number: int) -> str:
    """
    Generate the ms-rules.yaml content for modelservice routing.
    
    Args:
        ev: Environment variables dictionary
        model_number: Current model number in processing
    
    Returns:
        YAML content as string
    """
    model_list = ev.get("deploy_model_list", "").replace(",", " ").split()
    
    # If only one model, create basic routing rule
    if len(model_list) <= 1:
        rules_template = """- backendRefs:
      - group: inference.networking.x-k8s.io
        kind: InferencePool
        name: {{ namespace }}-{{ model_id_label }}-decode
        weight: 1
    matches:
      - path:
          type: PathPrefix
          value: /
"""
        template = Template(rules_template)
        return template.render(
            namespace=ev.get("vllm_common_namespace", ""),
            model_id_label=ev.get("deploy_current_model_id_label", "")
        )
    
    # For multiple models, return empty (handled differently in bash)
    return ""


def generate_ms_values_yaml(ev: dict, mount_model_volume: bool) -> str:
    """
    Generate the ms-values.yaml content for Helm chart.
    
    Args:
        ev: Environment variables dictionary
        mount_model_volume: Whether to mount model volume
    
    Returns:
        YAML content as string
    """
    values_template = """model_id: "{{ model_id }}"

decode:
  replicaCount: {{ decode_replicas }}
  kserve:
    storageUri: "{{ storage_uri }}"
  {{ decode_extra_pod_config }}
  {{ decode_command }}
  {{ decode_extra_container_config }}

prefill:
  replicaCount: {{ prefill_replicas }}
  kserve:
    storageUri: "{{ storage_uri }}"
  {{ prefill_extra_pod_config }}
  {{ prefill_command }}
  {{ prefill_extra_container_config }}

{% if mount_model_volume %}
cache:
  storageClass: {{ storage_class }}
  size: {{ cache_size }}
{% endif %}

{% if gateway_enabled %}
gateway:
  domain: {{ gateway_domain }}
{% endif %}

{% if route_enabled %}
route:
  enabled: true
  domain: {{ route_domain }}
{% endif %}
"""
    
    template = Template(values_template)
    
    # Prepare template variables
    template_vars = {
        'model_id': ev.get("deploy_current_model_id", ""),
        'decode_replicas': ev.get("vllm_modelservice_decode_replicas", "1"),
        'prefill_replicas': ev.get("vllm_modelservice_prefill_replicas", "1"),
        'storage_uri': ev.get("vllm_modelservice_uri", ""),
        'decode_extra_pod_config': add_config_prep(),
        'decode_command': add_command("decode"),
        'decode_extra_container_config': "",
        'prefill_extra_pod_config': "",
        'prefill_command': add_command("prefill"),
        'prefill_extra_container_config': "",
        'mount_model_volume': mount_model_volume,
        'storage_class': ev.get("vllm_common_pvc_storage_class", ""),
        'cache_size': ev.get("vllm_common_pvc_size", ""),
        'gateway_enabled': ev.get("vllm_modelservice_gateway_enabled", "false") == "true",
        'gateway_domain': ev.get("vllm_modelservice_gateway_domain", ""),
        'route_enabled': ev.get("control_deploy_is_openshift", "0") == "1",
        'route_domain': ev.get("vllm_modelservice_route_domain", "")
    }
    
    return template.render(**template_vars)




def wait_for_pods(ev: dict, component: str, dry_run: bool, verbose: bool) -> int:
    """
    Wait for pods to be created and running.
    
    Args:
        ev: Environment variables dictionary
        component: Component name (decode/prefill)
        dry_run: Whether this is a dry run
        verbose: Whether to show verbose output
    
    Returns:
        0 for success, non-zero for failure
    """
    namespace = ev.get("vllm_common_namespace", "")
    model_id_label = ev.get("deploy_current_model_id_label", "")
    
    # Wait for pod creation
    wait_cmd = f"kubectl wait --for=condition=PodScheduled --timeout=300s pod -l serving.kserve.io/inferenceservice={namespace}-{model_id_label}-{component} -n {namespace}"
    result = llmdbench_execute_cmd(wait_cmd, dry_run, verbose)
    if result != 0:
        return result
    
    # Wait for pods to be running
    wait_cmd = f"kubectl wait --for=condition=Ready --timeout=600s pod -l serving.kserve.io/inferenceservice={namespace}-{model_id_label}-{component} -n {namespace}"
    return llmdbench_execute_cmd(wait_cmd, dry_run, verbose)


def collect_logs(ev: dict, component: str, dry_run: bool, verbose: bool) -> int:
    """
    Collect logs from component pods.
    
    Args:
        ev: Environment variables dictionary
        component: Component name (decode/prefill)
        dry_run: Whether this is a dry run
        verbose: Whether to show verbose output
    
    Returns:
        0 for success, non-zero for failure
    """
    namespace = ev.get("vllm_common_namespace", "")
    model_id_label = ev.get("deploy_current_model_id_label", "")
    work_dir = ev.get("control_work_dir", "")
    
    # Create logs directory
    logs_dir = Path(work_dir) / "logs"
    if not dry_run:
        logs_dir.mkdir(parents=True, exist_ok=True)
    
    # Collect logs
    log_cmd = f"kubectl logs -l serving.kserve.io/inferenceservice={namespace}-{model_id_label}-{component} -n {namespace} > {logs_dir}/{component}-{model_id_label}.log"
    return llmdbench_execute_cmd(log_cmd, dry_run, verbose)


def main():
    """Main function for step 09 - Deploy via modelservice"""
    
    # Set current step name for logging/tracking
    os.environ["CURRENT_STEP_NAME"] = os.path.splitext(os.path.basename(__file__))[0]
    
    # Parse environment variables into ev dictionary
    ev = {}
    environment_variable_to_dict(ev)
    
    # Check if modelservice environment is active
    if not ev.get("control_environment_type_modelservice_active", False):
        announce("⏭️ Environment type modelservice not active. Skipping this step.")
        return 0
    
    # Extract flags
    dry_run = ev.get("control_dry_run", False)
    verbose = ev.get("control_verbose", False)
    
    if dry_run:
        announce("DRY RUN enabled. No actual changes will be made.")
    
    # Check storage class
    if not check_storage_class():
        announce("❌ Failed to check storage class")
        return 1
    
    # Check affinity
    if not check_affinity():
        announce("❌ Failed to check affinity")
        return 1
    
    # Re-parse environment variables in case check functions updated them
    environment_variable_to_dict(ev)
    
    # Extract environment for debugging
    extract_environment()
    
    # Deploy models
    model_list = ev.get("deploy_model_list", "").replace(",", " ").split()
    model_number = 0
    
    for model in model_list:
        if not model.strip():
            continue
            
        # Set current model environment variables
        current_model = model_attribute(model, "model")
        current_model_id = model_attribute(model, "modelid")
        current_model_id_label = model_attribute(model, "modelid_label")
        
        os.environ["LLMDBENCH_DEPLOY_CURRENT_MODEL"] = current_model
        os.environ["LLMDBENCH_DEPLOY_CURRENT_MODEL_ID"] = current_model_id
        os.environ["LLMDBENCH_DEPLOY_CURRENT_MODEL_ID_LABEL"] = current_model_id_label
        
        # Update ev dictionary with new model info
        ev["deploy_current_model"] = current_model
        ev["deploy_current_model_id"] = current_model_id
        ev["deploy_current_model_id_label"] = current_model_id_label
        
        # Determine model mounting
        mount_model_volume = False
        if (ev.get("vllm_modelservice_uri_protocol") == "pvc" or 
            ev.get("control_environment_type_standalone_active", "0") == "1"):
            pvc_name = ev.get("vllm_common_pvc_name", "")
            os.environ["LLMDBENCH_VLLM_MODELSERVICE_URI"] = f"pvc://{pvc_name}/models/{current_model}"
            mount_model_volume = True
        else:
            os.environ["LLMDBENCH_VLLM_MODELSERVICE_URI"] = f"hf://{current_model}"
            mount_model_volume = True
        
        # Check for mount override
        if ev.get("vllm_modelservice_mount_model_volume_override"):
            mount_model_volume = ev.get("vllm_modelservice_mount_model_volume_override", "false") == "true"
        
        # Update ev with URI
        ev["vllm_modelservice_uri"] = os.environ["LLMDBENCH_VLLM_MODELSERVICE_URI"]
        
        # Create directory structure
        model_num = f"{model_number:02d}"
        release = ev.get("vllm_modelservice_release", "")
        work_dir = Path(ev.get("control_work_dir", ""))
        helm_dir = work_dir / "setup" / "helm" / release / model_num
        
        if not dry_run:
            helm_dir.mkdir(parents=True, exist_ok=True)
        else:
            announce(f"---> would create directory {helm_dir}")
        
        # Generate ms-rules.yaml
        rules_content = generate_ms_rules_yaml(ev, model_number)
        rules_file = helm_dir / "ms-rules.yaml"
        
        if not dry_run:
            rules_file.write_text(rules_content)
        else:
            announce(f"---> would write rules to {rules_file}")
        
        # Generate ms-values.yaml
        values_content = generate_ms_values_yaml(ev, mount_model_volume)
        values_file = helm_dir / "ms-values.yaml"
        
        if not dry_run:
            values_file.write_text(values_content)
        else:
            announce(f"---> would write values to {values_file}")
        
        # Use existing helmfile created by step 07
        helmfile_path = work_dir / "setup" / "helm" / release / f"helmfile-{model_num}.yaml"
        
        # Deploy via helmfile
        announce(f"🚀 Installing helm chart \"ms-{release}\" via helmfile...")
        context_path = work_dir / "environment" / "context.ctx"
        namespace = ev.get("vllm_common_namespace", "")
        
        helmfile_cmd = (f"helmfile --namespace {namespace} "
                       f"--kubeconfig {context_path} "
                       f"--selector name={namespace}-{current_model_id_label}-ms "
                       f"apply -f {helmfile_path} --skip-diff-on-install")
        
        result = llmdbench_execute_cmd(helmfile_cmd, dry_run, verbose)
        if result != 0:
            announce(f"❌ Failed to deploy helm chart for model {current_model}")
            return result
        
        # Wait for pods and collect logs
        decode_replicas = int(ev.get("vllm_modelservice_decode_replicas", "0"))
        prefill_replicas = int(ev.get("vllm_modelservice_prefill_replicas", "0"))
        
        if decode_replicas > 0:
            announce(f"⏳ Waiting for decode pods to be scheduled...")
            result = wait_for_pods(ev, "decode", dry_run, verbose)
            if result != 0:
                return result
        
        if prefill_replicas > 0:
            announce(f"⏳ Waiting for prefill pods to be scheduled...")
            result = wait_for_pods(ev, "prefill", dry_run, verbose)
            if result != 0:
                return result
        
        if decode_replicas > 0:
            announce(f"⏳ Waiting for decode pods to be ready...")
            result = wait_for_pods(ev, "decode", dry_run, verbose)
            if result != 0:
                return result
        
        if prefill_replicas > 0:
            announce(f"⏳ Waiting for prefill pods to be ready...")
            result = wait_for_pods(ev, "prefill", dry_run, verbose)
            if result != 0:
                return result
        
        # Collect logs
        if decode_replicas > 0:
            result = collect_logs(ev, "decode", dry_run, verbose)
            if result != 0:
                announce(f"⚠️ Warning: Failed to collect decode logs for {current_model}")
        
        if prefill_replicas > 0:
            result = collect_logs(ev, "prefill", dry_run, verbose)
            if result != 0:
                announce(f"⚠️ Warning: Failed to collect prefill logs for {current_model}")
        
        model_number += 1
    
    announce("✅ Modelservice deployment completed successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())