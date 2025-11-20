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
    check_affinity, \
    add_annotations, \
    add_command_line_options, \
    add_additional_env_to_yaml, \
    add_resources, \
    add_config, \
    get_accelerator_nr, \
    is_standalone_deployment, \
    kubectl_apply, \
    environment_variable_to_dict, \
    wait_for_pods_creation, \
    wait_for_pods_running, \
    wait_for_pods_ready, \
    collect_logs
)

def main():
    """Deploy vLLM standalone models with Kubernetes Deployment, Service, and HTTPRoute."""
    os.environ["CURRENT_STEP_NAME"] = os.path.splitext(os.path.basename(__file__))[0]

    ev={}
    environment_variable_to_dict(ev)

    # Check if standalone environment is active
    if is_standalone_deployment(ev):

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

        # Create yamls directory
        yamls_dir = Path(ev["control_work_dir"]) / "setup" / "yamls"
        yamls_dir.mkdir(parents=True, exist_ok=True)

        # Process each model - First pass: Deploy resources
        model_list = ev.get("deploy_model_list", "").replace(",", " ").split()
        for model in model_list:
            # Generate filename-safe model name
            modelfn = model.replace("/", "___")

            # Set current model environment variable
            current_model = model_attribute(model, "model")
            os.environ["LLMDBENCH_DEPLOY_CURRENT_MODEL"] = current_model

            # Get model attributes
            model_label = model_attribute(model, "label")

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
            model_label = model_attribute(model, "label")

            ev["deploy_current_model_id_label"] = model_label

            # Wait for vllm pods creation
            result = wait_for_pods_creation(ev, ev["vllm_common_replicas"], "both")
            if result != 0:
                return result

            # Wait for vllm pods to be running
            result = wait_for_pods_running(ev, ev["vllm_common_replicas"], "both")
            if result != 0:
                return result

            # Wait for vllm pods to be ready
            result = wait_for_pods_ready(ev, ev["vllm_common_replicas"], "both")
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
    else:
        deploy_methods = ev.get("deploy_methods", "")
        announce(f"⏭️  Environment types are \"{deploy_methods}\". Skipping this step.")

    return 0

def generate_deployment_yaml(ev, model, model_label):
    """Generate Kubernetes Deployment YAML for vLLM standalone model."""

    # Get image reference
    image = get_image(
        ev["vllm_standalone_image_registry"],
        ev["vllm_standalone_image_repo"],
        ev["vllm_standalone_image_name"],
        ev["vllm_standalone_image_tag"],
        False,
        True
    )

    # Parse affinity
    affinity_key, affinity_value = ev["vllm_common_affinity"].split(":", 1)

    # Generate command line options
    args = add_command_line_options(ev["vllm_standalone_args"])

    # Generate additional environment variables
    additional_env = add_additional_env_to_yaml(ev, ev["vllm_common_envvars_to_yaml"])

    limits_str, requests_str = add_resources(ev, "common")

    # Generate annotations
    annotations = add_annotations("LLMDBENCH_VLLM_COMMON_ANNOTATIONS")

    extra_volume_mounts = add_config(ev['vllm_common_extra_volume_mounts'],8)
    extra_volumes = add_config(ev['vllm_common_extra_volumes'],6)

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
      schedulerName: {ev.get('vllm_common_pod_scheduler', 'default-scheduler')}
      affinity:
        nodeAffinity:
          requiredDuringSchedulingIgnoredDuringExecution:
            nodeSelectorTerms:
            - matchExpressions:
              - key: {affinity_key}
                operator: In
                values:
                - {affinity_value}
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
          value: "{os.environ.get('LLMDBENCH_DEPLOY_CURRENT_MODEL', '')}"
        - name: LLMDBENCH_VLLM_STANDALONE_VLLM_LOAD_FORMAT
          value: "{ev.get('vllm_standalone_vllm_load_format', '')}"
        - name: LLMDBENCH_VLLM_STANDALONE_MODEL_LOADER_EXTRA_CONFIG
          value: "{os.environ.get('LLMDBENCH_VLLM_STANDALONE_MODEL_LOADER_EXTRA_CONFIG', '{}')}"
        - name: VLLM_LOGGING_LEVEL
          value: "{ev.get('vllm_standalone_vllm_logging_level', '')}"
        - name: HF_HOME
          value: {ev.get('vllm_standalone_pvc_mountpoint', '')}
        - name: LLMDBENCH_VLLM_COMMON_AFFINITY
          value: "{os.environ.get('LLMDBENCH_VLLM_COMMON_AFFINITY', '')}"
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
            path: /health
            port: {ev['vllm_common_inference_port']}
          failureThreshold: 200
          initialDelaySeconds: {ev.get('vllm_common_initial_delay_probe', 60)}
          periodSeconds: 30
          timeoutSeconds: 5
        livenessProbe:
          tcpSocket:
            port: {ev['vllm_common_inference_port']}
          failureThreshold: 3
          periodSeconds: 10
        readinessProbe:
          httpGet:
            path: /health
            port: {ev['vllm_common_inference_port']}
          failureThreshold: 3
          periodSeconds: 5
        resources:
          limits:
{limits_str}
          requests:
{requests_str}
        volumeMounts:
        - name: preprocesses
          mountPath: /setup/preprocess
        - name: cache-volume
          mountPath: {ev.get('vllm_standalone_pvc_mountpoint', '')}
        - name: shm
          mountPath: /dev/shm
        {extra_volume_mounts}
      volumes:
      - name: preprocesses
        configMap:
          name: llm-d-benchmark-preprocesses
          defaultMode: 0500
      - name: cache-volume
        persistentVolumeClaim:
          claimName: {ev.get('vllm_common_pvc_name', '')}
#          readOnly: true
      {extra_volumes}
      - name: shm
        emptyDir:
          medium: Memory
          sizeLimit: {ev.get('vllm_common_shm_mem')}
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
