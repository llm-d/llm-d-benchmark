#!/usr/bin/env python3

"""
Modified version of example_openshift.py to run in a Kubernetes pod.
This script assumes it's running inside a pod and uses the environment variables
provided by the job configuration.
"""

import os
import subprocess
import urllib3
import yaml
import logging
import json
import shutil
from datetime import datetime
import sys
import time
from pathlib import Path

import kubernetes
from kubernetes import client
from kubernetes_asyncio import client as k8s_async_client
from kubernetes_asyncio import config as k8s_async_config
from kubernetes_asyncio import watch as k8s_async_watch

import asyncio

from fmperf.Cluster import Cluster
from fmperf import LMBenchmarkWorkload
from fmperf.StackSpec import StackSpec
from fmperf.utils import run_benchmark

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

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


async def wait_for_job(job_name, namespace, timeout=7200):
    """Wait for the  job to complete"""
    logger.info(f"Waiting for job {job_name} to complete...")

    # use async config loading
    await k8s_async_config.load_kube_config()
    api_client = k8s_async_client.ApiClient()
    batch_v1_api = k8s_async_client.BatchV1Api(api_client)
    try:
        w = k8s_async_watch.Watch()

        # sets up connection with kubernetes, async with manages the streams lifecycle
        async with w.stream(
            func=batch_v1_api.list_namespaced_job,
            namespace=namespace,
            field_selector=f"metadata.name={job_name}",
            timeout_seconds=timeout  # replaces the manual timeout check
        ) as stream:

            async for event in stream: # replaces time.wait since we grab events as they come from stream sasynchronous
                job_status = event['object'].status
                if job_status.succeeded:
                    logger.info(f"Evaluation job {job_name} completed successfully.")
                    return True

                elif job_status.failed:
                    logger.error(f"Evaluation job {job_name} failed")
                    return False


    except asyncio.TimeoutError:
        logger.info(f"Timeout waiting for evaluation job {job_name} after {timeout} seconds.")
        return False
    except Exception as e:
        logger.error(f"Error occured while waiting for job {job_name} : {e}")
    finally:
        await api_client.close()


def capture_pod_logs(job_name, namespace, output_file : str):
    """Capture logs from pods created by a job
       Not specific to fmperf, as the pod logs are based on the job,
       rather than fmperf specifically
    """
    try:
        v1 = client.CoreV1Api()

        # get pods created by the job using label selector
        label_selector = f"job-name={job_name}"
        pods = v1.list_namespaced_pod(
            namespace=namespace,
            label_selector=label_selector
        )

        if not pods.items:
            logger.error(f"No pods found for job {job_name}")
            return None

        # get logs from the first pod
        pod = pods.items[0]
        pod_name = pod.metadata.name

        logger.info(f"Capturing logs from pod: {pod_name}")

        logs = v1.read_namespaced_pod_log(
            name=pod_name,
            namespace=namespace,
            pretty=True
        )

        # create dir is parent path doesnt exist
        Path(output_file).parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, 'w') as f:
            f.write(logs)
        logger.info(f"Wrote logs to: {output_file}")

        return logs

    except Exception as e:
        logger.error(f"Error capturing logs for job {job_name}: {e}")
        return None


def move_data_result(capture_log_file, data_dir):
    """Move the data result from the file mentioned in the log to the specified data directory."""

    sed_cmd =  's/^.*Finished benchmarking, dumping summary to \\(.*.csv\\).*$/\\1/p'
    os_command = [ 'sed', '-n', sed_cmd, capture_log_file ]
    result = subprocess.run(os_command, capture_output=True, text=True)
    if result.returncode != 0:
        logger.error(f"Error finding result data: {result.stderr}")
        return False

    if not os.path.exists(data_dir):
        # create missing directory
        try:
            os.makedirs(data_dir, exist_ok=True)
            logger.info(f"Created data directory: {data_dir}")
        except Exception as e:
            logger.error(f"Error creating data directory {data_dir}: {e}")
            return False

    data_files = set(result.stdout.strip().split("\n"))
    files_moved = []

    for data_file in data_files:
        if not data_file:
            continue
        data_file = data_file.strip()
        if not os.path.exists(data_file):
            logger.error(f"Data file does not exist: {data_file}")
            continue    # ignore the missing temp warm up files

        try:
            destination = os.path.join(data_dir, os.path.basename(data_file))
            os.rename(data_file, destination)
            files_moved.append(data_file)
            logger.info(f"Moved data file '{data_file}' to '{destination}'")
        except Exception as e:
            logger.error(f"Error moving data file '{data_file}' to '{destination}', result: {e}")
            return False
    if not files_moved:
        logger.error("No data files were moved, check the log file for details.")
        return False
    return True


def convert_data_result(capture_dir: str) -> None:
    """Convert benchmark results CSV files to benchmark reports.

    Args:
        capture_dir (str): Directory where results CSVs should be converted.
    """

    if not os.path.isdir(capture_dir):
        logger.error(f'Invalid directory: {capture_dir}')
        return

    for data_file in os.listdir(capture_dir):
        if data_file.lower()[-4:] != '.csv':
            continue
        data_file_full_path = os.path.join(capture_dir, data_file)
        logger.info(f'Converting file to benchmark report: {data_file_full_path}')
        os_command = [
            'convert.py',
            data_file_full_path,
            os.path.join(capture_dir, f'benchmark_report,_{data_file}.yaml'),
            '-w',
            'fmperf',
            '-f',
        ]
        result = subprocess.run(os_command, capture_output=True, text=True)
        if result.returncode != 0:
            # Report error, but do not quit
            logger.error(f'Error converting result data: {result.stderr}')

def main():

    env_vars = os.environ

    # Get results directory for configuration
    results_dir = env_vars.get("LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR", "/requests")

    Path(results_dir).mkdir(parents=True, exist_ok=True)

    file_handler = logging.FileHandler(f"{results_dir}/stdout.log")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    logger.info("Starting benchmark run")
    stack_name = env_vars.get("LLMDBENCH_HARNESS_STACK_NAME", "llm-d-3b-instruct")
    harness_name = env_vars.get("LLMDBENCH_HARNESS_NAME", "fmperf")
    experiment_id = env_vars.get("LLMDBENCH_RUN_EXPERIMENT_ID", "abc123")
    stack_type = env_vars.get("LLMDBENCH_HARNESS_STACK_TYPE", "llm-d")
    endpoint_url = env_vars.get("LLMDBENCH_HARNESS_STACK_ENDPOINT_URL", "inference-gateway")
    workload_file = env_vars.get("LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME", "llmdbench_workload.yaml")
    repetition = int(env_vars.get("LLMDBENCH_FMPERF_REPETITION", "1"))
    namespace = env_vars.get("LLMDBENCH_HARNESS_NAMESPACE", "llmdbench")
    job_id = env_vars.get("LLMDBENCH_FMPERF_JOB_ID", f"{stack_name}-{experiment_id}")

    logger.info(f"Using configuration:")
    logger.info(f"  Stack name: {stack_name}")
    logger.info(f"  Stack type: {stack_type}")
    logger.info(f"  Endpoint URL: {endpoint_url}")
    logger.info(f"  Workload file: {workload_file}")
    logger.info(f"  Repetition: {repetition}")
    logger.info(f"  Namespace: {namespace}")
    logger.info(f"  Job ID: {job_id}")
    logger.info(f"  Results directory (PVC): {results_dir}")

    workload_file_path = os.path.join("/workspace/profiles/fmperf", workload_file)
    logger.info(f"Loading workload configuration from {workload_file_path}")
    workload_spec = LMBenchmarkWorkload.from_yaml(workload_file_path)

    shutil.copy(workload_file_path, f"{results_dir}/{workload_file_path.split('/')[-1]}")

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
        # run benchmark which will create the evaluation job
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
        logger.info(f" {results_dir}/{stack_name}/")

        stem = "/eval-pod-lod.log"
        eval_path = results_dir
        if eval_path == "/requests": # customize eval path if default dir is /requests
            eval_path = f"{results_dir}/{harness_name}_{experiment_id}_{stack_name}"
        eval_log_file = eval_path + stem
        eval_data_dir = f"{eval_path}/analysis/data/"

        job_name = f"lmbenchmark-evaluate-{job_id}"
        logger.info(f"Waiting for evaluation job {job_name} to complete...")

        # Wait for the evaluation job to complete
        asyncio.run(wait_for_job(job_name, namespace))

        logs = capture_pod_logs(job_name, namespace, eval_log_file)
        if move_data_result(eval_log_file, eval_path):
            logger.info(f"Data moved to {eval_path}")
        # Create benchmark report
        logger.info(f"Performing benchmark report conversion")
        convert_data_result(eval_path)


    except Exception as e:
        logger.error(f"Benchmark run failed: {str(e)}")
        raise

if __name__ == "__main__":
    main()
