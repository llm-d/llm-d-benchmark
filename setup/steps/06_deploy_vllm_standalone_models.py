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
    get_accelerator_nr, \
    is_standalone_deployment, \
    add_config, \
    environment_variable_to_dict
)


def main():
    """Deploy vLLM standalone models with Kubernetes Deployment, Service, and HTTPRoute."""
    os.environ["CURRENT_STEP_NAME"] = os.path.splitext(os.path.basename(__file__))[0]

    ev={}
    environment_variable_to_dict(ev)

    # Check if standalone environment is active
    if is_standalone_deployment(ev):

        # Check storage class
        if not check_storage_class():
            announce("❌ Failed to check storage class")
            return 1

        # Check affinity
        if not check_affinity(ev):
            announce("❌ Failed to check affinity")
            return 1

        # Re-parse environment variables in case check functions updated them
        for key in dict(os.environ).keys():
            if "LLMDBENCH_" in key:
                ev.update({key.split("LLMDBENCH_")[1].lower(): os.environ.get(key)})

        # Extract environment for debugging
        extract_environment()

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
            llmdbench_execute_cmd(
                actual_cmd=kubectl_deploy_cmd,
                dry_run=int(ev.get("control_dry_run", 0)),
                verbose=int(ev.get("control_verbose", 0)),
                fatal=True
            )

            # Generate Service YAML
            service_yaml = generate_service_yaml(ev, model, model_label)
            service_file = yamls_dir / f"{ev['current_step']}_b_service_{modelfn}.yaml"
            with open(service_file, 'w') as f:
                f.write(service_yaml)

            # Apply service
            kubectl_service_cmd = f"{ev['control_kcmd']} apply -f {service_file}"
            llmdbench_execute_cmd(
                actual_cmd=kubectl_service_cmd,
                dry_run=int(ev.get("control_dry_run", 0)),
                verbose=int(ev.get("control_verbose", 0))
            )

            # Optional HTTPRoute for OpenShift
            srl = "deployment,service,route,pods,secrets"
            if int(ev.get("vllm_standalone_httproute", 0)) == 1:
                srl = "deployment,service,httproute,route,pods,secrets"

                # Generate HTTPRoute YAML
                httproute_yaml = generate_httproute_yaml(ev, model, model_label)
                httproute_file = yamls_dir / f"{ev['current_step']}_c_httproute_{modelfn}.yaml"
                with open(httproute_file, 'w') as f:
                    f.write(httproute_yaml)

                # Apply HTTPRoute
                kubectl_httproute_cmd = f"{ev['control_kcmd']} apply -f {httproute_file}"
                llmdbench_execute_cmd(
                    actual_cmd=kubectl_httproute_cmd,
                    dry_run=int(ev.get("control_dry_run", 0)),
                    verbose=int(ev.get("control_verbose", 0))
                )

            announce(f"✅ Model \"{model}\" and associated service deployed.")

        # Second pass: Wait for pods to be ready
        for model in model_list:
            model_label = model_attribute(model, "label")
            namespace = ev["vllm_common_namespace"]

            # Wait for pod creation
            announce(f"⏳ Waiting for (standalone) pods serving model {model} to be created...")
            kubectl_wait_create_cmd = (
                f"{ev['control_kcmd']} --namespace {namespace} wait "
                f"--timeout={int(ev.get('control_wait_timeout', 600)) // 2}s "
                f"--for=create pod -l app=vllm-standalone-{model_label}"
            )
            llmdbench_execute_cmd(
                actual_cmd=kubectl_wait_create_cmd,
                dry_run=int(ev.get("control_dry_run", 0)),
                verbose=int(ev.get("control_verbose", 0)),
                fatal=True,
                attempts=2
            )
            announce(f"✅ (standalone) pods serving model {model} created")

            # Wait for Running state
            announce(f"⏳ Waiting for (standalone) pods serving model {model} to be in \"Running\" state (timeout={ev.get('vllm_common_timeout', 300)}s)...")
            kubectl_wait_running_cmd = (
                f"{ev['control_kcmd']} --namespace {namespace} wait "
                f"--timeout={ev.get('vllm_common_timeout', 300)}s "
                f"--for=jsonpath='{{.status.phase}}'=Running pod -l app=vllm-standalone-{model_label}"
            )
            llmdbench_execute_cmd(
                actual_cmd=kubectl_wait_running_cmd,
                dry_run=int(ev.get("control_dry_run", 0)),
                verbose=int(ev.get("control_verbose", 0))
            )
            announce(f"🚀 (standalone) pods serving model {model} running")

            # Wait for Ready condition
            announce(f"⏳ Waiting for (standalone) pods serving {model} to be Ready (timeout={ev.get('vllm_common_timeout', 300)}s)...")
            kubectl_wait_ready_cmd = (
                f"{ev['control_kcmd']} --namespace {namespace} wait "
                f"--timeout={ev.get('vllm_common_timeout', 300)}s "
                f"--for=condition=Ready=True pod -l app=vllm-standalone-{model_label}"
            )
            llmdbench_execute_cmd(
                actual_cmd=kubectl_wait_ready_cmd,
                dry_run=int(ev.get("control_dry_run", 0)),
                verbose=int(ev.get("control_verbose", 0))
            )
            announce(f"🚀 (standalone) pods serving model {model} ready")

            # Collect logs
            logs_dir = Path(ev["control_work_dir"]) / "setup" / "logs"
            logs_dir.mkdir(parents=True, exist_ok=True)
            kubectl_logs_cmd = (
                f"{ev['control_kcmd']} --namespace {namespace} logs --tail=-1 --prefix=true "
                f"-l app=vllm-standalone-{model_label} > {logs_dir}/vllm-standalone.log"
            )
            llmdbench_execute_cmd(
                actual_cmd=kubectl_logs_cmd,
                dry_run=int(ev.get("control_dry_run", 0)),
                verbose=int(ev.get("control_verbose", 0))
            )

            # Handle OpenShift route exposure
            if (int(ev.get("vllm_standalone_route", 0)) != 0 and
                int(ev.get("control_deploy_is_openshift", 0)) == 1):

                # Check if route already exists
                route_check_cmd = (
                    f"{ev['control_kcmd']} --namespace {namespace} get route --ignore-not-found | "
                    f"grep vllm-standalone-{model_label}-route || true"
                )

                try:
                    import subprocess
                    result = subprocess.run(
                        route_check_cmd,
                        shell=True,
                        capture_output=True,
                        text=True,
                        check=False
                    )
                    is_route = result.stdout.strip()
                except Exception:
                    is_route = ""

                if not is_route:
                    announce(f"📜 Exposing pods serving model {model} as service...")
                    kubectl_expose_cmd = (
                        f"{ev['control_kcmd']} --namespace {namespace} expose "
                        f"service/vllm-standalone-{model_label} --namespace {namespace} "
                        f"--target-port={ev['vllm_common_inference_port']} "
                        f"--name=vllm-standalone-{model_label}-route"
                    )
                    llmdbench_execute_cmd(
                        actual_cmd=kubectl_expose_cmd,
                        dry_run=int(ev.get("control_dry_run", 0)),
                        verbose=int(ev.get("control_verbose", 0))
                    )
                    announce(f"✅ Service for pods service model {model} created")

                announce(f"✅ Model \"{model}\" and associated service deployed.")

        # Show resource snapshot
        announce(f"ℹ️ A snapshot of the relevant (model-specific) resources on namespace \"{ev['vllm_common_namespace']}\":")
        if int(ev.get("control_dry_run", 0)) == 0:
            kubectl_get_cmd = f"{ev['control_kcmd']} get --namespace {ev['vllm_common_namespace']} {srl}"
            llmdbench_execute_cmd(
                actual_cmd=kubectl_get_cmd,
                dry_run=int(ev.get("control_dry_run", 0)),
                verbose=int(ev.get("control_verbose", 0)),
                fatal=False
            )
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
        ev["vllm_standalone_image_tag"]
    )

    # Parse affinity
    affinity_key, affinity_value = ev["vllm_common_affinity"].split(":", 1)

    # Generate command line options
    args = add_command_line_options(ev["vllm_standalone_args"])

    # Generate additional environment variables
    additional_env = add_additional_env_to_yaml(ev.get("vllm_common_envvars_to_yaml", ""))

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
    stood-up-by: {ev['control_username']}
    stood-up-from: llm-d-benchmark
    stood-up-via: {ev['deploy_methods']}
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
            cpu: "{ev.get('vllm_common_cpu_nr', '')}"
            memory: {ev.get('vllm_common_cpu_mem', '')}
            {ev.get('vllm_common_accelerator_resource', '')}: "{
              get_accelerator_nr(
                ev.get('vllm_common_accelerator_nr', 'auto'),
                ev.get('vllm_common_tensor_parallelism', 1),
                ev.get('vllm_common_data_parallelism', 1),
              )
            }"
            ephemeral-storage: {ev.get('vllm_standalone_ephemeral_storage', '')}
          requests:
            cpu: "{ev.get('vllm_common_cpu_nr', '')}"
            memory: {ev.get('vllm_common_cpu_mem', '')}
            {ev.get('vllm_common_accelerator_resource', '')}: "{
              get_accelerator_nr(
                ev.get('vllm_common_accelerator_nr', 'auto'),
                ev.get('vllm_common_tensor_parallelism', 1),
                ev.get('vllm_common_data_parallelism', 1),
              )
            }"
            ephemeral-storage: {ev.get('vllm_standalone_ephemeral_storage', '')}
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
    stood-up-by: {ev['control_username']}
    stood-up-from: llm-d-benchmark
    stood-up-via: {ev['deploy_methods']}
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
