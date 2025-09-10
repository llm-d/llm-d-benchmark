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
    check_storage_class, check_affinity, environment_variable_to_dict, render_string,
    get_image, add_command_line_options, get_accelerator_nr, add_annotations as functions_add_annotations
)


def add_config_prep():
    """
    Set up default values for extra pod/container configurations.
    Equivalent to the bash add_config_prep function.
    """
    # Set default values for configuration
    os.environ.setdefault("LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_POD_CONFIG", "#no____config")
    os.environ.setdefault("LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_CONTAINER_CONFIG", "#no____config")
    os.environ.setdefault("LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_VOLUME_MOUNTS", "[]")
    os.environ.setdefault("LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_VOLUMES", "[]")
    os.environ.setdefault("LLMDBENCH_VLLM_MODELSERVICE_PREFILL_EXTRA_POD_CONFIG", "#no____config")
    os.environ.setdefault("LLMDBENCH_VLLM_MODELSERVICE_PREFILL_EXTRA_CONTAINER_CONFIG", "#no____config")
    os.environ.setdefault("LLMDBENCH_VLLM_MODELSERVICE_PREFILL_EXTRA_VOLUME_MOUNTS", "[]")
    os.environ.setdefault("LLMDBENCH_VLLM_MODELSERVICE_PREFILL_EXTRA_VOLUMES", "[]")


def add_pod_annotations(annotation_var: str) -> str:
    """
    Generate podAnnotations YAML section.
    """
    return functions_add_annotations(annotation_var)


def add_command(model_command: str) -> str:
    """
    Generate command section for container based on model_command type.
    """
    if model_command == "custom":
        return """command:
      - /bin/sh
      - '-c'"""
    return ""


# Note: add_command_line_options is now imported from functions.py


def add_additional_env_to_yaml(envvars_to_yaml: str) -> str:
    """
    Generate environment variables YAML section.
    """
    if not envvars_to_yaml:
        return ""
    
    lines = []
    for envvar in envvars_to_yaml.split(","):
        envvar = envvar.strip()
        if envvar and envvar.startswith("LLMDBENCH_"):
            # Remove LLMDBENCH_VLLM_STANDALONE_ prefix for name
            name = envvar.replace("LLMDBENCH_VLLM_STANDALONE_", "")
            value = os.environ.get(envvar, "")
            lines.append(f"      - name: {name}")
            lines.append(f"        value: \"{value}\"")
    
    return "\n".join(lines) if lines else ""


def add_config(config_data: str, indent_spaces: int, label: str = "") -> str:
    """
    Add configuration with proper indentation and optional label.
    """
    if not config_data or config_data == "#no____config":
        return ""
    
    spaces = " " * indent_spaces
    
    result = ""
    if label:
        result = f"{label}:\n"
    
    if config_data == "[]":
        return f"{result}{config_data}"
    
    # If it's a file path, read the file content
    if config_data.startswith("/") and Path(config_data).exists():
        with open(config_data, 'r') as f:
            content = f.read()
        lines = content.split('\n')
        indented_lines = [f"{spaces}{line}" for line in lines]
        return result + "\n".join(indented_lines)
    
    # Otherwise treat as direct content
    lines = config_data.split('\n')
    indented_lines = [f"{spaces}{line}" for line in lines]
    return result + "\n".join(indented_lines)


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
    if len([m for m in model_list if m.strip()]) == 1:
        model_id_label = ev.get("deploy_current_model_id_label", "")
        return f"""- backendRefs:
      - group: inference.networking.x-k8s.io
        kind: InferencePool
        name: {model_id_label}-gaie
        port: 8000
        weight: 1
"""
    
    # For multiple models, return empty (handled differently in bash)
    return ""


def filter_empty_resource(resource_name: str, resource_value: str) -> str:
    """
    Filter out empty resource values, mimicking bash behavior with sed.
    The bash script filters lines that start with ': ""' (empty resource values).
    """
    if not resource_name or not resource_value:
        return ""
    return f"        {resource_name}: \"{resource_value}\""


def generate_ms_values_yaml(ev: dict, mount_model_volume: bool, rules_file: Path) -> str:
    """
    Generate the ms-values.yaml content for Helm chart.
    Exactly matches the bash script structure from lines 60-239.
    
    Args:
        ev: Environment variables dictionary
        mount_model_volume: Whether to mount model volume
        rules_file: Path to ms-rules.yaml file to be included
    
    Returns:
        YAML content as string
    """
    # Get all required environment variables
    fullname_override = ev.get("deploy_current_model_id_label", "")
    multinode = ev.get("vllm_modelservice_multinode", "false")
    
    # Model artifacts section
    model_uri = ev.get("vllm_modelservice_uri", "")
    model_size = ev.get("vllm_common_pvc_model_cache_size", "")
    model_name = ev.get("deploy_current_model", "")
    
    # Routing section
    service_port = ev.get("vllm_common_inference_port", "8000")
    release = ev.get("vllm_modelservice_release", "")
    route_enabled = ev.get("vllm_modelservice_route", "false")
    model_id = ev.get("deploy_current_model_id", "")
    model_id_label = ev.get("deploy_current_model_id_label", "")
    
    # Image details
    image_registry = ev.get("llmd_image_registry", "")
    image_repo = ev.get("llmd_image_repo", "")
    image_name = ev.get("llmd_image_name", "")
    image_tag = ev.get("llmd_image_tag", "")
    main_image = get_image(image_registry, image_repo, image_name, image_tag, 0)
    
    # Proxy details
    proxy_image_registry = ev.get("llmd_routingsidecar_image_registry", "")
    proxy_image_repo = ev.get("llmd_routingsidecar_image_repo", "")
    proxy_image_name = ev.get("llmd_routingsidecar_image_name", "")
    proxy_image_tag = ev.get("llmd_routingsidecar_image_tag", "")
    proxy_image = get_image(proxy_image_registry, proxy_image_repo, proxy_image_name, proxy_image_tag, 0)
    proxy_connector = ev.get("llmd_routingsidecar_connector", "")
    proxy_debug_level = ev.get("llmd_routingsidecar_debug_level", "")
    
    # EPP and routing configuration
    inference_model_create = ev.get("vllm_modelservice_inference_model", "true")
    inference_pool_create = ev.get("vllm_modelservice_inference_pool", "true")
    epp_create = ev.get("vllm_modelservice_epp", "true")
    
    # Decode configuration
    decode_replicas = int(ev.get("vllm_modelservice_decode_replicas", "0"))
    decode_create = "true" if decode_replicas > 0 else "false"
    decode_data_parallelism = ev.get("vllm_modelservice_decode_data_parallelism", "1")
    decode_tensor_parallelism = ev.get("vllm_modelservice_decode_tensor_parallelism", "1")
    decode_model_command = ev.get("vllm_modelservice_decode_model_command", "")
    decode_extra_args = ev.get("vllm_modelservice_decode_extra_args", "")
    decode_cpu_mem = ev.get("vllm_modelservice_decode_cpu_mem", "")
    decode_cpu_nr = ev.get("vllm_modelservice_decode_cpu_nr", "")
    decode_inference_port = ev.get("vllm_modelservice_decode_inference_port", "8000")
    
    # Prefill configuration
    prefill_replicas = int(ev.get("vllm_modelservice_prefill_replicas", "0"))
    prefill_create = "true" if prefill_replicas > 0 else "false"
    prefill_data_parallelism = ev.get("vllm_modelservice_prefill_data_parallelism", "1")
    prefill_tensor_parallelism = ev.get("vllm_modelservice_prefill_tensor_parallelism", "1")
    prefill_model_command = ev.get("vllm_modelservice_prefill_model_command", "")
    prefill_extra_args = ev.get("vllm_modelservice_prefill_extra_args", "")
    prefill_cpu_mem = ev.get("vllm_modelservice_prefill_cpu_mem", "")
    prefill_cpu_nr = ev.get("vllm_modelservice_prefill_cpu_nr", "")
    
    # Resource configuration
    accelerator_resource = ev.get("vllm_common_accelerator_resource", "")
    decode_accelerator_nr = ev.get("vllm_modelservice_decode_accelerator_nr", "auto")
    prefill_accelerator_nr = ev.get("vllm_modelservice_prefill_accelerator_nr", "auto")
    
    # Calculate actual accelerator numbers
    decode_accelerator_count = get_accelerator_nr(
        decode_accelerator_nr, 
        decode_tensor_parallelism, 
        decode_data_parallelism
    )
    prefill_accelerator_count = get_accelerator_nr(
        prefill_accelerator_nr, 
        prefill_tensor_parallelism, 
        prefill_data_parallelism
    )
    
    ephemeral_storage_resource = ev.get("vllm_common_ephemeral_storage_resource", "")
    decode_ephemeral_storage_nr = ev.get("vllm_modelservice_decode_ephemeral_storage_nr", "")
    prefill_ephemeral_storage_nr = ev.get("vllm_modelservice_prefill_ephemeral_storage_nr", "")
    
    decode_network_resource = ev.get("vllm_modelservice_decode_network_resource", "")
    decode_network_nr = ev.get("vllm_modelservice_decode_network_nr", "")
    prefill_network_resource = ev.get("vllm_modelservice_prefill_network_resource", "")
    prefill_network_nr = ev.get("vllm_modelservice_prefill_network_nr", "")
    
    # Affinity configuration - get fresh value after check_affinity() call
    affinity = os.environ.get("LLMDBENCH_VLLM_COMMON_AFFINITY", "")
    if ":" in affinity:
        affinity_key, affinity_value = affinity.split(":", 1)
    else:
        affinity_key, affinity_value = "", ""
    
    # Probe configuration
    initial_delay_probe = ev.get("vllm_common_initial_delay_probe", "30")
    common_inference_port = ev.get("vllm_common_inference_port", "8000")
    
    # Extra configurations
    decode_extra_pod_config = ev.get("vllm_modelservice_decode_extra_pod_config", "")
    decode_extra_container_config = ev.get("vllm_modelservice_decode_extra_container_config", "")
    decode_extra_volume_mounts = ev.get("vllm_modelservice_decode_extra_volume_mounts", "")
    decode_extra_volumes = ev.get("vllm_modelservice_decode_extra_volumes", "")
    
    prefill_extra_pod_config = ev.get("vllm_modelservice_prefill_extra_pod_config", "")
    prefill_extra_container_config = ev.get("vllm_modelservice_prefill_extra_container_config", "")
    prefill_extra_volume_mounts = ev.get("vllm_modelservice_prefill_extra_volume_mounts", "")
    prefill_extra_volumes = ev.get("vllm_modelservice_prefill_extra_volumes", "")
    
    # Environment variables to YAML
    envvars_to_yaml = ev.get("vllm_common_envvars_to_yaml", "")
    
    # Read the rules file content
    rules_content = ""
    if rules_file.exists():
        rules_content = rules_file.read_text().rstrip()
    
    # Build the complete YAML structure exactly matching the bash script
    yaml_content = f"""fullnameOverride: {fullname_override}
multinode: {multinode}

modelArtifacts:
  uri: {model_uri}
  size: {model_size}
  authSecretName: "llm-d-hf-token"
  name: {model_name}

routing:
  servicePort: {service_port}
  parentRefs:
    - group: gateway.networking.k8s.io
      kind: Gateway
      name: infra-{release}-inference-gateway
  proxy:
    image: "{proxy_image}"
    secure: false
    connector: {proxy_connector}
    debugLevel: {proxy_debug_level}
  inferenceModel:
    create: {inference_model_create}
  inferencePool:
    create: {inference_pool_create}
    name: {model_id_label}-gaie
  httpRoute:
    create: {route_enabled}
    rules:
    - backendRefs:
      - group: inference.networking.x-k8s.io
        kind: InferencePool
        name: {model_id_label}-gaie
        port: 8000
        weight: 1
      matches:
      - path:
          type: PathPrefix
          value: /{model_id}/
      filters:
      - type: URLRewrite
        urlRewrite:
          path:
            type: ReplacePrefixMatch
            replacePrefixMatch: /
    {rules_content}

  epp:
    create: {epp_create}

decode:
  create: {decode_create}
  replicas: {decode_replicas}
  acceleratorTypes:
      labelKey: {affinity_key}
      labelValues:
        - {affinity_value}
  parallelism:
    data: {decode_data_parallelism}
    tensor: {decode_tensor_parallelism}
  annotations:
      {functions_add_annotations("LLMDBENCH_VLLM_COMMON_ANNOTATIONS")}
  podAnnotations:
      {add_pod_annotations("LLMDBENCH_VLLM_MODELSERVICE_DECODE_PODANNOTATIONS")}
  {add_config(decode_extra_pod_config, 2, "extraConfig")}
  containers:
  - name: "vllm"
    mountModelVolume: {str(mount_model_volume).lower()}
    image: "{main_image}"
    modelCommand: {decode_model_command}
    {add_command(decode_model_command)}
    args: |
      {add_command_line_options(decode_extra_args).replace('        - |', '').strip()}
    env:
      - name: VLLM_NIXL_SIDE_CHANNEL_HOST
        valueFrom:
          fieldRef:
            fieldPath: status.podIP
      {add_additional_env_to_yaml(envvars_to_yaml)}
    ports:
      - containerPort: {decode_inference_port}
      - containerPort: 5557
    resources:
      limits:
        memory: {decode_cpu_mem}
        cpu: "{decode_cpu_nr}"
        {filter_empty_resource(ephemeral_storage_resource, decode_ephemeral_storage_nr)}
        {filter_empty_resource(accelerator_resource, str(decode_accelerator_count))}
        {filter_empty_resource(decode_network_resource, decode_network_nr)}
      requests:
        memory: {decode_cpu_mem}
        cpu: "{decode_cpu_nr}"
        {filter_empty_resource(ephemeral_storage_resource, decode_ephemeral_storage_nr)}
        {filter_empty_resource(accelerator_resource, str(decode_accelerator_count))}
        {filter_empty_resource(decode_network_resource, decode_network_nr)}
    extraConfig:
      startupProbe:
        httpGet:
          path: /health
          port: {decode_inference_port}
        failureThreshold: 60
        initialDelaySeconds: {initial_delay_probe}
        periodSeconds: 30
        timeoutSeconds: 5
      livenessProbe:
        tcpSocket:
          port: {decode_inference_port}
        failureThreshold: 3
        periodSeconds: 5
      readinessProbe:
        httpGet:
          path: /health
          port: 8200
        failureThreshold: 3
        periodSeconds: 5
    {add_config(decode_extra_container_config, 6)}
    volumeMounts: {add_config(decode_extra_volume_mounts, 4)}
  volumes: {add_config(decode_extra_volumes, 2)}

prefill:
  create: {prefill_create}
  replicas: {prefill_replicas}
  acceleratorTypes:
      labelKey: {affinity_key}
      labelValues:
        - {affinity_value}
  parallelism:
    data: {prefill_data_parallelism}
    tensor: {prefill_tensor_parallelism}
  annotations:
      {functions_add_annotations("LLMDBENCH_VLLM_COMMON_ANNOTATIONS")}
  podAnnotations:
      {add_pod_annotations("LLMDBENCH_VLLM_MODELSERVICE_PREFILL_PODANNOTATIONS")}
  {add_config(prefill_extra_pod_config, 2, "extraConfig")}
  containers:
  - name: "vllm"
    mountModelVolume: {str(mount_model_volume).lower()}
    image: "{main_image}"
    modelCommand: {prefill_model_command}
    {add_command(prefill_model_command)}
    args: |
      {add_command_line_options(prefill_extra_args).replace('        - |', '').strip()}
    env:
      - name: VLLM_IS_PREFILL
        value: "1"
      - name: VLLM_NIXL_SIDE_CHANNEL_HOST
        valueFrom:
          fieldRef:
            fieldPath: status.podIP
      {add_additional_env_to_yaml(envvars_to_yaml)}
    ports:
      - containerPort: {common_inference_port}
      - containerPort: 5557
    resources:
      limits:
        memory: {prefill_cpu_mem}
        cpu: "{prefill_cpu_nr}"
        {filter_empty_resource(ephemeral_storage_resource, prefill_ephemeral_storage_nr)}
        {filter_empty_resource(accelerator_resource, str(prefill_accelerator_count))}
        {filter_empty_resource(prefill_network_resource, prefill_network_nr)}
      requests:
        memory: {prefill_cpu_mem}
        cpu: "{prefill_cpu_nr}"
        {filter_empty_resource(ephemeral_storage_resource, prefill_ephemeral_storage_nr)}
        {filter_empty_resource(accelerator_resource, str(prefill_accelerator_count))}
        {filter_empty_resource(prefill_network_resource, prefill_network_nr)}
    extraConfig:
      startupProbe:
        httpGet:
          path: /health
          port: {common_inference_port}
        failureThreshold: 60
        initialDelaySeconds: {initial_delay_probe}
        periodSeconds: 30
        timeoutSeconds: 5
      livenessProbe:
        tcpSocket:
          port: {common_inference_port}
        failureThreshold: 3
        periodSeconds: 5
      readinessProbe:
        httpGet:
          path: /health
          port: {common_inference_port}
        failureThreshold: 3
        periodSeconds: 5
    {add_config(prefill_extra_container_config, 6)}
    volumeMounts: {add_config(prefill_extra_volume_mounts, 4)}
  volumes: {add_config(prefill_extra_volumes, 2)}
"""
    
    return yaml_content




def wait_for_pods_creation(ev: dict, component: str, dry_run: bool, verbose: bool) -> int:
    """
    Wait for pods to be created.
    """
    namespace = ev.get("vllm_common_namespace", "")
    model_id_label = ev.get("deploy_current_model_id_label", "")
    wait_timeout = int(ev.get("control_wait_timeout", "600")) // 2
    
    announce(f"⏳ waiting for ({component}) pods serving model to be created...")
    wait_cmd = f"kubectl --namespace {namespace} wait --timeout={wait_timeout}s --for=create pod -l llm-d.ai/model={model_id_label},llm-d.ai/role={component}"
    result = llmdbench_execute_cmd(wait_cmd, dry_run, verbose, 1, 2)
    if result == 0:
        announce(f"✅ ({component}) pods serving model created")
    return result


def wait_for_pods_running(ev: dict, component: str, dry_run: bool, verbose: bool) -> int:
    """
    Wait for pods to be in Running state.
    """
    namespace = ev.get("vllm_common_namespace", "")
    model_id_label = ev.get("deploy_current_model_id_label", "")
    wait_timeout = ev.get("control_wait_timeout", "600")
    
    announce(f"⏳ Waiting for ({component}) pods serving model to be in \"Running\" state (timeout={wait_timeout}s)...")
    wait_cmd = f"kubectl --namespace {namespace} wait --timeout={wait_timeout}s --for=jsonpath='{{.status.phase}}'=Running pod -l llm-d.ai/model={model_id_label},llm-d.ai/role={component}"
    result = llmdbench_execute_cmd(wait_cmd, dry_run, verbose)
    if result == 0:
        announce(f"🚀 ({component}) pods serving model running")
    return result


def wait_for_pods_ready(ev: dict, component: str, dry_run: bool, verbose: bool) -> int:
    """
    Wait for pods to be Ready.
    """
    namespace = ev.get("vllm_common_namespace", "")
    model_id_label = ev.get("deploy_current_model_id_label", "")
    wait_timeout = ev.get("control_wait_timeout", "600")
    
    announce(f"⏳ Waiting for ({component}) pods serving model to be Ready (timeout={wait_timeout}s)...")
    wait_cmd = f"kubectl --namespace {namespace} wait --timeout={wait_timeout}s --for=condition=Ready=True pod -l llm-d.ai/model={model_id_label},llm-d.ai/role={component}"
    result = llmdbench_execute_cmd(wait_cmd, dry_run, verbose)
    if result == 0:
        announce(f"🚀 ({component}) pods serving model ready")
    return result


def collect_logs(ev: dict, component: str, dry_run: bool, verbose: bool) -> int:
    """
    Collect logs from component pods.
    """
    namespace = ev.get("vllm_common_namespace", "")
    model_id_label = ev.get("deploy_current_model_id_label", "")
    work_dir = ev.get("control_work_dir", "")
    
    # Create logs directory
    logs_dir = Path(work_dir) / "setup" / "logs"
    if not dry_run:
        logs_dir.mkdir(parents=True, exist_ok=True)
    
    # Collect logs
    log_file = logs_dir / f"llm-d-{component}.log"
    log_cmd = f"kubectl --namespace {namespace} logs --tail=-1 --prefix=true -l llm-d.ai/model={model_id_label},llm-d.ai/role={component} > {log_file}"
    return llmdbench_execute_cmd(log_cmd, dry_run, verbose)


def main():
    """Main function for step 09 - Deploy via modelservice"""
    
    # Set current step for functions.py compatibility
    os.environ["LLMDBENCH_CURRENT_STEP"] = "09"
    
    # Parse environment variables into ev dictionary
    ev = {}
    environment_variable_to_dict(ev)
    
    # Check if modelservice environment is active
    if not ev.get("control_environment_type_modelservice_active", False):
        deploy_methods = ev.get("deploy_methods", "")
        announce(f"⏭️ Environment types are \"{deploy_methods}\". Skipping this step.")
        return 0
    
    # Check storage class
    if not check_storage_class():
        announce("❌ Failed to check storage class")
        return 1
    
    # Check affinity
    if not check_affinity():
        announce("❌ Failed to check affinity")
        return 1
    
    # Extract environment for debugging
    extract_environment()
    
    # Extract flags
    dry_run = ev.get("control_dry_run", "false") == "true"
    verbose = ev.get("control_verbose", "false") == "true"
    
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
        mount_override = ev.get("vllm_modelservice_mount_model_volume_override")
        if mount_override:
            mount_model_volume = mount_override == "true"
        
        # Update ev with URI
        ev["vllm_modelservice_uri"] = os.environ["LLMDBENCH_VLLM_MODELSERVICE_URI"]
        
        # Create directory structure (Do not use "llmdbench_execute_cmd" for these commands)
        model_num = f"{model_number:02d}"
        release = ev.get("vllm_modelservice_release", "")
        work_dir = Path(ev.get("control_work_dir", ""))
        helm_dir = work_dir / "setup" / "helm" / release / model_num
        
        # Always create directory structure (even in dry-run)
        helm_dir.mkdir(parents=True, exist_ok=True)
        
        # Generate ms-rules.yaml content first
        rules_content = generate_ms_rules_yaml(ev, model_number)
        rules_file = helm_dir / "ms-rules.yaml"
        
        # Write empty file first, then write content
        rules_file.write_text("")
        if len([m for m in model_list if m.strip()]) == 1:
            rules_file.write_text(rules_content)
        
        # Set up configuration preparation
        add_config_prep()
        
        # Generate ms-values.yaml
        values_content = generate_ms_values_yaml(ev, mount_model_volume, rules_file)
        values_file = helm_dir / "ms-values.yaml"
        values_file.write_text(values_content)
        
        # Clean up temp file
        rules_file.unlink()
        
        # Deploy via helmfile
        announce(f"🚀 Installing helm chart \"ms-{release}\" via helmfile...")
        context_path = work_dir / "environment" / "context.ctx"
        namespace = ev.get("vllm_common_namespace", "")
        
        helmfile_cmd = (f"helmfile --namespace {namespace} "
                       f"--kubeconfig {context_path} "
                       f"--selector name={current_model_id_label}-ms "
                       f"apply -f {work_dir}/setup/helm/{release}/helmfile-{model_num}.yaml --skip-diff-on-install --skip-schema-validation")
        
        result = llmdbench_execute_cmd(helmfile_cmd, dry_run, verbose)
        if result != 0:
            announce(f"❌ Failed to deploy helm chart for model {current_model}")
            return result
        
        announce(f"✅ {namespace}-{current_model_id_label}-ms helm chart deployed successfully")
        
        # Wait for pods and collect logs exactly like bash script
        decode_replicas = int(ev.get("vllm_modelservice_decode_replicas", "0"))
        prefill_replicas = int(ev.get("vllm_modelservice_prefill_replicas", "0"))
        
        # Wait for decode pods creation
        if decode_replicas > 0:
            result = wait_for_pods_creation(ev, "decode", dry_run, verbose)
            if result != 0:
                return result
        
        # Wait for prefill pods creation
        if prefill_replicas > 0:
            result = wait_for_pods_creation(ev, "prefill", dry_run, verbose)
            if result != 0:
                return result
        
        # Wait for decode pods to be running
        if decode_replicas > 0:
            result = wait_for_pods_running(ev, "decode", dry_run, verbose)
            if result != 0:
                return result
        
        # Wait for prefill pods to be running
        if prefill_replicas > 0:
            result = wait_for_pods_running(ev, "prefill", dry_run, verbose)
            if result != 0:
                return result
        
        # Wait for decode pods to be ready
        if decode_replicas > 0:
            result = wait_for_pods_ready(ev, "decode", dry_run, verbose)
            if result != 0:
                return result
            
            # Collect decode logs
            collect_logs(ev, "decode", dry_run, verbose)
        
        # Wait for prefill pods to be ready
        if prefill_replicas > 0:
            result = wait_for_pods_ready(ev, "prefill", dry_run, verbose)
            if result != 0:
                return result
            
            # Collect prefill logs
            collect_logs(ev, "prefill", dry_run, verbose)
        
        # Handle OpenShift route creation
        if (ev.get("vllm_modelservice_route") == "true" and 
            ev.get("control_deploy_is_openshift", "0") == "1"):
            
            # Check if route exists
            route_name = f"{release}-inference-gateway-route"
            check_route_cmd = f"kubectl --namespace {namespace} get route -o name --ignore-not-found | grep -E \"/{route_name}$\""
            
            result = llmdbench_execute_cmd(check_route_cmd, dry_run, verbose)
            if result != 0:  # Route doesn't exist
                announce(f"📜 Exposing pods serving model {model} as service...")
                inference_port = ev.get("vllm_common_inference_port", "8000")
                expose_cmd = (f"kubectl --namespace {namespace} expose service/infra-{release}-inference-gateway "
                             f"--target-port={inference_port} --name={route_name}")
                
                result = llmdbench_execute_cmd(expose_cmd, dry_run, verbose)
                if result == 0:
                    announce(f"✅ Service for pods service model {model} created")
            
            announce(f"✅ Model \"{model}\" and associated service deployed.")
        
        # Clean up model environment variables
        if "LLMDBENCH_DEPLOY_CURRENT_MODEL" in os.environ:
            del os.environ["LLMDBENCH_DEPLOY_CURRENT_MODEL"]
        if "LLMDBENCH_DEPLOY_CURRENT_MODEL_ID" in os.environ:
            del os.environ["LLMDBENCH_DEPLOY_CURRENT_MODEL_ID"]
        if "LLMDBENCH_DEPLOY_CURRENT_MODEL_ID_LABEL" in os.environ:
            del os.environ["LLMDBENCH_DEPLOY_CURRENT_MODEL_ID_LABEL"]
        
        model_number += 1
    
    announce("✅ modelservice completed model deployment")
    return 0


if __name__ == "__main__":
    sys.exit(main())