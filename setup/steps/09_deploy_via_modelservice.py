#!/usr/bin/env python3

import os
import sys
from pathlib import Path

import pykube

# Add project root to path for imports
current_file = Path(__file__).resolve()
project_root = current_file.parents[1]
sys.path.insert(0, str(project_root))

# Import from functions.py
from functions import (
    announce,
    llmdbench_execute_cmd,
    model_attribute,
    extract_environment,
    check_storage_class,
    check_affinity,
    environment_variable_to_dict,
    wait_for_pods_created_running_ready,
    collect_logs,
    get_image,
    add_command,
    add_command_line_options,
    add_annotations,
    add_additional_env_to_yaml,
    add_config,
    add_resources,
    add_accelerator,
    add_affinity,
    clear_string,
    install_wva_components,
    kube_connect,
    kubectl_apply
)

def conditional_volume_config(
    volume_config: str, field_name: str, indent: int = 4
) -> str:
    """
    Generate volume configuration only if the config is not empty.
    Skip the field entirely if the volume config is empty or contains only "[]" or "{}".
    """
    config_result = add_config(volume_config, indent)
    if config_result.strip():
        return f"{field_name}: {config_result}"
    return ""


def conditional_extra_config(
    extra_config: str, indent: int = 2, label: str = "extraConfig"
) -> str:
    """
    Generate extraConfig section only if the config is not empty.
    Skip the field entirely if the config is empty or contains only "{}" or "[]".
    """
    # Check if config is empty before processing
    if not extra_config or extra_config.strip() in ["{}", "[]", "#no____config"]:
        return ""

    config_result = add_config(extra_config, indent + 2)  # Add extra indent for content
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


def generate_ms_values_yaml(
    ev: dict, mount_model_volume: bool, rules_file: Path
) -> str:
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
    model_id_label = ev.get("deploy_current_model_id_label", "")

    # Image details
    image_registry = ev.get("llmd_image_registry", "")
    image_repo = ev.get("llmd_image_repo", "")
    image_name = ev.get("llmd_image_name", "")
    image_tag = ev.get("llmd_image_tag", "")
    main_image = get_image(image_registry, image_repo, image_name, image_tag, False, True)

    # Proxy details
    proxy_image_registry = ev.get("llmd_routingsidecar_image_registry", "")
    proxy_image_repo = ev.get("llmd_routingsidecar_image_repo", "")
    proxy_image_name = ev.get("llmd_routingsidecar_image_name", "")
    proxy_image_tag = ev.get("llmd_routingsidecar_image_tag", "")
    proxy_image = get_image(
        proxy_image_registry, proxy_image_repo, proxy_image_name, proxy_image_tag, False, True
    )
    proxy_connector = ev.get("llmd_routingsidecar_connector", "")
    proxy_debug_level = ev.get("llmd_routingsidecar_debug_level", "")

    # Decode configuration
    decode_replicas = int(ev.get("vllm_modelservice_decode_replicas", "0"))
    decode_create = "true" if decode_replicas > 0 else "false"
    decode_data_parallelism = ev.get("vllm_modelservice_decode_data_parallelism", "1")
    decode_tensor_parallelism = ev["vllm_modelservice_decode_tensor_parallelism"]
    decode_model_command = ev.get("vllm_modelservice_decode_model_command", "")
    decode_extra_args = ev.get("vllm_modelservice_decode_extra_args", "")
    decode_inference_port = ev["vllm_modelservice_decode_inference_port"]

    # Prefill configuration
    prefill_replicas = int(ev.get("vllm_modelservice_prefill_replicas", "0"))
    prefill_create = "true" if prefill_replicas > 0 else "false"
    prefill_data_parallelism = ev.get("vllm_modelservice_prefill_data_parallelism", "1")
    prefill_tensor_parallelism = ev.get(
        "vllm_modelservice_prefill_tensor_parallelism", "1"
    )
    prefill_model_command = ev.get("vllm_modelservice_prefill_model_command", "")
    prefill_extra_args = ev.get("vllm_modelservice_prefill_extra_args", "")
    prefill_inference_port = ev["vllm_modelservice_prefill_inference_port"]

    # Probe configuration
    initial_delay_probe = ev.get("vllm_common_initial_delay_probe", "30")
    common_inference_port = ev.get("vllm_common_inference_port", "8000")

    # Extra configurations
    decode_extra_pod_config = ev.get("vllm_modelservice_decode_extra_pod_config", "")
    decode_extra_container_config = ev.get(
        "vllm_modelservice_decode_extra_container_config", ""
    )
    decode_extra_volume_mounts = ev.get(
        "vllm_modelservice_decode_extra_volume_mounts", ""
    )
    decode_extra_volumes = ev.get("vllm_modelservice_decode_extra_volumes", "")

    prefill_extra_pod_config = ev.get("vllm_modelservice_prefill_extra_pod_config", "")
    prefill_extra_container_config = ev.get(
        "vllm_modelservice_prefill_extra_container_config", ""
    )
    prefill_extra_volume_mounts = ev.get(
        "vllm_modelservice_prefill_extra_volume_mounts", ""
    )
    prefill_extra_volumes = ev.get("vllm_modelservice_prefill_extra_volumes", "")

    # Environment variables to YAML
    envvars_to_yaml = ev.get("vllm_common_envvars_to_yaml", "")

    # Build decode resources section cleanly
    decode_limits_str, decode_requests_str = add_resources(ev, "decode")
    prefill_limits_str, prefill_requests_str = add_resources(ev, "prefill")

    # Handle command sections
    decode_command_section = (
        add_command(decode_model_command) if decode_model_command else ""
    )
    decode_args_section = (
        add_command_line_options(decode_extra_args).lstrip()
        if decode_extra_args
        else ""
    )
    prefill_command_section = (
        add_command(prefill_model_command) if prefill_model_command else ""
    )
    prefill_args_section = (
        add_command_line_options(prefill_extra_args).lstrip()
        if prefill_extra_args
        else ""
    )

    # Build the complete YAML structure with proper handling of empty values
    yaml_content = f"""fullnameOverride: {fullname_override}
multinode: {multinode}

schedulerName: {ev['vllm_common_pod_scheduler']}

modelArtifacts:
  uri: {model_uri}
  size: {model_size}
  authSecretName: "llm-d-hf-token"
  name: {model_name}
  labels:
    llm-d.ai/inferenceServing: "true"
    llm-d.ai/model: {model_id_label}

routing:
  servicePort: {service_port}
  proxy:
    image: "{proxy_image}"
    secure: false
    connector: {proxy_connector}
    debugLevel: {proxy_debug_level}

{add_accelerator(ev)}

decode:
  create: {decode_create}
  replicas: {decode_replicas}
{add_affinity(ev)}
  parallelism:
    data: {decode_data_parallelism}
    tensor: {decode_tensor_parallelism}
  annotations:
      {add_annotations("LLMDBENCH_VLLM_COMMON_ANNOTATIONS").lstrip()}
  podAnnotations:
      {add_annotations("LLMDBENCH_VLLM_MODELSERVICE_DECODE_PODANNOTATIONS").lstrip()}
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
      {add_additional_env_to_yaml(ev, envvars_to_yaml).lstrip()}
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
    {add_config(decode_extra_container_config, 6).lstrip()}
    {conditional_volume_config(decode_extra_volume_mounts, "volumeMounts", 4)}
  {conditional_volume_config(decode_extra_volumes, "volumes", 2)}

prefill:
  create: {prefill_create}
  replicas: {prefill_replicas}
{add_affinity(ev)}
  parallelism:
    data: {prefill_data_parallelism}
    tensor: {prefill_tensor_parallelism}
  annotations:
      {add_annotations("LLMDBENCH_VLLM_COMMON_ANNOTATIONS").lstrip()}
  podAnnotations:
      {add_annotations("LLMDBENCH_VLLM_MODELSERVICE_PREFILL_PODANNOTATIONS").lstrip()}
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
      {add_additional_env_to_yaml(ev, envvars_to_yaml).lstrip()}
    resources:
      limits:
{prefill_limits_str}
      requests:
{prefill_requests_str}
    extraConfig:
      startupProbe:
        httpGet:
          path: /health
          port: {prefill_inference_port}
        failureThreshold: 60
        initialDelaySeconds: {initial_delay_probe}
        periodSeconds: 30
        timeoutSeconds: 5
      livenessProbe:
        tcpSocket:
          port: {prefill_inference_port}
        failureThreshold: 3
        periodSeconds: 5
      readinessProbe:
        httpGet:
          path: /health
          port: {prefill_inference_port}
        failureThreshold: 3
        periodSeconds: 5
    {add_config(prefill_extra_container_config, 6).lstrip()}
    {conditional_volume_config(prefill_extra_volume_mounts, "volumeMounts", 4)}
  {conditional_volume_config(prefill_extra_volumes, "volumes", 2)}
"""

    return clear_string(yaml_content)

def define_httproute(
    ev: dict,
    single_model: bool = True
) -> str:
    """
    Generate the ms-values.yaml content for Helm chart.
    Exactly matches the bash script structure from lines 60-239.

    Args:
        ev: Environment variables dictionary
        single_model: indicates only one model will be deployed

    Returns:
        YAML manifest for HTTPRoute
"""
    release = ev["vllm_modelservice_release"]
    namespace = ev.get("vllm_common_namespace", "")
    model_id_label = ev.get("deploy_current_model_id_label", "")
    service_port = ev.get("vllm_common_inference_port", "8000")

    manifest=f"""apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: {model_id_label}
  namespace: {namespace}
spec:
  parentRefs:
    - group: gateway.networking.k8s.io
      kind: Gateway
      name: infra-{release}-inference-gateway
  rules:
    - backendRefs:
      - group: inference.networking.x-k8s.io
        kind: InferencePool
        name: {model_id_label}-gaie
        port: {service_port}
        weight: 1
      timeouts:
        backendRequest: 0s
        request: 0s
      matches:
        - path:
            type: PathPrefix
            value: /{model_id_label}/
      filters:
        - type: URLRewrite
          urlRewrite:
            path:
              type: ReplacePrefixMatch
              replacePrefixMatch: /
"""
    # For single model case, create simpler rule
    if single_model:
      manifest = f"""{manifest}
    - backendRefs:
      - group: inference.networking.x-k8s.io
        kind: InferencePool
        name: {model_id_label}-gaie
        port: {service_port}
        weight: 1
      timeouts:
        backendRequest: 0s
        request: 0s
"""
    return manifest

def main():
    """Main function for step 09 - Deploy via modelservice"""

    # Set current step for functions.py compatibility
    os.environ["LLMDBENCH_CURRENT_STEP"] = os.path.splitext(os.path.basename(__file__))[0]

    # Parse environment variables into ev dictionary
    ev = {}
    environment_variable_to_dict(ev)

    # Check if modelservice environment is active
    if not ev["control_environment_type_modelservice_active"]:
        announce(
            f"⏭️ Environment types are \"{ev['deploy_methods']}\". Skipping this step."
        )
        return 0

    # Check storage class
    if not check_storage_class(ev):
        announce("ERROR: Failed to check storage class")
        return 1

    # Check affinity
    if not check_affinity(ev):
        announce("ERROR: Failed to check affinity")
        return 1

    # Extract environment for debugging
    extract_environment(ev)

    # Deploy models
    model_list = ev["deploy_model_list"].replace(",", " ").split()
    model_number = 0

    for model in model_list:
        if not model.strip():
            continue

        # FIXME add_additional_env_to_yaml is still using os.environ
        # Set current model environment variables
        os.environ["LLMDBENCH_DEPLOY_CURRENT_MODEL"] = model_attribute(model, "model")
        os.environ["LLMDBENCH_DEPLOY_CURRENT_MODEL_ID"] = model_attribute(
            model, "modelid"
        )
        os.environ["LLMDBENCH_DEPLOY_CURRENT_MODEL_ID_LABEL"] = model_attribute(
            model, "modelid_label"
        )
        os.environ["LLMDBENCH_DEPLOY_CURRENT_SERVICE_NAME"] = (
            f'{model_attribute(model, "modelid_label")}-gaie-epp'
        )

        environment_variable_to_dict(ev)

        # Determine model mounting
        mount_model_volume = False
        if (
            ev["vllm_modelservice_uri_protocol"] == "pvc"
            or ev["control_environment_type_standalone_active"]
        ):
            pvc_name = ev["vllm_common_pvc_name"]
            # FIXME add_additional_env_to_yaml is still using os.environ
            os.environ["LLMDBENCH_VLLM_MODELSERVICE_URI"] = (
                f"pvc://{pvc_name}/models/{ev['deploy_current_model']}"
            )
            mount_model_volume = True
        else:
            # FIXME add_additional_env_to_yaml is still using os.environ
            os.environ["LLMDBENCH_VLLM_MODELSERVICE_URI"] = (
                f"hf://{ev['deploy_current_model']}"
            )
            mount_model_volume = True

        # Check for mount override
        mount_override = ev["vllm_modelservice_mount_model_volume_override"]
        if mount_override:
            mount_model_volume = mount_override == "true"

        # Update ev with URI
        environment_variable_to_dict(ev)

        # Create directory structure (Do not use "llmdbench_execute_cmd" for these commands)
        model_num = f"{model_number:02d}"
        release = ev["vllm_modelservice_release"]
        work_dir = Path(ev.get("control_work_dir", ""))
        helm_dir = work_dir / "setup" / "helm" / release / model_num

        # Always create directory structure (even in dry-run)
        helm_dir.mkdir(parents=True, exist_ok=True)

        # Set proper defaults for empty configurations
        add_config_prep()

        # Generate ms-rules.yaml content
        rules_file = helm_dir / "ms-rules.yaml"
        rules_file.write_text("")

        # Generate ms-values.yaml
        values_content = generate_ms_values_yaml(ev, mount_model_volume, rules_file)
        values_file = helm_dir / "ms-values.yaml"
        values_file.write_text(values_content)

        # Clean up temp file
        rules_file.unlink()

        # Deploy via helmfile
        announce(f'🚀 Installing helm chart "ms-{release}" via helmfile...')
        context_path = work_dir / "environment" / "context.ctx"

        helmfile_cmd = (
            f"helmfile --namespace {ev['vllm_common_namespace']} "
            f"--kubeconfig {context_path} "
            f"--selector name={ev['deploy_current_model_id_label']}-ms "
            f"apply -f {work_dir}/setup/helm/{release}/helmfile-{model_num}.yaml --skip-diff-on-install --skip-schema-validation"
        )

        result = llmdbench_execute_cmd(
            helmfile_cmd, ev["control_dry_run"], ev["control_verbose"]
        )
        if result != 0:
            announce(
                f"❌ Failed to deploy helm chart for model {ev['deploy_current_model']}"
            )
            return result

        announce(
            f"✅ {ev['vllm_common_namespace']}-{ev['deploy_current_model_id_label']}-ms helm chart deployed successfully"
        )

        api, client = kube_connect(f'{ev["control_work_dir"]}/environment/context.ctx')
        httproute_spec = define_httproute(ev, single_model = len([m for m in model_list if m.strip()]) == 1)
        kubectl_apply(api=api, manifest_data=httproute_spec, dry_run=ev["control_dry_run"])

        # Wait for decode pods to be created, running, and ready
        api_client = client.CoreV1Api()
        result = wait_for_pods_created_running_ready(
            api_client, ev, ev["vllm_modelservice_decode_replicas"], "decode"
        )
        if result != 0:
            return result

        # Wait for prefill pods to be created, running, and ready
        result = wait_for_pods_created_running_ready(
            api_client, ev, ev["vllm_modelservice_prefill_replicas"], "prefill"
        )
        if result != 0:
            return result

        if result != 0:
            return result

        # Collect decode logs
        collect_logs(ev, ev["vllm_modelservice_decode_replicas"], "decode")

        # Collect prefill logs
        collect_logs(ev, ev["vllm_modelservice_prefill_replicas"], "prefill")

        announce(f"📜 Labelling gateway for model  \"{model}\"")
        label_gateway_cmd = f"{ev['control_kcmd']} --namespace  {ev['vllm_common_namespace']} label gateway/infra-{release}-inference-gateway stood-up-by={ev['control_username']} stood-up-from=llm-d-benchmark stood-up-via={ev['deploy_methods']}"
        result = llmdbench_execute_cmd(label_gateway_cmd, ev["control_dry_run"], ev["control_verbose"])
        if result != 0:
            announce(f"ERROR: Unable to label gateway for model \"{model}\"")
        else :
          announce(f"✅ Service for pods service model {model} created")

        service_name = ''

        if ev['vllm_modelservice_gateway_class_name'] == "kgateway" :
          service_name = f"infra-{release}-inference-gateway"

        if ev['vllm_modelservice_gateway_class_name'] == "istio" :
          service_name = f"{ev['deploy_current_model_id_label']}-gaie-epp"

        # Handle OpenShift route creation
        if ev["vllm_modelservice_route"] and ev["control_deploy_is_openshift"] == "1" and service_name:

            # Check if route exists
            route_name = f"{release}-inference-gateway-route"
            check_route_cmd = f"{ev['control_kcmd']} --namespace {ev['vllm_common_namespace']} get route -o name --ignore-not-found | grep -E \"/{route_name}$\""
            ecode = llmdbench_execute_cmd(check_route_cmd, ev["control_dry_run"], ev["control_verbose"], True, 1, False)
            if ecode != 0:  # Route doesn't exist
                announce(f"📜 Exposing service \"{service_name}\" (serving model {model}) as a route ...")
                inference_port = ev["vllm_common_inference_port"]
                expose_cmd = (
                    f"{ev['control_kcmd']} --namespace {ev['vllm_common_namespace']} expose service/{service_name} "
                    f"--target-port={inference_port} --name={route_name}"
                )

                ecode = llmdbench_execute_cmd(
                    expose_cmd, ev["control_dry_run"], ev["control_verbose"]
                )
                if ecode == 0:
                    announce(f"✅ route service \"{service_name}\" (serving model {model})created")

        announce(f'✅ Model "{model}" and associated service deployed.')

        if ev["wva_enabled"] and ev["control_deploy_is_openshift"] == "1":
            #
            # Right now we have only verified this installation path for OC and not other mediums like kind
            # so lets not find out until we actually test those paths...it is supported according to WVA
            # but we have not invested on testing there yet.
            #
            install_wva_components(ev)
            announce(f'✅ WVA has been configured for Model "{model}".')

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
