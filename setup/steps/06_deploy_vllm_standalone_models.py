#!/usr/bin/env python3

import os
import sys
from pathlib import Path

# Add project root to path for imports
current_file = Path(__file__).resolve()
project_root = current_file.parents[1]
sys.path.insert(0, str(project_root))

# Import from functions.py
from functions import (
    announce, \
    llmdbench_execute_cmd, \
    model_attribute, \
    extract_environment, \
    get_image, \
    check_storage_class, \
    check_accelerator, \
    check_network, \
    discover_node_resources, \
    add_annotations, \
    add_command_line_options, \
    add_additional_env_to_yaml, \
    add_resources, \
    add_config, \
    add_affinity, \
    check_priority_class, \
    add_priority_class_name, \
    add_pull_secret, \
    is_standalone_deployment, \
    kubectl_apply, \
    environment_variable_to_dict, \
    wait_for_pods_created_running_ready, \
    kube_connect, \
    collect_logs, \
    propagate_standup_parameters
)

def main():
    """Deploy vLLM standalone models with Kubernetes Deployment, Service, and HTTPRoute."""

    ev = {'current_step_name': os.path.splitext(os.path.basename(__file__))[0] }
    environment_variable_to_dict(ev)

    # Check if standalone environment is active
    if is_standalone_deployment(ev):

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

        if not check_priority_class(ev):
            announce("ERROR: Failed to check priority class")
            return 1

        # Create yamls directory
        yamls_dir = Path(ev["control_work_dir"]) / "setup" / "yamls"
        yamls_dir.mkdir(parents=True, exist_ok=True)

        # Process each model - First pass: Deploy resources
        model_list = ev.get("deploy_model_list", "").replace(",", " ").split()
        for model in model_list:
            # Generate filename-safe model name
            modelfn = model.replace("/", "___")

            # Set current model environment variable
            current_model = model_attribute(model, "model", ev)
            ev["deploy_current_model"] = current_model

            # Get model attributes
            model_label = model_attribute(model, "label", ev)

            # Generate Deployment YAML
            deployment_yaml = generate_deployment_yaml(ev, model, model_label)
            deployment_file = yamls_dir / f"{ev['current_step']}_a_deployment_{modelfn}.yaml"
            with open(deployment_file, 'w') as f:
                f.write(deployment_yaml)

            announce(f"🚚 Deploying model \"{model}\" and associated service (from files located at {ev['control_work_dir']})...")

            # Apply deployment
            kubectl_deploy_cmd = f"{ev['control_kcmd']} apply -f {deployment_file}"
            llmdbench_execute_cmd(actual_cmd=kubectl_deploy_cmd, dry_run=ev["control_dry_run"], verbose=ev["control_verbose"], fatal=True)

            # Generate Service YAML
            service_yaml = generate_service_yaml(ev, model, model_label)
            service_file = yamls_dir / f"{ev['current_step']}_b_service_{modelfn}.yaml"
            with open(service_file, 'w') as f:
                f.write(service_yaml)

            # Apply service
            kubectl_service_cmd = f"{ev['control_kcmd']} apply -f {service_file}"
            llmdbench_execute_cmd(actual_cmd=kubectl_service_cmd, dry_run=ev["control_dry_run"], verbose=ev["control_verbose"], fatal=True)

            # Optional HTTPRoute for OpenShift
            srl = "deployment,service,pods,secrets"
            if ev["control_deploy_is_openshift"] == "1" :
              srl = "deployment,service,route,pods,secrets"
            if ev["vllm_standalone_httproute"] == "1" :
                srl = "deployment,service,httproute,route,pods,secrets"

                # Generate HTTPRoute YAML
                httproute_yaml = generate_httproute_yaml(ev, model, model_label)
                httproute_file = yamls_dir / f"{ev['current_step']}_c_httproute_{modelfn}.yaml"
                with open(httproute_file, 'w') as f:
                    f.write(httproute_yaml)

                # Apply HTTPRoute
                kubectl_apply(api=api, manifest_data=httproute_yaml, dry_run=ev["control_dry_run"])

            announce(f"✅ Model \"{model}\" and associated service deployed.")

        # Second pass: Wait for pods to be ready
        for model in model_list:
            model_label = model_attribute(model, "label", ev)

            ev["deploy_current_model_id_label"] = model_label

            # Wait for vllm pods to be created, running and ready
            api, client = kube_connect(f"{ev['control_work_dir']}/environment/context.ctx")
            api_client = client.CoreV1Api()
            result = wait_for_pods_created_running_ready(api_client, ev, ev["vllm_common_replicas"], "both")
            if result != 0:
                return result

            # Collect decode logs
            collect_logs(ev, ev["vllm_common_replicas"], "both")

            # Handle OpenShift route exposure
            if ev["vllm_standalone_route"] == "1" and ev["control_deploy_is_openshift"] == "1" :

                # Check if route already exists
                route_check_cmd = (
                    f"{ev['control_kcmd']} --namespace {ev['vllm_common_namespace']} get route --ignore-not-found | grep vllm-standalone-{model_label}-route"
                )
                result = llmdbench_execute_cmd(actual_cmd=route_check_cmd, dry_run=ev["control_dry_run"], verbose=ev["control_verbose"], silent=1, attempts=1, fatal=False)
                if result:
                    announce(f"📜 Exposing pods serving model {model} as service...")
                    kubectl_expose_cmd = (
                        f"{ev['control_kcmd']} --namespace {ev['vllm_common_namespace']} expose "
                        f"service/vllm-standalone-{model_label} --namespace {ev['vllm_common_namespace']} "
                        f"--target-port={ev['vllm_common_inference_port']} "
                        f"--name=vllm-standalone-{model_label}-route"
                    )
                    llmdbench_execute_cmd(actual_cmd=kubectl_expose_cmd, dry_run=ev["control_dry_run"], verbose=ev["control_verbose"], fatal=True)
                    announce(f"✅ Service for pods service model {model} created")

                announce(f"✅ Model \"{model}\" and associated service deployed.")

        # Show resource snapshot
        announce(f"ℹ️ A snapshot of the relevant (model-specific) resources on namespace \"{ev['vllm_common_namespace']}\":")
        kubectl_get_cmd = f"{ev['control_kcmd']} get --namespace {ev['vllm_common_namespace']} {srl}"
        llmdbench_execute_cmd(actual_cmd=kubectl_get_cmd,dry_run=ev["control_dry_run"], verbose=ev["control_verbose"],fatal=False)
        propagate_standup_parameters(ev, api)

    else:
        deploy_methods = ev.get("deploy_methods", "")
        announce(f"⏭️  Environment types are \"{deploy_methods}\". Skipping this step.")

    return 0

def generate_deployment_yaml(ev, model, model_label):
    """Generate Kubernetes Deployment YAML for vLLM standalone model."""

    # Get image reference
    image = get_image(
        ev,
        "vllm_standalone_image",
        False,
        True
    )

    # Generate command line options
    args = add_command_line_options(ev, "vllm_standalone_args")

    # for the launcher remove sleep mode on ev so that it
    # doesn't get added to the uvicorn arguments
    launcher_ev = ev.copy()
    _ = launcher_ev.pop("vllm_common_enable_sleep_mode", None)
    launcher_args = add_command_line_options(launcher_ev, "vllm_standalone_launcher_args")

    # Generate additional environment variables
    additional_env = add_additional_env_to_yaml(ev, "vllm_standalone_envvars_to_yaml")

    limits_str, requests_str = add_resources(ev, "common")

    # Generate annotations
    annotations = add_annotations(ev, "LLMDBENCH_VLLM_COMMON_ANNOTATIONS")

    extra_volume_mounts = add_config('vllm_standalone_extra_volume_mounts',8, "", ev)
    extra_volumes = add_config('vllm_standalone_extra_volumes',6, "", ev)

    deployment_yaml = f"""apiVersion: apps/v1
kind: Deployment
metadata:
  name: vllm-standalone-{model_label}
  labels:
    app: vllm-standalone-{model_label}
    stood-up-by: "{ev['control_username']}"
    stood-up-from: llm-d-benchmark
    stood-up-via: "{ev['deploy_methods']}"
  namespace: {ev['vllm_common_namespace']}
spec:
  replicas: {ev['vllm_common_replicas']}
  selector:
    matchLabels:
      app: vllm-standalone-{model_label}
  template:
    metadata:
      labels:
        app: vllm-standalone-{model_label}
        llm-d.ai/inferenceServing: "true"
        llm-d.ai/model: {model_label}
        llm-d.ai/role: both
      annotations:
{annotations}
    spec:
      schedulerName: {ev['vllm_common_pod_scheduler']}
{add_priority_class_name(ev)}
{add_affinity(ev)}
      containers:
      - name: vllm-standalone-{model_label}
        image: {image}
        imagePullPolicy: Always
        command:
        - /bin/bash
        - "-c"
        args:
{args}
        env:
        - name: LLMDBENCH_VLLM_STANDALONE_MODEL
          value: "{ev['deploy_current_model']}"
        - name: LLMDBENCH_VLLM_COMMON_VLLM_LOAD_FORMAT
          value: "{ev['vllm_common_vllm_load_format']}"
        - name: LLMDBENCH_VLLM_COMMON_MODEL_LOADER_EXTRA_CONFIG
          value: "{ev['vllm_common_model_loader_extra_config']}"
        - name: LLMDBENCH_VLLM_COMMON_AFFINITY
          value: "{ev['vllm_common_affinity']}"
        - name: HUGGING_FACE_HUB_TOKEN
          valueFrom:
            secretKeyRef:
              name: {ev.get('vllm_common_hf_token_name', '')}
              key: HF_TOKEN
{additional_env}
        ports:
        - containerPort: {ev['vllm_common_inference_port']}
        startupProbe:
          httpGet:
            path: {ev["vllm_standalone_startup_probe_path"]}
            port: {ev['vllm_common_inference_port']}
          failureThreshold: {ev["vllm_standalone_startup_probe_failure_threshold"]}
          initialDelaySeconds: {ev["vllm_standalone_startup_probe_initial_delay"]}
          periodSeconds: 30
          timeoutSeconds: 5
        livenessProbe:
          tcpSocket:
            port: {ev['vllm_common_inference_port']}
          failureThreshold: 3
          periodSeconds: 10
        readinessProbe:
          httpGet:
            path: {ev["vllm_standalone_readiness_probe_path"]}
            port: {ev['vllm_common_inference_port']}
          failureThreshold: 3
          periodSeconds: 5
        resources:
          limits:
{limits_str}
          requests:
{requests_str}
        volumeMounts:
        {extra_volume_mounts}
{add_pull_secret(ev)}
"""
    if ev["vllm_standalone_launcher"]:
      deployment_yaml += f"""
      - name: vllm-launcher-{model_label}
        image: {image}
        imagePullPolicy: Always
        command:
        - /bin/bash
        - "-c"
        args:
{launcher_args}
        env:
        - name: LLMDBENCH_VLLM_STANDALONE_MODEL
          value: "{ev['deploy_current_model']}"
        - name: LLMDBENCH_VLLM_COMMON_VLLM_LOAD_FORMAT
          value: "{ev['vllm_common_vllm_load_format']}"
        - name: LLMDBENCH_VLLM_COMMON_MODEL_LOADER_EXTRA_CONFIG
          value: "{ev['vllm_common_model_loader_extra_config']}"
        - name: LLMDBENCH_VLLM_COMMON_AFFINITY
          value: "{ev['vllm_common_affinity']}"
        - name: HUGGING_FACE_HUB_TOKEN
          valueFrom:
            secretKeyRef:
              name: {ev.get('vllm_common_hf_token_name', '')}
              key: HF_TOKEN
{additional_env}
        ports:
        - containerPort: {ev['vllm_standalone_launcher_port']}
        startupProbe:
          httpGet:
            path: {ev["vllm_standalone_startup_probe_path"]}
            port: {ev["vllm_standalone_inference_port"]}
          failureThreshold: {ev["vllm_standalone_startup_probe_failure_threshold"]}
          initialDelaySeconds: {ev["vllm_standalone_startup_probe_initial_delay"]}
          periodSeconds: 30
          timeoutSeconds: 5
        livenessProbe:
          tcpSocket:
            port: {ev['vllm_standalone_launcher_port']}
          failureThreshold: 3
          periodSeconds: 10
        readinessProbe:
          httpGet:
            path: {ev["vllm_common_readiness_probe_path"]}
            port: {ev["vllm_common_inference_port"]}
          failureThreshold: 3
          periodSeconds: 5
        resources:
          limits:
{limits_str}
          requests:
{requests_str}
        volumeMounts:
        {extra_volume_mounts}
{add_pull_secret(ev)}
"""
    deployment_yaml += f"""
      volumes:
      {extra_volumes}
"""
    return deployment_yaml

def generate_service_yaml(ev, model, model_label):
    """Generate Kubernetes Service YAML for vLLM standalone model."""

    service_yaml = f"""apiVersion: v1
kind: Service
metadata:
  name: vllm-standalone-{model_label}
  namespace: {ev['vllm_common_namespace']}
  labels:
    stood-up-by: "{ev['control_username']}"
    stood-up-from: llm-d-benchmark
    stood-up-via: "{ev['deploy_methods']}"
spec:
  ports:
  - name: http
    port: 80
    targetPort: {ev['vllm_common_inference_port']}
"""
    if ev["vllm_standalone_launcher"]:
      service_yaml += f"""
  - name: http-launcher
    port: 81
    targetPort: {ev['vllm_standalone_launcher_port']}
  - name: http-launcher-vllm
    port: 82
    targetPort: {ev['vllm_standalone_launcher_vllm_port']}
"""
    service_yaml += f"""
  selector:
    app: vllm-standalone-{model_label}
  type: ClusterIP
"""
    return service_yaml

def generate_httproute_yaml(ev, model, model_label):
    """Generate HTTPRoute YAML for vLLM standalone model."""

    # Extract cluster URL for hostname
    cluster_url = ev.get("cluster_url", "").replace("https://api.", "")

    # Get model attributes for backend reference
    model_parameters = model_attribute(model, "parameters")
    model_type = model_attribute(model, "modeltype")

    httproute_yaml = f"""apiVersion: gateway.networking.k8s.io/v1beta1
kind: HTTPRoute
metadata:
  name: vllm-standalone-{model_label}
  namespace: {ev['vllm_common_namespace']}
spec:
  parentRefs:
  - name: openshift-gateway
    namespace: openshift-gateway
  hostnames:
  - "{model}.{ev['vllm_common_namespace']}.apps.{cluster_url}"
  rules:
  - matches:
    - path:
        type: PathPrefix
        value: /
    backendRefs:
    - name: vllm-standalone-{model_parameters}-vllm-{model_label}-{model_type}
      port: {ev['vllm_common_inference_port']}
"""
    return httproute_yaml

if __name__ == "__main__":
    sys.exit(main())
