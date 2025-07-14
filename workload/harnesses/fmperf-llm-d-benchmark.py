#!/usr/bin/env python3

"""
Modified version of example_openshift.py to run in a Kubernetes pod.
This script assumes it's running inside a pod and uses the environment variables
provided by the job configuration.
"""

import os
import urllib3
import yaml
import logging
import json
from datetime import datetime
import sys
import time

import kubernetes
from kubernetes import client

from fmperf.Cluster import Cluster
from fmperf import LMBenchmarkWorkload
from fmperf.StackSpec import StackSpec
from fmperf.utils import run_benchmark

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Disable SSL warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def update_workload_config(workload_spec, env_vars):
    """Update workload configuration with environment variables if provided."""
    logger.info("Updating workload configuration from environment variables")
    if 'LLMDBENCH_FMPERF_BATCH_SIZE' in env_vars:
        workload_spec.batch_size = int(env_vars['LLMDBENCH_FMPERF_BATCH_SIZE'])
        logger.info(f"Set batch_size to {workload_spec.batch_size}")
    if 'LLMDBENCH_FMPERF_SEQUENCE_LENGTH' in env_vars:
        workload_spec.sequence_length = int(env_vars['LLMDBENCH_FMPERF_SEQUENCE_LENGTH'])
        logger.info(f"Set sequence_length to {workload_spec.sequence_length}")
    if 'LLMDBENCH_FMPERF_MAX_TOKENS' in env_vars:
        workload_spec.max_tokens = int(env_vars['LLMDBENCH_FMPERF_MAX_TOKENS'])
        logger.info(f"Set max_tokens to {workload_spec.max_tokens}")
    if 'LLMDBENCH_FMPERF_NUM_USERS_WARMUP' in env_vars:
        workload_spec.num_users_warmup = int(env_vars['LLMDBENCH_FMPERF_NUM_USERS_WARMUP'])
        logger.info(f"Set num_users_warmup to {workload_spec.num_users_warmup}")
    if 'LLMDBENCH_FMPERF_NUM_USERS' in env_vars:
        workload_spec.num_users = int(env_vars['LLMDBENCH_FMPERF_NUM_USERS'])
        logger.info(f"Set num_users to {workload_spec.num_users}")
    if 'LLMDBENCH_FMPERF_NUM_ROUNDS' in env_vars:
        workload_spec.num_rounds = int(env_vars['LLMDBENCH_FMPERF_NUM_ROUNDS'])
        logger.info(f"Set num_rounds to {workload_spec.num_rounds}")
    if 'LLMDBENCH_FMPERF_SYSTEM_PROMPT' in env_vars:
        workload_spec.system_prompt = int(env_vars['LLMDBENCH_FMPERF_SYSTEM_PROMPT'])
        logger.info(f"Set system_prompt to {workload_spec.system_prompt}")
    if 'LLMDBENCH_FMPERF_CHAT_HISTORY' in env_vars:
        workload_spec.chat_history = int(env_vars['LLMDBENCH_FMPERF_CHAT_HISTORY'])
        logger.info(f"Set chat_history to {workload_spec.chat_history}")
    if 'LLMDBENCH_FMPERF_ANSWER_LEN' in env_vars:
        workload_spec.answer_len = int(env_vars['LLMDBENCH_FMPERF_ANSWER_LEN'])
        logger.info(f"Set answer_len to {workload_spec.answer_len}")
    if 'LLMDBENCH_FMPERF_TEST_DURATION' in env_vars:
        workload_spec.test_duration = int(env_vars['LLMDBENCH_FMPERF_TEST_DURATION'])
        logger.info(f"Set test_duration to {workload_spec.test_duration}")

    return workload_spec

def wait_for_evaluation_job(cluster, job_name, namespace, timeout=7200):
    """Wait for the evaluation job to complete."""
    logger.info(f"Waiting for evaluation job {job_name} to complete...")
    start_time = time.time()
    k8s_client = client.BatchV1Api()

    while True:
        if time.time() - start_time > timeout:
            logger.error(f"Timeout waiting for evaluation job after {timeout} seconds")
            return False

        try:
            # Try to get the job
            job = k8s_client.read_namespaced_job(name=job_name, namespace=namespace)

            # If we can get the job, check its status
            if job.status.succeeded:
                logger.info(f"Evaluation job {job_name} completed successfully")
                return True
            if job.status.failed:
                logger.error(f"Evaluation job {job_name} failed")
                return False

        except client.exceptions.ApiException as e:
            if e.status == 404:
                # Job is gone - check if it was deleted by the code (which would mean success)
                # or if it failed
                try:
                    # Try to get the job one more time to see if it was in a successful state
                    job = k8s_client.read_namespaced_job(name=job_name, namespace=namespace)
                    if job.status.succeeded:
                        logger.info(f"Job {job_name} completed successfully before deletion")
                        return True
                except client.exceptions.ApiException:
                    # If we can't get the job at all, it might have failed
                    logger.error(f"Job {job_name} disappeared without completing successfully")
                    return False
            else:
                logger.error(f"Error checking job status: {str(e)}")
                return False

        # Wait before checking again
        time.sleep(30)
        remaining = int(timeout - (time.time() - start_time))
        logger.info(f"Still waiting for evaluation job... ({remaining} seconds remaining)")

def main():
    logger.info("Starting benchmark run")
    env_vars = os.environ
    stack_name = env_vars.get("LLMDBENCH_HARNESS_STACK_NAME", "llm-d-3b-instruct")
    stack_type = env_vars.get("LLMDBENCH_HARNESS_STACK_TYPE", "llm-d")
    endpoint_url = env_vars.get("LLMDBENCH_HARNESS_STACK_ENDPOINT_URL", "inference-gateway")
    workload_file = env_vars.get("LLMDBENCH_HARNESS_WORKLOAD_FILE", "llmdbench_workload.yaml")
    repetition = int(env_vars.get("LLMDBENCH_FMPERF_REPETITION", "1"))
    namespace = env_vars.get("LLMDBENCH_HARNESS_NAMESPACE", "llmdbench")
    job_id = env_vars.get("LLMDBENCH_FMPERF_JOB_ID", stack_name)

    # Get results directory for configuration
    results_dir = env_vars.get("LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR", "/requests")

    logger.info(f"Using configuration:")
    logger.info(f"  Stack name: {stack_name}")
    logger.info(f"  Stack type: {stack_type}")
    logger.info(f"  Endpoint URL: {endpoint_url}")
    logger.info(f"  Workload file: {workload_file}")
    logger.info(f"  Repetition: {repetition}")
    logger.info(f"  Namespace: {namespace}")
    logger.info(f"  Job ID: {job_id}")
    logger.info(f"  Results directory (PVC): {results_dir}")

    workload_file_path = os.path.join("/workspace", workload_file)
    logger.info(f"Loading workload configuration from {workload_file_path}")
    workload_spec = LMBenchmarkWorkload.from_yaml(workload_file_path)

    logger.info("Updating workload configuration with environment variables")
    workload_spec = update_workload_config(workload_spec, env_vars)

    logger.info("Creating stack specification")
    stack_spec = StackSpec(
        name=stack_name,
        stack_type=stack_type,
        refresh_interval=300,
        endpoint_url=endpoint_url
    )

    logger.info("Initializing Kubernetes client")
    kubernetes.config.load_incluster_config()
    apiclient = client.ApiClient()
    cluster = Cluster(name="in-cluster", apiclient=apiclient, namespace=namespace)

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")

    logger.info("Starting benchmark run")

    try:
        # Run benchmark which will create the evaluation job
        results = run_benchmark(
            cluster=cluster,
            stack_spec=stack_spec,
            workload_spec=workload_spec,
            repetition=repetition,
            id=job_id,
        )

        logger.info("\nEvaluation job has been created!")
        logger.info("The evaluation job will:")
        logger.info("1. Run the benchmark tests")
        logger.info("2. Save results to the PVC at:")
        logger.info(f"   {results_dir}/{stack_name}/")

        # Wait for the evaluation job to complete
        job_name = f"lmbenchmark-evaluate-{job_id}"
        logger.info(f"Waiting for evaluation job {job_name} to complete...")
        if wait_for_evaluation_job(cluster, job_name, namespace):
            logger.info("Evaluation job completed successfully")
        else:
            logger.error("Evaluation job failed or timed out")
            raise Exception("Evaluation job failed or timed out")

    except Exception as e:
        logger.error(f"Benchmark run failed: {str(e)}")
        raise

if __name__ == "__main__":
    main()
