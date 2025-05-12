#!/usr/bin/env python3

"""
This script runs benchmarking on an existing vllm-d stack deployment using LMBenchmark workload.
Note: When using LMBenchmarkWorkloadSpec, only the repetition parameter is used.
The duration and number_users parameters are ignored as the workload specification
controls these through max_requests and max_seconds.
"""

import os
import urllib3

import kubernetes
from kubernetes import client, config

from fmperf import Cluster
from fmperf import LMBenchmarkWorkload
from fmperf.StackSpec import StackSpec
from fmperf.utils import run_benchmark
from fmperf.utils.storage import create_local_storage, create_vpc_block_storage

# Initialize Kubernetes Configuration
def initialize_kubernetes(context_path, work_namespace, work_pvc_name):

    print(f"Loading kube config from \"{context_path}\"")
    kubernetes.config.load_kube_config(context_path)

    _, active_context = kubernetes.config.list_kube_config_contexts(context_path)

    cluster_name=active_context['context']['cluster']
    print(f"Cluster name is \"{cluster_name}\"")

    apiclient = client.ApiClient()
    v1 = client.CoreV1Api(apiclient)

    pvc_created = False
    for pvc in v1.list_namespaced_persistent_volume_claim(namespace=work_namespace, watch=False).items :
        if pvc.metadata.name == work_pvc_name :
            pvc_created = True

    if not pvc_created :
        raise ValueError (f"PVC volume \"{work_pvc_name}\" not found on this cluster!")

    cluster = Cluster(name=cluster_name, apiclient=apiclient, namespace=work_namespace)

    return cluster

if __name__ == "__main__":

    context_path = f'{os.path.expanduser("~")}/.kube/config'
    work_dir=os.environ.get("LLMDBENCH_CONTROL_WORK_DIR")
    if work_dir :
        context_path = f"{work_dir}/environment/context.ctx"
    print(f"Path to context is \"{context_path}\"")

    work_namespace = os.environ.get("LLMDBENCH_CLUSTER_NAMESPACE")
    if not work_namespace :
        work_namespace = "default"
    print(f"Cluster namespace is \"{work_namespace}\"")

    work_pvc_name = os.environ.get("LLMDBENCH_FMPERF_PVC_NAME")
    if not work_pvc_name :
        work_pvc_name = "workload-pvc"

    workload_name = os.environ.get("LLMDBENCH_FMPERF_EXPERIMENT_PROFILE")
    ## USER Entry: File Location for model workload parameters
    workload_file = os.path.join(f"{work_dir}/workload/profiles/{workload_name}")
    print(f"FMPerf workload file to be used is \"{workload_file}\"")

    experiment_id = os.environ.get("LLMDBENCH_EXPERIMENT_ID")
    print(f"Experiment ID will be \"{experiment_id}\"")

    # Initialize Kubernetes
    cluster = initialize_kubernetes(context_path, work_namespace, work_pvc_name)

    # Create workload object
    workload_spec = LMBenchmarkWorkload.from_yaml(workload_file)
    workload_spec.pvc_name = work_pvc_name

    # Create stack spec for the existing vllm-d deployment
    stack_spec = StackSpec(
        name=experiment_id,
        stack_type="vllm-d",  # This will automatically set endpoint to vllm-router-service
        refresh_interval=300,  # Refresh model list every 5 minutes
        endpoint_url="http://inference-gateway"  # Service name
    )

    # USER Entry: Experiment variables
    # Note: For LMBenchmarkWorkload, only repetition is used
    # duration and number_users are controlled by the workload spec
    REPETITION = 1  # Repeat the experiments this many times

    # Run benchmarking experiment against the stack
    run_benchmark(
        cluster=cluster,
        stack_spec=stack_spec,  # Using stack_spec instead of model_spec
        workload_spec=workload_spec,
        repetition=REPETITION,
    )