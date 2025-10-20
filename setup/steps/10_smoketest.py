#!/usr/bin/env python3

import os
import sys
import tempfile
import re
from pathlib import Path
from kubernetes import client as k8s_client, config as k8s_config
import ipaddress
# import openshift as oc

# Add project root to path and load k8s config for imports
current_file = Path(__file__).resolve()
project_root = current_file.parents[1]
sys.path.insert(0, str(project_root))
k8s_config.load_kube_config()

# ---------------- Import local packages ----------------

try:
    from functions import announce, environment_variable_to_dict, get_accelerator_nr, is_standalone_deployment, get_accelerator_type, llmdbench_execute_cmd, model_attribute, get_model_name_from_pod, get_image
except ImportError as e:
    # Fallback for when dependencies are not available
    announce(f"ERROR: Could not import required modules: {e}")
    announce("This script requires the llm-d environment to be properly set up.")
    announce("Please run: ./setup/install_deps.sh")
    sys.exit(1)

# ---------------- Helpers ----------------

def check_deployment(ev: dict):
    """
    Checking if current deployment was successful
    """

    announce("🔍 Checking if current deployment was successful...")
    dry_run = int(ev.get("control_dry_run", 0))
    verbose = int(ev.get("control_verbose", 0))

    """
    Checking if service/gateway was successfully deployed
    """
    if is_standalone_deployment(ev):
        pod_string = "standalone"
        try:
            api_instance = k8s_client.CoreV1Api()
            all_services = api_instance.list_namespaced_service(namespace=ev["vllm_common_namespace"], watch=False)
            for service in all_services.items:
                if pod_string in service.metadata.name:
                    service_name = service.metadata.name
            service_name = service.metadata.name
            service_ip=service.spec.cluster_ip
            service_type = "service"
            route_string = service_name + '-route'
        except k8s_client.ApiException as e:
            announce(f"ERROR: unable to find service: {e}")
    else:
        pod_string = "decode"
        route_string=f"{ev.get('vllm_modelservice_release', '')}-inference-gateway-route"
        service_type = "gateway"
        try:
            api_instance = k8s_client.CustomObjectsApi()
            gateways = api_instance.list_namespaced_custom_object(
                group="gateway.networking.k8s.io",
                version="v1",
                namespace=ev["vllm_common_namespace"],
                plural="gateways"
            )
            for service in gateways['items']:
                if service['metadata']['name'] == f"infra-{ev.get('vllm_modelservice_release', '')}-inference-gateway":
                    service_name = service['metadata']['name']
                    for address in service["status"]["addresses"]:
                        if address.get("type") == "IPAddress":
                            service_ip = address.get("value")
                            break
                    break
        except k8s_client.ApiException as e:
            announce(f"ERROR: unable to finding gateway: {e}")

    if dry_run:
        service_name = "localhost"
        service_ip = "127.0.0.8"
    else:
        if not service_name:
            announce(f"ERROR: No {service_type} found with string \"{pod_string}\"!")
        elif not service_ip:
            announce(f"ERROR: Unable to find IP for service/gateway \"{service}\"!")
        elif not ipaddress.ip_address(service_ip):
            announce(f"ERROR: Invalid IP (\"{service_ip}\") for service/gateway \"{service_name}\"!")

    """
    Checking if pods were successfully deployed
    """
    model_list = ev.get("deploy_model_list", "").replace(",", " ").split()
    for model in model_list:
        current_model = model_attribute(model, "model")
        current_model_ID = model_attribute(model, "modelid")
        current_model_ID_label = model_attribute(model, "modelid_label")

    if dry_run:
        pod_ip_list = "127.0.0.4"
    try:
        api_instance = k8s_client.CoreV1Api()
        pod_ip_list = []
        if is_standalone_deployment(ev):
            pods = api_instance.list_namespaced_pod(namespace=ev["vllm_common_namespace"])
            for pod in pods.items:
                if pod_string in pod.metadata.name:
                    pod_ip_list.append(pod.status.pod_ip)
        else:
            pods = api_instance.list_namespaced_pod(namespace=ev["vllm_common_namespace"], label_selector=f"llm-d.ai/model={current_model_ID_label},llm-d.ai/role={pod_string}")
            for pod in pods.items:
                pod_ip_list.append(pod.status.pod_ip)
    except k8s_client.ApiException as e:
        announce(f"ERROR: Unable to find pods in namespace {ev['vllm_common_namespace']}: {e}")

    if not pod_ip_list:
        announce(f"ERROR: Unable to find IPs for pods \"{pod_string}\"!")

    announce(f"🚀 Testing all pods \"{pod_string}\" (port {ev['vllm_common_inference_port']})...")
    for pod_ip in pod_ip_list:
        announce(f"       🚀 Testing pod ip \"{pod_ip}\" ...")
        if dry_run:
            announce(f"       ✅ Pod ip \"{pod_ip}\" responded successfully ({current_model})")
        else:
            image_url = get_image(ev['llmd_image_registry'], ev['llmd_image_repo'], ev['llmd_image_name'], ev['llmd_image_tag'])
            received_model_name, curl_command_used = get_model_name_from_pod(ev['vllm_common_namespace'], image_url, pod_ip, ev['vllm_common_inference_port'])
            if received_model_name == current_model:
                announce(f"       ✅ Pod ip \"{pod_ip}\" responded successfully ({received_model_name})")
            else:
                announce(f"       ERROR: Pod ip \"{pod_ip}\" responded to \"{curl_command_used}\" with model name \"{received_model_name}\" (instead of {current_model})!")

    announce(f"✅ All pods respond successfully")
    announce(f"🚀 Testing service/gateway \"{service_ip}\" (port 80)...")

    if dry_run:
        announce(f"✅ Service responds successfully ({current_model})")
    else:
        image_url = get_image(ev['llmd_image_registry'], ev['llmd_image_repo'], ev['llmd_image_name'], ev['llmd_image_tag'])
        received_model_name, curl_command_used = get_model_name_from_pod(ev['vllm_common_namespace'], image_url, service_ip, "80")
        if received_model_name == current_model:
            announce(f"✅ Service responds successfully ({received_model_name})")
        else:
            announce(f"ERROR: Service responded to \"{curl_command_used}\" with model name \"{received_model_name}\" (instead of {current_model})!")

    if dry_run:
        route_url = ""
    else:
        if ev['control_deploy_is_openshift']:
            api_instance = k8s_client.CustomObjectsApi()
            try:
                route = api_instance.get_namespaced_custom_object(
                group="route.openshift.io",
                version="v1",
                name=route_string,
                namespace=ev['vllm_common_namespace'],
                plural="routes"
            )
                route_url = route["spec"]["host"]
            except k8s_client.ApiException as e:
                announce(f"ERROR: unable to fetch route: {e}")

    if route_url:
        announce(f"🚀 Testing external route \"{route_url}\"...")
        if is_standalone_deployment(ev):
            received_model_name, curl_command_used = get_model_name_from_pod(ev['vllm_common_namespace'], image_url, route_url, '80')
        else:
            received_model_name, curl_command_used = get_model_name_from_pod(ev['vllm_common_namespace'], image_url, route_url + ':80/' + current_model_ID, '80')
        if received_model_name == current_model:
            announce(f"✅ External route responds successfully ({received_model_name})")
        else:
            announce(f"ERROR: External route responded to \"{curl_command_used}\" with model name \"{received_model_name}\" (instead of {current_model})!")


def main():
    """Main function following the pattern from other Python steps"""

    # Set current step name for logging/tracking
    os.environ["LLMDBENCH_CURRENT_STEP"] = os.path.splitext(os.path.basename(__file__))[0]

    ev = {}
    environment_variable_to_dict(ev)

    if ev["control_dry_run"]:
        announce("DRY RUN enabled. No actual changes will be made.")

    # Execute the main logic
    return check_deployment(ev)


if __name__ == "__main__":
    sys.exit(main())
