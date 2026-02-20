#!/usr/bin/env python3

import os
import sys
import subprocess
from pathlib import Path

# Add project root to path for imports
current_file = Path(__file__).resolve()
project_root = current_file.parents[1]
sys.path.insert(0, str(project_root))

# Import from functions.py
from functions import environment_variable_to_dict, announce, llmdbench_execute_cmd, model_attribute, auto_detect_version

def gateway_values(provider : str, host: str, service: str) -> str:
    if provider == "istio":
        return f"""gateway:
  gatewayClassName: istio
  gatewayParameters:
    enabled: true
    accessLogging: false
    logLevel: error
    resources:
      limits:
        cpu: "16"
        memory: 16Gi
      requests:
        cpu: "4"
        memory: 4Gi
  service:
    type: {service}
"""

    elif provider == "kgateway" :
        return f"""gateway:
  gatewayClassName: kgateway
  """

    elif provider == "kgateway-openshift":
        return f"""gateway:
  gatewayClassName: kgateway
  service:
    type: {service}
  gatewayParameters:
    floatingUserId: true
    enabled: true
  """

    elif provider == "data-science-gateway-class" :
        return f"""gateway:
  gatewayClassName: data-science-gateway-class
  labels:
    istio.io/rev: openshift-gateway
    platform.opendatahub.io/part-of: gatewayconfig

  listeners:
    - name: https
      port: 443
      protocol: HTTPS
      allowedRoutes:
        namespaces:
          from: All
      tls:
        mode: Terminate
        certificateRefs:
          - group: ""
            kind: Secret
            name: data-science-gateway-service-tls
            namespace: openshift-ingress

  destinationRule:
      enabled: true
      trafficPolicy:
        connectionPool:
          http:
            http1MaxPendingRequests: 256000
            maxRequestsPerConnection: 256000
            http2MaxRequests: 256000
            idleTimeout: "900s"
          tcp:
            maxConnections: 256000
            maxConnectionDuration: "1800s"
            connectTimeout: "900s"

  tls:
    referenceGrant:
      enabled: true
      secretNamespace: openshift-ingress
      secretName: data-science-gateway-service-tls
  """

    elif provider == "gke":
        return f"""gateway:
  gatewayClassName: gke-l7-regional-external-managed
  destinationRule: {host}

provider:
  name: gke"""
    else:
        return ""

def main():
    """Set up helm repositories and create helmfile configurations for model deployments."""
    # Parse environment variables
    ev = {'current_step_name': os.path.splitext(os.path.basename(__file__))[0] }
    environment_variable_to_dict(ev)

    # Check if modelservice environment is active
    if ev["control_environment_type_modelservice_active"]:

        # Add and update llm-d-modelservic helm repository
        announce("🔧 Setting up helm repositories ...")

        # Add llm-d-modelservice helm repository
        # TODO make this a function
        helm_repo_add_cmd = (
            f"{ev['control_hcmd']} repo add {ev['vllm_modelservice_chart_name']} "
            f"{ev['vllm_modelservice_helm_repository_url']} --force-update"
        )
        result = llmdbench_execute_cmd(
            actual_cmd=helm_repo_add_cmd,
            dry_run=ev["control_dry_run"],
            verbose=ev["control_verbose"]
        )
        if result != 0:
            announce(f"ERROR: Failed setting up llm-d-modelservice helm repository with \"{helm_repo_add_cmd}\" (exit code: {result})")
            exit(result)

        # Add llm-d-infra helm repository
        helm_repo_add_cmd = (
            f"{ev['control_hcmd']} repo add {ev['vllm_infra_chart_name']} "
            f"{ev['vllm_infra_helm_repository_url']} --force-update"
        )
        result = llmdbench_execute_cmd(
            actual_cmd=helm_repo_add_cmd,
            dry_run=ev["control_dry_run"],
            verbose=ev["control_verbose"]
        )
        if result != 0:
            announce(f"ERROR: Failed setting up llm-d-infra helm repository with \"{helm_repo_add_cmd}\" (exit code: {result})")
            exit(result)

        # Update helm repositories
        helm_repo_update_cmd = f"{ev['control_hcmd']} repo update"
        result = llmdbench_execute_cmd(
            actual_cmd=helm_repo_update_cmd,
            dry_run=ev["control_dry_run"],
            verbose=ev["control_verbose"]
        )
        if result != 0:
            announce(f"ERROR: Failed setting up helm repositories with \"{helm_repo_update_cmd}\" (exit code: {result})")
            exit(result)

        # Auto-detect chart version if needed
        result = auto_detect_version(ev, ev['vllm_modelservice_chart_name'], "vllm_modelservice_chart_version", "vllm_modelservice_helm_repository")
        if 0 != result:
            exit(result)
        result = auto_detect_version(ev, ev['vllm_infra_chart_name'], "vllm_infra_chart_version", "vllm_infra_helm_repository")
        if 0 != result:
            exit(result)

        # Create base helm directory structure
        helm_base_dir = Path(ev["control_work_dir"]) / "setup" / "helm" / ev["vllm_modelservice_release"]
        helm_base_dir.mkdir(parents=True, exist_ok=True)

        # Process each model
        model_number = 0
        model_list = ev["deploy_model_list"].split(',')

        for model in model_list:
            # Get model attribute
            model_id_label = model_attribute(model, "modelid_label", ev)

            # Create infra values file
            infra_value_file = Path(helm_base_dir / "infra.yaml" )
            with open(infra_value_file, 'w') as f:
                gw_class = ev['vllm_modelservice_gateway_class_name']
                if gw_class == 'kgateway' and ev['control_deploy_is_openshift']:
                    gw_class = f"{gw_class}-openshift"
                f.write(gateway_values(gw_class, f"{model_id_label}-gaie-epp.{ev['vllm_common_namespace']}{ev['vllm_common_fqdn']}", ev["vllm_modelservice_gateway_service_type"]))

            ev["deploy_current_model_id_label"] = model_id_label

            # Format model number with zero padding
            model_num = f"{model_number:02d}"

            # Create model-specific directory
            model_dir = helm_base_dir / model_num
            model_dir.mkdir(parents=True, exist_ok=True)

            # Generate helmfile YAML content
            non_admin_defaults = ""
            if not ev['user_is_admin'] == "0": # Avoid default namespace creation for non cluster-level admin users
                non_admin_defaults = "helmDefaults:\n  createNamespace: false\n---\n\n"

            helmfile_content = f"""{non_admin_defaults}repositories:
  - name: {ev['vllm_modelservice_helm_repository']}
    url: {ev['vllm_modelservice_helm_repository_url']}
  - name: {ev['vllm_infra_helm_repository']}
    url: {ev['vllm_infra_helm_repository_url']}

releases:
  - name: infra-{ev['vllm_modelservice_release']}
    namespace: {ev['vllm_common_namespace']}
    chart: {ev['vllm_infra_helm_repository']}/{ev['vllm_infra_chart_name']}
    version: {ev['vllm_infra_chart_version']}
    installed: true
    labels:
      type: infrastructure
      kind: inference-stack
    values:
      - infra.yaml

  - name: {model_id_label}-ms
    namespace: {ev['vllm_common_namespace']}
    chart: {ev['vllm_modelservice_helm_repository']}/{ev['vllm_modelservice_chart_name']}
    version: {ev['vllm_modelservice_chart_version']}
    installed: true
    needs:
      - {ev['vllm_common_namespace']}/infra-{ev['vllm_modelservice_release']}
      - {ev['vllm_common_namespace']}/{model_id_label}-gaie
    values:
      - {model_num}/ms-values.yaml
    labels:
      kind: inference-stack

  - name: {model_id_label}-gaie
    namespace: {ev['vllm_common_namespace']}
    chart: {ev['vllm_gaie_chart_name']}
    version: {ev['vllm_gaie_chart_version']}
    installed: true
    needs:
      -  {ev['vllm_common_namespace']}/infra-{ev['vllm_modelservice_release']}
    values:
      - {model_num}/gaie-values.yaml
    labels:
      kind: inference-stack
"""

            # Write helmfile configuration
            helmfile_path = helm_base_dir / f"helmfile-{model_num}.yaml"
            with open(helmfile_path, 'w') as f:
                f.write(helmfile_content)

            announce(f"📝 Created helmfile configuration for model {model} ({model_num})")

            model_number += 1

        announce(f"🚀 Installing helm chart \"infra-{ev['vllm_modelservice_release']}\" via helmfile...")
        install_cmd=f"helmfile --namespace {ev['vllm_common_namespace']} --kubeconfig {ev['control_work_dir']}/environment/context.ctx --selector name=infra-{ev['vllm_modelservice_release']} apply -f {ev['control_work_dir']}/setup/helm/{ev['vllm_modelservice_release']}/helmfile-00.yaml --skip-diff-on-install"
        result = llmdbench_execute_cmd(
            actual_cmd=install_cmd,
            dry_run=ev["control_dry_run"],
            verbose=ev["control_verbose"]
        )
        if result != 0:
            announce(f"ERROR: Failed Failed installing chart \"infra-{ev['vllm_modelservice_release']}\" (exit code: {result})")
            exit(result)
        announce(f"✅ chart \"infra-{ev['vllm_modelservice_release']}\" deployed successfully")

        announce("✅ Completed gateway deployment")
    else:
        deploy_methods = ev["deploy_methods"]
        announce(f"⏭️ Environment types are \"{deploy_methods}\". Skipping this step.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
