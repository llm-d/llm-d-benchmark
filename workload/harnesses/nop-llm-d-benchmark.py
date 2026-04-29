#!/usr/bin/env python3

"""
Benchmark 'nop' harness
"""
# pylint: disable=invalid-name

from __future__ import annotations
from datetime import datetime, timezone
import os
import logging
from pathlib import Path
import yaml

from kubernetes import client, config

from nop_functions import (
    get_env_variables,
    BenchmarkResult,
    convert_result,
    LoadFormat,
    benchmark_nop,
)

from fma_functions import benchmark_fma

# Configure logging
logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 60.0  # time (seconds) to wait for request
MAX_VLLM_WAIT = 15.0 * 60.0  # time (seconds) to wait for vllm to respond


def setup_logger(directory: str):
    """Configure the root logger"""

    Path(directory).mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler(f"{directory}/stdout.log"),
            logging.StreamHandler(),
        ],
    )


# pylint: disable=too-many-locals,too-many-statements
def main():
    """main entry point"""

    start_time = datetime.now().astimezone(timezone.utc).timestamp()
    write_log_per_process = False

    envs = get_env_variables(
        [
            "LLMDBENCH_CONTROL_WORK_DIR",
            "LLMDBENCH_STANDALONE_ENABLED",
            "LLMDBENCH_STANDALONE_LAUNCHER_ENABLED",
            "LLMDBENCH_FMA_ENABLED",
        ]
    )
    requests_dir = envs["LLMDBENCH_CONTROL_WORK_DIR"]
    setup_logger(requests_dir)

    standalone_enabled = envs["LLMDBENCH_STANDALONE_ENABLED"].lower() == "true"
    standalone_launcher_enabled = (
        envs["LLMDBENCH_STANDALONE_LAUNCHER_ENABLED"].lower() == "true"
    )
    fma_enabled = envs["LLMDBENCH_FMA_ENABLED"].lower() == "true"

    keys = [
        "LLMDBENCH_VLLM_COMMON_NAMESPACE",
        "LLMDBENCH_HARNESS_STACK_ENDPOINT_URL",
        "LLMDBENCH_VLLM_COMMON_VLLM_LOAD_FORMAT",
    ]
    if standalone_enabled and standalone_launcher_enabled:
        keys.extend(
            [
                "LLMDBENCH_VLLM_STANDALONE_LAUNCHER_PORT",
                "LLMDBENCH_VLLM_STANDALONE_LAUNCHER_VLLM_PORT",
            ]
        )
    if fma_enabled:
        keys.extend(["LLMDBENCH_FMA_LAUNCHER_CONFIG_PORT", "LLMDBENCH_FMA_ITERATIONS"])

    envs.update(get_env_variables(keys))
    logger.info("Environment variables:")
    for key, value in envs.items():
        logger.info("  '%s': '%s'", key, value)

    if not standalone_enabled and not fma_enabled:
        raise RuntimeError("Nop harness only handles mode 'standalone' or 'fma'")
    if standalone_enabled and fma_enabled:
        raise RuntimeError(
            "Nop harness only handles mode 'standalone' or 'fma', not both"
        )

    namespace = envs["LLMDBENCH_VLLM_COMMON_NAMESPACE"]
    endpoint_url = envs["LLMDBENCH_HARNESS_STACK_ENDPOINT_URL"]
    load_format = LoadFormat.loadformat_from_value(
        envs["LLMDBENCH_VLLM_COMMON_VLLM_LOAD_FORMAT"]
    )
    launcher_port = envs.get("LLMDBENCH_VLLM_STANDALONE_LAUNCHER_PORT", "0")
    launcher_vllm_port = envs.get("LLMDBENCH_VLLM_STANDALONE_LAUNCHER_VLLM_PORT", "0")

    fma_launcher_port = envs.get("LLMDBENCH_FMA_LAUNCHER_CONFIG_PORT", "0")
    fma_iterations = int(envs.get("LLMDBENCH_FMA_ITERATIONS", "0"))

    deploy_methods = []
    if standalone_enabled:
        deploy_methods.append("standalone")
    if fma_enabled:
        deploy_methods.append("fma")

    # Load Kubernetes configuration
    config.load_kube_config()
    v1 = client.CoreV1Api()
    apps_v1 = client.AppsV1Api()
    api = client.CustomObjectsApi()

    benchmark_result = BenchmarkResult()
    benchmark_result.scenario.deploy_methods = ",".join(deploy_methods)
    if fma_enabled:
        try:
            logger.info("Benchmark FMA launcher start...")
            benchmark_fma(
                v1,
                api,
                apps_v1,
                namespace,
                endpoint_url,
                fma_launcher_port,
                benchmark_result,
                load_format,
                requests_dir,
                fma_iterations,
                REQUEST_TIMEOUT,
                MAX_VLLM_WAIT,
                write_log_per_process,
            )
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("error on benchmark FMA launcher")
        finally:
            logger.info("Benchmark FMA launcher end")
    else:
        benchmark_nop(
            v1,
            namespace,
            endpoint_url,
            launcher_port,
            launcher_vllm_port,
            standalone_launcher_enabled,
            benchmark_result,
            load_format,
            requests_dir,
            REQUEST_TIMEOUT,
            MAX_VLLM_WAIT,
            write_log_per_process,
        )

    stop_time = datetime.now().astimezone(timezone.utc).timestamp()
    benchmark_result.time.start = start_time
    benchmark_result.time.stop = stop_time

    # write results yaml file
    result_filepath = os.path.join(requests_dir, "result.yaml")
    with open(result_filepath, "w", encoding="utf-8", newline="") as file:
        yaml.dump(benchmark_result.dump(), file, indent=2, sort_keys=False)
        logger.info("result yaml file saved to path: %s", result_filepath)

    benchmark_report_filepath = os.path.join(requests_dir, "benchmark_report")
    os.makedirs(benchmark_report_filepath, exist_ok=True)
    benchmark_report_filepath = os.path.join(benchmark_report_filepath, "result.yaml")
    convert_result(result_filepath, benchmark_report_filepath, start_time, stop_time)


if __name__ == "__main__":
    try:
        logger.info("Starting 'nop' harness run")
        main()
    except Exception:  # pylint: disable=broad-exception-caught
        logger.exception("Error running 'nop' harness")
    finally:
        logger.info("End 'nop' harness run")
