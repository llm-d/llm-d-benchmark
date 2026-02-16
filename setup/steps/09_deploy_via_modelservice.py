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
    add_pull_secret,
    check_storage_class,
    check_accelerator,
    check_network,
    discover_node_resources,
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
    add_scc_to_service_account,
    clear_string,
    install_wva_components,
    kube_connect,
    kubectl_apply,
    auto_detect_version,
    propagate_standup_parameters
)

def conditional_volume_config(
    volume_config_key: str, field_name: str, indent: int = 4, ev: dict = {}
) -> str:
    """
    Generate volume configuration only if the config is not empty.
    Skip the field entirely if the volume config is empty or contains only "[]" or "{}".
    """
    config_result = add_config(volume_config_key, indent, "", ev)
    if config_result.strip():
        return f"{field_name}: {config_result}"
    return ""


def conditional_extra_config(
    extra_config_key: str, indent: int = 2, label: str = "extraConfig", ev: dict = {}
) -> str:
    """
    Generate extraConfig section only if the config is not empty.
    Skip the field entirely if the config is empty or contains only "{}" or "[]".
    """
    extra_config = ev[extra_config_key]
    # Check if config is empty before processing
    if not extra_config or extra_config.strip() in ["{}", "[]", "#no____config"]:
        return ""

    config_result = add_config(extra_config_key, indent + 2, "", ev)  # Add extra indent for content
    if config_result.strip():
        spaces = " " * indent
        return f"{spaces}{label}:\n{config_result}"
    return ""

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
    decode_create = "true" if ev["vllm_modelservice_decode_replicas"] > 0 else "false"
    prefill_create = "true" if ev["vllm_modelservice_prefill_replicas"] > 0 else "false"

    # Build decode resources section cleanly
    decode_limits_str, decode_requests_str = add_resources(ev, "decode")
    prefill_limits_str, prefill_requests_str = add_resources(ev, "prefill")

    # Build the complete YAML structure with proper handling of empty values
    yaml_content = f"""fullnameOverride: {ev["deploy_current_model_id_label"]}
multinode: {ev["vllm_modelservice_multinode"]}

schedulerName: {ev['vllm_common_pod_scheduler']}

modelArtifacts:
  uri: {ev["vllm_modelservice_uri"]}
  size: {ev["vllm_common_pvc_model_cache_size"]}
  authSecretName: "llm-d-hf-token"
  name: {ev["deploy_current_model"]}
  labels:
    llm-d.ai/inferenceServing: "true"
    llm-d.ai/model: {ev["deploy_current_model_id_label"]}

routing:
  servicePort: {ev["vllm_common_inference_port"]}
  proxy:
    image: "{get_image(ev, "llmd_routingsidecar_image", False, True)}"
    secure: false
    connector: {ev["llmd_routingsidecar_connector"]}
    debugLevel: {ev["llmd_routingsidecar_debug_level"]}

{add_accelerator(ev)}

decode:
  create: {decode_create}
  replicas: {ev["vllm_modelservice_decode_replicas"]}
{add_affinity(ev)}
  parallelism:
    data: {ev["vllm_modelservice_decode_data_parallelism"]}
    dataLocal: {ev["vllm_modelservice_decode_data_local_parallelism"]}
    tensor: {ev["vllm_modelservice_decode_tensor_parallelism"]}
    workers: {ev["vllm_modelservice_decode_num_workers_parallelism"]}
  annotations:
      {add_annotations(ev, "LLMDBENCH_VLLM_COMMON_ANNOTATIONS").lstrip()}
  podAnnotations:
      {add_annotations(ev, "LLMDBENCH_VLLM_MODELSERVICE_DECODE_PODANNOTATIONS").lstrip()}
  schedulerName: {ev['vllm_common_pod_scheduler']}
  extraConfig:
{add_pull_secret(ev)}
{conditional_extra_config("vllm_modelservice_decode_extra_pod_config", 2, "", ev)}
  containers:
  - name: "vllm"
    mountModelVolume: {str(mount_model_volume).lower()}
    image: "{get_image(ev, "llmd_image", False, True)}"
    modelCommand: {ev["vllm_modelservice_decode_model_command"]}
    {add_command(ev, "vllm_modelservice_decode_model_command")}
    args:
{add_command_line_options(ev, "vllm_modelservice_decode_extra_args")}
    env:
      - name: VLLM_IS_DECODE
        value: "1"
      {add_additional_env_to_yaml(ev, "vllm_modelservice_decode_envvars_to_yaml").lstrip()}
    resources:
      limits:
{decode_limits_str}
      requests:
{decode_requests_str}
    extraConfig:
      startupProbe:
        httpGet:
          path: /health
          port: {ev["vllm_modelservice_decode_inference_port"]}
        failureThreshold: 60
        initialDelaySeconds: {ev["vllm_common_initial_delay_probe"]}
        periodSeconds: 30
        timeoutSeconds: 5
      livenessProbe:
        tcpSocket:
          port: {ev["vllm_modelservice_decode_inference_port"]}
        failureThreshold: 3
        periodSeconds: 5
      readinessProbe:
        httpGet:
          path: /health
          port: {ev["vllm_modelservice_decode_inference_port"]}
        failureThreshold: 3
        periodSeconds: 5
      {add_config("vllm_modelservice_decode_extra_container_config", 6, "", ev).lstrip()}
    {conditional_volume_config("vllm_modelservice_decode_extra_volume_mounts", "volumeMounts", 4, ev)}
  {conditional_volume_config("vllm_modelservice_decode_extra_volumes", "volumes", 2, ev)}

prefill:
  create: {prefill_create}
  replicas: {ev["vllm_modelservice_prefill_replicas"]}
{add_affinity(ev)}
  parallelism:
    data: {ev["vllm_modelservice_prefill_data_parallelism"]}
    dataLocal: {ev["vllm_modelservice_prefill_data_local_parallelism"]}
    tensor: {ev["vllm_modelservice_prefill_tensor_parallelism"]}
    workers: {ev["vllm_modelservice_prefill_num_workers_parallelism"]}
  annotations:
      {add_annotations(ev, "LLMDBENCH_VLLM_COMMON_ANNOTATIONS").lstrip()}
  podAnnotations:
      {add_annotations(ev, "LLMDBENCH_VLLM_MODELSERVICE_PREFILL_PODANNOTATIONS").lstrip()}
  schedulerName: {ev['vllm_common_pod_scheduler']}
  extraConfig:
{add_pull_secret(ev)}
{conditional_extra_config("vllm_modelservice_prefill_extra_pod_config", 2, "", ev)}
  containers:
  - name: "vllm"
    mountModelVolume: {str(mount_model_volume).lower()}
    image: "{get_image(ev, "llmd_image", False, True)}"
    modelCommand: {ev["vllm_modelservice_prefill_model_command"]}
    {add_command(ev, "vllm_modelservice_prefill_model_command")}
    args:
{add_command_line_options(ev, "vllm_modelservice_prefill_extra_args")}
    env:
      - name: VLLM_IS_PREFILL
        value: "1"
      {add_additional_env_to_yaml(ev, "vllm_modelservice_prefill_envvars_to_yaml").lstrip()}
    resources:
      limits:
{prefill_limits_str}
      requests:
{prefill_requests_str}
    extraConfig:
      startupProbe:
        httpGet:
          path: /health
          port: {ev["vllm_modelservice_prefill_inference_port"]}
        failureThreshold: 60
        initialDelaySeconds: {ev["vllm_common_initial_delay_probe"]}
        periodSeconds: 30
        timeoutSeconds: 5
      livenessProbe:
        tcpSocket:
          port: {ev["vllm_modelservice_prefill_inference_port"]}
        failureThreshold: 3
        periodSeconds: 5
      readinessProbe:
        httpGet:
          path: /health
          port: {ev["vllm_modelservice_prefill_inference_port"]}
        failureThreshold: 3
        periodSeconds: 5
      {add_config("vllm_modelservice_prefill_extra_container_config", 6, "", ev).lstrip()}
    {conditional_volume_config("vllm_modelservice_prefill_extra_volume_mounts", "volumeMounts", 4, ev)}
  {conditional_volume_config("vllm_modelservice_prefill_extra_volumes", "volumes", 2, ev)}
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
      - group: {ev['vllm_modelservice_inferencepool_api'].split('/')[0]}
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
      - group: {ev['vllm_modelservice_inferencepool_api'].split('/')[0]}
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

    # Parse environment variables into ev dictionary
    ev = {'current_step_name': os.path.splitext(os.path.basename(__file__))[0] }
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

    if not discover_node_resources(ev):
        announce("ERROR: Failed to discover resources on nodes")
        return 1

    if not check_accelerator(ev):
        announce("ERROR: Failed to check accelerator")
        return 1

    if not check_network(ev):
        announce("ERROR: Failed to check network")
        return 1

    # Deploy models
    model_list = ev["deploy_model_list"].replace(",", " ").split()
    model_number = 0

    get_image(
        ev,
        "llmd_inferencescheduler_image",
        True,
        True
    )

    auto_detect_version(ev, ev['vllm_modelservice_chart_name'], "vllm_modelservice_chart_version", "vllm_modelservice_helm_repository", True)
    auto_detect_version(ev, ev['vllm_infra_chart_name'], "vllm_infra_chart_version", "vllm_infra_helm_repository", True)

    for model in model_list:
      if not model.strip():
          continue

      ev["deploy_current_model"] = model_attribute(model, "model", ev)
      ev["deploy_current_model_id"] = model_attribute(model, "modelid", ev)
      ev["deploy_current_model_id_label"] = model_attribute(model, "modelid_label", ev)
      ev["deploy_current_service_name"] = (
          f'{model_attribute(model, "modelid_label", ev)}-gaie-epp'
      )

      # Determine model mounting
      mount_model_volume = False
      if (
          ev["vllm_modelservice_uri_protocol"] == "pvc"
          or ev["control_environment_type_standalone_active"]
      ):
          pvc_name = ev["vllm_common_pvc_name"]
          ev["vllm_modelservice_uri"] = (
              f"pvc://{pvc_name}/models/{ev['deploy_current_model']}"
          )
          mount_model_volume = True
      else:
          ev["vllm_modelservice_uri"] = (
              f"hf://{ev['deploy_current_model']}"
          )
          mount_model_volume = True

      # Check for mount override
      mount_override = ev["vllm_modelservice_mount_model_volume_override"]
      if mount_override:
          mount_model_volume = mount_override == "true"

      # Create directory structure (Do not use "llmdbench_execute_cmd" for these commands)
      model_num = f"{model_number:02d}"
      release = ev["vllm_modelservice_release"]
      work_dir = Path(ev.get("control_work_dir", ""))
      helm_dir = work_dir / "setup" / "helm" / release / model_num

      # Always create directory structure (even in dry-run)
      helm_dir.mkdir(parents=True, exist_ok=True)

      # Generate ms-rules.yaml content
      rules_file = helm_dir / "ms-rules.yaml"
      rules_file.write_text("")

      # Generate ms-values.yaml
      values_content = generate_ms_values_yaml(ev, mount_model_volume, rules_file)
      values_file = helm_dir / "ms-values.yaml"
      values_file.write_text(values_content)

      # Clean up temp file
      rules_file.unlink()

      api, client = kube_connect(f'{ev["control_work_dir"]}/environment/context.ctx')

      # Pods are created on the service account named after model_id_label
      if values_content.count("runAsGroup: 0") or values_content.count("runAsUser: 0") :
        add_scc_to_service_account(
            api,
            "anyuid",
            ev["deploy_current_model_id_label"],
            ev["vllm_common_namespace"],
            ev["control_dry_run"],
        )
        add_scc_to_service_account(
            api,
            "privileged",
            ev["deploy_current_model_id_label"],
            ev["vllm_common_namespace"],
            ev["control_dry_run"],
        )

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
              f"ERROR: Failed to deploy helm chart for model {ev['deploy_current_model']}"
          )
          return result

      announce(
          f"✅ {ev['vllm_common_namespace']}-{ev['deploy_current_model_id_label']}-ms helm chart deployed successfully"
      )

      httproute_spec = define_httproute(ev, single_model = len([m for m in model_list if m.strip()]) == 1)
      with open(f'{ev["control_work_dir"]}/setup/yamls/{ev["current_step_nr"]}_httproute.yaml', "w") as f:
          f.write(httproute_spec)

      kubectl_apply(api=api, manifest_data=httproute_spec, dry_run=ev["control_dry_run"])

      expected_num_decode_pods = ev["vllm_modelservice_decode_replicas"]
      if ev["vllm_modelservice_multinode"] :
          expected_num_decode_pods = ev["vllm_modelservice_decode_num_workers_parallelism"] * expected_num_decode_pods

      # Wait for decode pods to be created, running, and ready
      api_client = client.CoreV1Api()
      result = wait_for_pods_created_running_ready(
          api_client, ev, expected_num_decode_pods, "decode"
      )
      if result != 0:
          return result

      expected_num_prefill_pods = ev["vllm_modelservice_prefill_replicas"]
      if ev["vllm_modelservice_multinode"] :
          expected_num_prefill_pods = ev["vllm_modelservice_prefill_num_workers_parallelism"] * expected_num_prefill_pods

      # Wait for prefill pods to be created, running, and ready
      result = wait_for_pods_created_running_ready(
          api_client, ev, expected_num_prefill_pods, "prefill"
      )
      if result != 0:
          return result

      result = wait_for_pods_created_running_ready(
          api_client, ev, 1, "inferencepool"
      )
      if result != 0:
          return result

      # Collect decode logs
      collect_logs(ev, ev["vllm_modelservice_decode_replicas"], "decode")

      # Collect prefill logs
      collect_logs(ev, ev["vllm_modelservice_prefill_replicas"], "prefill")

      announce(f"📜 Labelling gateway for model \"{model}\"")
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

      model_number += 1

    announce("✅ modelservice completed model deployment")
    propagate_standup_parameters(ev, api)
    return 0


if __name__ == "__main__":
    sys.exit(main())
