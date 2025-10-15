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
    announce, llmdbench_execute_cmd, model_attribute, extract_environment,
    check_storage_class, check_affinity, environment_variable_to_dict,
    get_image, add_command_line_options, get_accelerator_nr, add_annotations as functions_add_annotations,
    add_additional_env_to_yaml as functions_add_additional_env_to_yaml, add_config as functions_add_config
)




def add_command(model_command: str) -> str:
    """
    Generate command section for container based on model_command type.
    """
    if model_command == "custom":
        return """command:
      - /bin/sh
      - '-c'"""
    return ""


def conditional_volume_config(volume_config: str, field_name: str, indent: int = 4) -> str:
    """
    Generate volume configuration only if the config is not empty.
    Skip the field entirely if the volume config is empty or contains only "[]" or "{}".
    """
    config_result = functions_add_config(volume_config, indent)
    if config_result.strip():
        return f"{field_name}: {config_result}"
    return ""


def conditional_extra_config(extra_config: str, indent: int = 2, label: str = "extraConfig") -> str:
    """
    Generate extraConfig section only if the config is not empty.
    Skip the field entirely if the config is empty or contains only "{}" or "[]".
    """
    # Check if config is empty before processing
    if not extra_config or extra_config.strip() in ["{}", "[]", "#no____config"]:
        return ""

    config_result = functions_add_config(extra_config, indent + 2)  # Add extra indent for content
    if config_result.strip():
        spaces = " " * indent
        return f"{spaces}{label}:\n{config_result}"
    return ""


def add_config_prep():
    """
    Set proper defaults for empty configurations.
    Equivalent to the bash add_config_prep function.
    """
    # Set defaults for decode extra configs
    if not os.environ.get("LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_POD_CONFIG"):
        os.environ["LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_POD_CONFIG"] = "{}"

    if not os.environ.get("LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_CONTAINER_CONFIG"):
        os.environ["LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_CONTAINER_CONFIG"] = "{}"

    if not os.environ.get("LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_VOLUME_MOUNTS"):
        os.environ["LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_VOLUME_MOUNTS"] = "[]"

    if not os.environ.get("LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_VOLUMES"):
        os.environ["LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_VOLUMES"] = "[]"

    # Set defaults for prefill extra configs
    if not os.environ.get("LLMDBENCH_VLLM_MODELSERVICE_PREFILL_EXTRA_POD_CONFIG"):
        os.environ["LLMDBENCH_VLLM_MODELSERVICE_PREFILL_EXTRA_POD_CONFIG"] = "{}"

    if not os.environ.get("LLMDBENCH_VLLM_MODELSERVICE_PREFILL_EXTRA_CONTAINER_CONFIG"):
        os.environ["LLMDBENCH_VLLM_MODELSERVICE_PREFILL_EXTRA_CONTAINER_CONFIG"] = "{}"

    if not os.environ.get("LLMDBENCH_VLLM_MODELSERVICE_PREFILL_EXTRA_VOLUME_MOUNTS"):
        os.environ["LLMDBENCH_VLLM_MODELSERVICE_PREFILL_EXTRA_VOLUME_MOUNTS"] = "[]"

    if not os.environ.get("LLMDBENCH_VLLM_MODELSERVICE_PREFILL_EXTRA_VOLUMES"):
        os.environ["LLMDBENCH_VLLM_MODELSERVICE_PREFILL_EXTRA_VOLUMES"] = "[]"


# Note: add_command_line_options is now imported from functions.py






def filter_empty_resource(resource_name: str, resource_value: str) -> str:
    """
    Filter out empty resource values, mimicking bash behavior with sed.
    The bash script filters lines that start with ': ""' (empty resource values).
    """
    if not resource_name or not resource_value:
        return ""
    return f"{resource_name}: \"{resource_value}\""


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
    decode_cpu_mem = ev.get("vllm_modelservice_decode_cpu_mem", "") or ev.get("vllm_common_cpu_mem", "")
    decode_cpu_nr = ev.get("vllm_modelservice_decode_cpu_nr", "") or ev.get("vllm_common_cpu_nr", "")
    decode_inference_port = ev.get("vllm_modelservice_decode_inference_port", "8000")

    # Prefill configuration
    prefill_replicas = int(ev.get("vllm_modelservice_prefill_replicas", "0"))
    prefill_create = "true" if prefill_replicas > 0 else "false"
    prefill_data_parallelism = ev.get("vllm_modelservice_prefill_data_parallelism", "1")
    prefill_tensor_parallelism = ev.get("vllm_modelservice_prefill_tensor_parallelism", "1")
    prefill_model_command = ev.get("vllm_modelservice_prefill_model_command", "")
    prefill_extra_args = ev.get("vllm_modelservice_prefill_extra_args", "")
    prefill_cpu_mem = ev.get("vllm_modelservice_prefill_cpu_mem", "") or ev.get("vllm_common_cpu_mem", "")
    prefill_cpu_nr = ev.get("vllm_modelservice_prefill_cpu_nr", "") or ev.get("vllm_common_cpu_nr", "")

    # Resource configuration - handle auto accelerator resource
    accelerator_resource = os.environ.get("LLMDBENCH_VLLM_COMMON_ACCELERATOR_RESOURCE", "")
    if accelerator_resource == "auto":
        accelerator_resource = "nvidia.com/gpu"

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

    # Build decode resources section cleanly
    decode_limits_resources = []
    decode_requests_resources = []

    if decode_cpu_mem:
        decode_limits_resources.append(f"        memory: {decode_cpu_mem}")
        decode_requests_resources.append(f"        memory: {decode_cpu_mem}")
    if decode_cpu_nr:
        decode_limits_resources.append(f"        cpu: \"{decode_cpu_nr}\"")
        decode_requests_resources.append(f"        cpu: \"{decode_cpu_nr}\"")
    if ephemeral_storage_resource and decode_ephemeral_storage_nr:
        decode_limits_resources.append(f"        {ephemeral_storage_resource}: \"{decode_ephemeral_storage_nr}\"")
        decode_requests_resources.append(f"        {ephemeral_storage_resource}: \"{decode_ephemeral_storage_nr}\"")
    if accelerator_resource and decode_accelerator_count and str(decode_accelerator_count) != "0":
        decode_limits_resources.append(f"        {accelerator_resource}: \"{decode_accelerator_count}\"")
        decode_requests_resources.append(f"        {accelerator_resource}: \"{decode_accelerator_count}\"")
    if decode_network_resource and decode_network_nr:
        decode_limits_resources.append(f"        {decode_network_resource}: \"{decode_network_nr}\"")
        decode_requests_resources.append(f"        {decode_network_resource}: \"{decode_network_nr}\"")

    # Build prefill resources section cleanly
    prefill_limits_resources = []
    prefill_requests_resources = []

    if prefill_cpu_mem:
        prefill_limits_resources.append(f"        memory: {prefill_cpu_mem}")
        prefill_requests_resources.append(f"        memory: {prefill_cpu_mem}")
    if prefill_cpu_nr:
        prefill_limits_resources.append(f"        cpu: \"{prefill_cpu_nr}\"")
        prefill_requests_resources.append(f"        cpu: \"{prefill_cpu_nr}\"")
    if ephemeral_storage_resource and prefill_ephemeral_storage_nr:
        prefill_limits_resources.append(f"        {ephemeral_storage_resource}: \"{prefill_ephemeral_storage_nr}\"")
        prefill_requests_resources.append(f"        {ephemeral_storage_resource}: \"{prefill_ephemeral_storage_nr}\"")
    if accelerator_resource and prefill_accelerator_count and str(prefill_accelerator_count) != "0":
        prefill_limits_resources.append(f"        {accelerator_resource}: \"{prefill_accelerator_count}\"")
        prefill_requests_resources.append(f"        {accelerator_resource}: \"{prefill_accelerator_count}\"")
    if prefill_network_resource and prefill_network_nr:
        prefill_limits_resources.append(f"        {prefill_network_resource}: \"{prefill_network_nr}\"")
        prefill_requests_resources.append(f"        {prefill_network_resource}: \"{prefill_network_nr}\"")

    # Join resources with newlines
    decode_limits_str = "\n".join(decode_limits_resources) if decode_limits_resources else "        {}"
    decode_requests_str = "\n".join(decode_requests_resources) if decode_requests_resources else "        {}"
    prefill_limits_str = "\n".join(prefill_limits_resources) if prefill_limits_resources else "        {}"
    prefill_requests_str = "\n".join(prefill_requests_resources) if prefill_requests_resources else "        {}"

    # Handle command sections
    decode_command_section = add_command(decode_model_command) if decode_model_command else ""
    decode_args_section = add_command_line_options(decode_extra_args).lstrip() if decode_extra_args else ""
    prefill_command_section = add_command(prefill_model_command) if prefill_model_command else ""
    prefill_args_section = add_command_line_options(prefill_extra_args).lstrip() if prefill_extra_args else ""

    # Build the complete YAML structure with proper handling of empty values
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
      timeouts:
        backendRequest: 0s
        request: 0s
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
      {functions_add_annotations("LLMDBENCH_VLLM_COMMON_ANNOTATIONS").lstrip()}
  podAnnotations:
      {functions_add_annotations("LLMDBENCH_VLLM_MODELSERVICE_DECODE_PODANNOTATIONS").lstrip()}
  {conditional_extra_config(decode_extra_pod_config, 2, "extraConfig")}
  containers:
  - name: "vllm"
    mountModelVolume: {str(mount_model_volume).lower()}
    image: "{main_image}"
    modelCommand: {decode_model_command or '""'}
    {decode_command_section}
    args:
      {decode_args_section}
    env:
      - name: VLLM_NIXL_SIDE_CHANNEL_HOST
        valueFrom:
          fieldRef:
            fieldPath: status.podIP
      {functions_add_additional_env_to_yaml(envvars_to_yaml).lstrip()}
    resources:
      limits:
{decode_limits_str}
      requests:
{decode_requests_str}
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
    {functions_add_config(decode_extra_container_config, 6).lstrip()}
    {conditional_volume_config(decode_extra_volume_mounts, "volumeMounts", 4)}
  {conditional_volume_config(decode_extra_volumes, "volumes", 2)}

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
      {functions_add_annotations("LLMDBENCH_VLLM_COMMON_ANNOTATIONS").lstrip()}
  podAnnotations:
      {functions_add_annotations("LLMDBENCH_VLLM_MODELSERVICE_PREFILL_PODANNOTATIONS").lstrip()}
  {conditional_extra_config(prefill_extra_pod_config, 2, "extraConfig")}
  containers:
  - name: "vllm"
    mountModelVolume: {str(mount_model_volume).lower()}
    image: "{main_image}"
    modelCommand: {prefill_model_command or '""'}
    {prefill_command_section}
    args:
      {prefill_args_section}
    env:
      - name: VLLM_IS_PREFILL
        value: "1"
      - name: VLLM_NIXL_SIDE_CHANNEL_HOST
        valueFrom:
          fieldRef:
            fieldPath: status.podIP
      {functions_add_additional_env_to_yaml(envvars_to_yaml).lstrip()}
    resources:
      limits:
{prefill_limits_str}
      requests:
{prefill_requests_str}
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
    {functions_add_config(prefill_extra_container_config, 6).lstrip()}
    {conditional_volume_config(prefill_extra_volume_mounts, "volumeMounts", 4)}
  {conditional_volume_config(prefill_extra_volumes, "volumes", 2)}
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
    if not check_affinity(ev):
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

        # Set proper defaults for empty configurations
        add_config_prep()

        # Generate ms-rules.yaml content
        rules_file = helm_dir / "ms-rules.yaml"

        # For single model, write routing rule; otherwise empty
        if len([m for m in model_list if m.strip()]) == 1:
            rules_content = f"""- backendRefs:
      - group: inference.networking.x-k8s.io
        kind: InferencePool
        name: {current_model_id_label}-gaie
        port: 8000
        weight: 1
      timeouts:
        backendRequest: 0s
        request: 0s
"""
            rules_file.write_text(rules_content)
        else:
            rules_file.write_text("")


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