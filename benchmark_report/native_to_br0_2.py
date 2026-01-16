"""
Convert application native output formats into a Benchmark Report.
"""

import uuid
import ssl
import os
import re
import sys
from datetime import datetime, timezone

import numpy as np


from .base import Units, WorkloadGenerator
from .core import (
    check_file,
    get_nested,
    import_yaml,
    load_benchmark_report,
    update_dict,
)
from .schema_v0_2 import BenchmarkReportV02, Distribution, LoadSource


def _populate_run() -> dict:
    """Create a benchmark report with run details from environment variables.

    Returns:
        dict: dict with run section of BenchmarkReport.
    """
    # Unique ID for pod
    pid = os.environ.get("POD_UID")
    # Create an experiment ID from the results directory used (includes a timestamp)
    eid = str(uuid.uuid5(uuid.NAMESPACE_URL, os.environ.get("LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR")))
    # Create cluster ID from the API server certificate
    host = os.environ["KUBERNETES_SERVICE_HOST"]
    port = int(os.environ["KUBERNETES_SERVICE_PORT"])
    try:
        cert = ssl.get_server_certificate((host, port), timeout=5)
    except (TimeoutError, OSError):
        # As a failover, just use the service host
        cert = host
    cid = str(uuid.uuid5(uuid.NAMESPACE_DNS, cert))

    # Use the namespace for "user"
    try:
        with open("/var/run/secrets/kubernetes.io/serviceaccount/namespace", "r") as ff:
            namespace = ff.read().strip()
    except FileNotFoundError:
        namespace = os.environ.get("LLMDBENCH_VLLM_COMMON_NAMESPACE")

    br_dict = {
        "run": {
            "eid": eid,
            "cid": cid,
            "pid": pid,
            "user": "namespace=" + namespace,
            "time": {
                "start": os.environ.get("LLMDBENCH_HARNESS_START"),
                "end": os.environ.get("LLMDBENCH_HARNESS_STOP"),
                "duration": os.environ.get("LLMDBENCH_HARNESS_DELTA"),
            },
        },
    }
    return br_dict


def _populate_load() -> dict:
    """Create a benchmark report with scenario.load from environment variables.

    Returns:
        dict: dict with scenario.load part of of BenchmarkReport.
    """
    # Get arguments to harness command
    args_str = os.environ.get("LLMDBENCH_HARNESS_ARGS")
    kv_pairs = [kv.strip() for kv in args_str.split('--') if kv.strip()]
    args = {}
    for kv in kv_pairs:
        if "=" in kv:
            # Flag and value separated by "="
            key, value = kv.split("=", 1)
            key = key.strip()
            value = value.strip()
        elif " " in kv:
            # Flag and value separated by " "
            key, value = kv.split(" ", 1)
            key = key.strip()
            value = value.strip()
        else:
            # Flag-only argument
            key = kv
            value = None
        args[key] = value

    br_dict = {
        "scenario": {
            "load": {
                "standardized": {
                    "tool_version": os.environ.get("LLMDBENCH_HARNESS_VERSION", ""),
                },
                "native": {
                    "args": args,
                },
            },
        },
    }
    return br_dict


def _populate_aggregate_stack() -> dict:
    """Create a benchmark report with scenario.stack from environment variables
    for aggregate.

    Returns:
        dict: dict with scenario.stack part of of BenchmarkReport.
    """
    br_dict = {
        "scenario": {
            "stack": [
            ],
        },
    }
    return br_dict


def _populate_disaggregate_stack() -> dict:
    """Create a benchmark report with scenario.stack from environment variables
    for disaggregate.

    Returns:
        dict: dict with scenario.stack part of of BenchmarkReport.
    """
    br_dict = {
        "scenario": {
            "stack": [
            ],
        },
    }
    return br_dict


def _populate_stack() -> dict:
    """Create a benchmark report with scenario.stack from environment variables.

    Returns:
        dict: dict with scenario.stack part of of BenchmarkReport.
    """

    if "LLMDBENCH_DEPLOY_METHODS" not in os.environ:
        sys.stderr.write(
            "Warning: LLMDBENCH_DEPLOY_METHODS undefined, cannot determine deployment method\n"
        )
        return {}

    if os.environ["LLMDBENCH_DEPLOY_METHODS"] == "standalone":
        # This is an aggregate serving setup
        return _populate_aggregate_stack()

    if os.environ["LLMDBENCH_DEPLOY_METHODS"] == "modelservice":
        # This is a disaggregated serving setup
        return _populate_disaggregate_stack()

    sys.stderr.write(
        f"Warning: Unknown deployment method LLMDBENCH_DEPLOY_METHODS={os.environ["LLMDBENCH_DEPLOY_METHODS"]}\n"
    )
    return {}


def _populate_benchmark_report_from_envars() -> dict:
    """Create a benchmark report with details from environment variables.

    Returns:
        dict: run and scenario following schema of BenchmarkReport.
    """
    # Start benchmark report
    br_dict = {
        "version": "0.2",
        "run": {
            "uid": str(uuid.uuid4()), # Initial UID, may be updated
        }
    }

    # We make the assumption that if the environment variable
    # LLMDBENCH_MAGIC_ENVAR is defined, then we are inside a harness pod.
    if "LLMDBENCH_MAGIC_ENVAR" not in os.environ:
        # We are not in a harness pod
        return br_dict

    # Fill in more run details
    update_dict(br_dict, _populate_run())
    # Populate part of scenario.load
    update_dict(br_dict, _populate_load())
    # Populate part of scenario.stack
    update_dict(br_dict, _populate_stack())

    return br_dict


def _vllm_timestamp_to_iso(date_str: str) -> str:
    """Convert timestamp from vLLM benchmark into ISO-8601 format.

    This also works with InferenceMAX.
    String format is YYYYMMDD-HHMMSS in UTC.

    Args:
        date_str (str): Timestamp from vLLM benchmark.

    Returns:
        str: Timestamp in ISO-8601 format.
    """
    date_str = date_str.strip()
    if not re.search("[0-9]{8}-[0-9]{6}", date_str):
        sys.stderr.write(f"Invalid date format: {date_str}\n")
        return None
    year = int(date_str[0:4])
    month = int(date_str[4:6])
    day = int(date_str[6:8])
    hour = int(date_str[9:11])
    minute = int(date_str[11:13])
    second = int(date_str[13:15])

    return (
        datetime(year, month, day, hour, minute, second)
        .astimezone()
        .isoformat(timespec="seconds")
    )


def import_vllm_benchmark(results_file: str) -> BenchmarkReportV02:
    """Import data from a vLLM benchmark run as a BenchmarkReport.

    Args:
        results_file (str): Results file to import.

    Returns:
        BenchmarkReportV02: Imported data.
    """
    check_file(results_file)

    # Import results file from vLLM benchmark
    results = import_yaml(results_file)

    # Get environment variables from llm-d-benchmark run as a dict following the
    # schema of BenchmarkReportV02
    br_dict = _populate_benchmark_report_from_envars()

    # Add to that dict the data from vLLM benchmark.
    update_dict(
        br_dict,
        {
            "run": {"time": {"start": _vllm_timestamp_to_iso(results.get("date"))}},
            "scenario": {
                "load": {
                    "metadata": {
                        "schema_version": "0.0.1",
                        "cfg_id": "",  # TODO
                    },
                    "standardized": {
                        "tool": WorkloadGenerator.VLLM_BENCHMARK,
                        "stage": 0,
                        "rate_qps": results.get("request_rate"),
                        "concurrency": results.get("max_concurrency"),
                        "source": LoadSource.RANDOM, # TODO
                        "input_seq_len": {
                            "distribution": Distribution.FIXED, # TODO
                            "value": 1, # TODO
                        },
                    },
                },
            },
            "results": {
                "request_performance": {
                    "aggregate": {
                        "requests": {
                            "total": results.get("num_prompts"),
                            "failures": results.get("num_prompts")
                            - results.get("completed"),
                            "input_length": {
                                "units": Units.COUNT,
                                "mean": results.get("total_input_tokens", 0)
                                / results.get("num_prompts", -1),
                            },
                            "output_length": {
                                "units": Units.COUNT,
                                "mean": results.get("total_output_tokens", 0)
                                / results.get("completed", -1),
                            },
                        },
                        "latency": {
                            "time_to_first_token": {
                                "units": Units.MS,
                                "mean": results.get("mean_ttft_ms"),
                                "stddev": results.get("std_ttft_ms"),
                                "p0p1": results.get("p0.1_ttft_ms"),
                                "p1": results.get("p1_ttft_ms"),
                                "p5": results.get("p5_ttft_ms"),
                                "p10": results.get("p10_ttft_ms"),
                                "P25": results.get("p25_ttft_ms"),
                                "p50": results.get("median_ttft_ms"),
                                "p75": results.get("p75_ttft_ms"),
                                "p90": results.get("p90_ttft_ms"),
                                "p95": results.get("p95_ttft_ms"),
                                "p99": results.get("p99_ttft_ms"),
                                "p99p9": results.get("p99.9_ttft_ms"),
                            },
                            "time_per_output_token": {
                                "units": Units.MS_PER_TOKEN,
                                "mean": results.get("mean_tpot_ms"),
                                "stddev": results.get("std_tpot_ms"),
                                "p0p1": results.get("p0.1_tpot_ms"),
                                "p1": results.get("p1_tpot_ms"),
                                "p5": results.get("p5_tpot_ms"),
                                "p10": results.get("p10_tpot_ms"),
                                "P25": results.get("p25_tpot_ms"),
                                "p50": results.get("median_tpot_ms"),
                                "p75": results.get("p75_tpot_ms"),
                                "p90": results.get("p90_tpot_ms"),
                                "p95": results.get("p95_tpot_ms"),
                                "p99": results.get("p99_tpot_ms"),
                                "p99p9": results.get("p99.9_tpot_ms"),
                            },
                            "inter_token_latency": {
                                "units": Units.MS_PER_TOKEN,
                                "mean": results.get("mean_itl_ms"),
                                "stddev": results.get("std_itl_ms"),
                                "p0p1": results.get("p0.1_itl_ms"),
                                "p1": results.get("p1_itl_ms"),
                                "p5": results.get("p5_itl_ms"),
                                "p10": results.get("p10_itl_ms"),
                                "P25": results.get("p25_itl_ms"),
                                "p90": results.get("p90_itl_ms"),
                                "p95": results.get("p95_itl_ms"),
                                "p99": results.get("p99_itl_ms"),
                                "p99p9": results.get("p99.9_itl_ms"),
                            },
                            "request_latency": {
                                "units": Units.MS,
                                "mean": results.get("mean_e2el_ms"),
                                "stddev": results.get("std_e2el_ms"),
                                "p0p1": results.get("p0.1_e2el_ms"),
                                "p1": results.get("p1_e2el_ms"),
                                "p5": results.get("p5_e2el_ms"),
                                "p10": results.get("p10_e2el_ms"),
                                "P25": results.get("p25_e2el_ms"),
                                "p90": results.get("p90_e2el_ms"),
                                "p95": results.get("p95_e2el_ms"),
                                "p99": results.get("p99_e2el_ms"),
                                "p99p9": results.get("p99.9_e2el_ms"),
                            },
                        },
                        "throughput": {
                            "output_token_rate": {
                                "units": Units.TOKEN_PER_S,
                                "mean": results.get("output_throughput"),
                            },
                            "total_token_rate": {
                                "units": Units.TOKEN_PER_S,
                                "mean": results.get("total_token_throughput"),
                            },
                            "request_rate": {
                                "units": Units.QUERY_PER_S,
                                "mean": results.get("request_throughput"),
                            },
                        },
                    },
                },
            },
        },
    )

    return load_benchmark_report(br_dict)


def import_inference_max(results_file: str) -> BenchmarkReportV02:
    """Import data from an InferenceMAX benchmark run as a BenchmarkReportV01.

    Args:
        results_file (str): Results file to import.

    Returns:
        BenchmarkReportV02: Imported data.
    """
    check_file(results_file)

    # Import results file from vLLM benchmark
    results = import_yaml(results_file)

    # Get environment variables from llm-d-benchmark run as a dict following the
    # schema of BenchmarkReportV02
    br_dict = _populate_benchmark_report_from_envars()

    # Add to that dict the data from vLLM benchmark.
    update_dict(
        br_dict,
        {
            "run": {
                "time": {
                    "start": _vllm_timestamp_to_iso(results.get("date")),
                    "duration": f"PT{results.get('duration')}S",
                }
            },
            "scenario": {
                "load": {
                    "metadata": {
                        "schema_version": "0.0.1",
                        "cfg_id": "",  # TODO
                    },
                    "standardized": {
                        "tool": WorkloadGenerator.INFERENCE_MAX,
                        "stage": 0,
                        "rate_qps": results.get("request_rate"),
                        "concurrency": results.get("max_concurrency"),
                        "source": LoadSource.RANDOM, # TODO
                        "input_seq_len": {
                            "distribution": Distribution.FIXED, # TODO
                            "value": 1, # TODO
                        },
                    },
                },
            },
            "results": {
                "request_performance": {
                    "aggregate": {
                        "requests": {
                            "total": results.get("num_prompts"),
                            "failures": results.get("num_prompts")
                            - results.get("completed"),
                            "input_length": {
                                "units": Units.COUNT,
                                "mean": np.array(results.get("input_lens", [0])).mean(),
                            },
                            "output_length": {
                                "units": Units.COUNT,
                                "mean": np.array(
                                    results.get("output_lens", [0])
                                ).mean(),
                            },
                        },
                        "latency": {
                            "time_to_first_token": {
                                "units": Units.MS,
                                "mean": results.get("mean_ttft_ms"),
                                "stddev": results.get("std_ttft_ms"),
                                "p0p1": results.get("p0.1_ttft_ms"),
                                "p1": results.get("p1_ttft_ms"),
                                "p5": results.get("p5_ttft_ms"),
                                "p10": results.get("p10_ttft_ms"),
                                "P25": results.get("p25_ttft_ms"),
                                "p50": results.get("median_ttft_ms"),
                                "p75": results.get("p75_ttft_ms"),
                                "p90": results.get("p90_ttft_ms"),
                                "p95": results.get("p95_ttft_ms"),
                                "p99": results.get("p99_ttft_ms"),
                                "p99p9": results.get("p99.9_ttft_ms"),
                            },
                            "time_per_output_token": {
                                "units": Units.MS_PER_TOKEN,
                                "mean": results.get("mean_tpot_ms"),
                                "stddev": results.get("std_tpot_ms"),
                                "p0p1": results.get("p0.1_tpot_ms"),
                                "p1": results.get("p1_tpot_ms"),
                                "p5": results.get("p5_tpot_ms"),
                                "p10": results.get("p10_tpot_ms"),
                                "P25": results.get("p25_tpot_ms"),
                                "p50": results.get("median_tpot_ms"),
                                "p75": results.get("p75_tpot_ms"),
                                "p90": results.get("p90_tpot_ms"),
                                "p95": results.get("p95_tpot_ms"),
                                "p99": results.get("p99_tpot_ms"),
                                "p99p9": results.get("p99.9_tpot_ms"),
                            },
                            "inter_token_latency": {
                                "units": Units.MS_PER_TOKEN,
                                "mean": results.get("mean_itl_ms"),
                                "stddev": results.get("std_itl_ms"),
                                "p0p1": results.get("p0.1_itl_ms"),
                                "p1": results.get("p1_itl_ms"),
                                "p5": results.get("p5_itl_ms"),
                                "p10": results.get("p10_itl_ms"),
                                "P25": results.get("p25_itl_ms"),
                                "p90": results.get("p90_itl_ms"),
                                "p95": results.get("p95_itl_ms"),
                                "p99": results.get("p99_itl_ms"),
                                "p99p9": results.get("p99.9_itl_ms"),
                            },
                            "request_latency": {
                                "units": Units.MS,
                                "mean": results.get("mean_e2el_ms"),
                                "stddev": results.get("std_e2el_ms"),
                                "p0p1": results.get("p0.1_e2el_ms"),
                                "p1": results.get("p1_e2el_ms"),
                                "p5": results.get("p5_e2el_ms"),
                                "p10": results.get("p10_e2el_ms"),
                                "P25": results.get("p25_e2el_ms"),
                                "p90": results.get("p90_e2el_ms"),
                                "p95": results.get("p95_e2el_ms"),
                                "p99": results.get("p99_e2el_ms"),
                                "p99p9": results.get("p99.9_e2el_ms"),
                            },
                        },
                        "throughput": {
                            "output_token_rate": {
                                "units": Units.TOKEN_PER_S,
                                "mean": results.get("output_throughput"),
                            },
                            "total_token_rate": {
                                "units": Units.TOKEN_PER_S,
                                "mean": results.get("total_token_throughput"),
                            },
                            "request_rate": {
                                "units": Units.QUERY_PER_S,
                                "mean": results.get("request_throughput"),
                            },
                        },
                    },
                },
            },
        },
    )

    return load_benchmark_report(br_dict)


def import_inference_perf(results_file: str) -> BenchmarkReportV02:
    """Import data from a Inference Perf run as a BenchmarkReportV02.

    Args:
        results_file (str): Results file to import.

    Returns:
        BenchmarkReportV02: Imported data.
    """
    check_file(results_file)

    # Import results from Inference Perf
    results = import_yaml(results_file)

    # Get stage number from metrics filename
    stage = int(results_file.rsplit("stage_")[-1].split("_", 1)[0])

    # Get environment variables from llm-d-benchmark run as a dict following the
    # schema of BenchmarkReportV02
    br_dict = _populate_benchmark_report_from_envars()

    config = get_nested(br_dict, ["scenario", "load", "native", "config"], {})

    # Add to that dict the data from Inference Perf
    update_dict(
        br_dict,
        {
            "scenario": {
                "load": {
                    "metadata": {
                        "schema_version": "0.0.1",
                        "cfg_id": "",  # TODO
                    },
                    "standardized": {
                        "tool": WorkloadGenerator.INFERENCE_PERF,
                        "stage": stage,
                        "rate_qps": get_nested(results, ["load_summary", "requested_rate"]),
                        "concurrency": get_nested(results, ["load_summary", "concurrency"]),
                        "source": LoadSource.RANDOM, # TODO see https://github.com/kubernetes-sigs/inference-perf/blob/main/docs/config.md?plain=1#L42
                        "input_seq_len": {
                            "distribution": Distribution.OTHER, #  TODO
                            # If br_dict has config file from
                            # _populate_benchmark_report_from_envars, get
                            # details from there, otherwise get what is available
                            # from results file.
                            "value": get_nested(config, ["data", "input_distribution", "mean"], get_nested(results, ["successes", "prompt_len", "mean"])),
                            "std_dev": get_nested(config, ["data", "input_distribution", "std"]),
                            "min": get_nested(config, ["data", "input_distribution", "min"], get_nested(results, ["successes", "prompt_len", "min"])),
                            "max": get_nested(config, ["data", "input_distribution", "max"], get_nested(results, ["successes", "prompt_len", "max"])),
                        },
                    },
                    "native": {
                        "config": config,
                    },
                },
            },
           "results": {
                "request_performance": {
                    "aggregate": {
                        "requests": {
                            "total": get_nested(results, ["load_summary", "count"]),
                            "failures": get_nested(results, ["failures", "count"]),
                            "input_length": {
                                "units": Units.COUNT,
                                "mean": get_nested(results, ["successes", "prompt_len", "mean"]),
                                "min": get_nested(results, ["successes", "prompt_len", "min"]),
                                "p0p1": get_nested(results, ["successes", "prompt_len", "p0.1"]),
                                "p1": get_nested(results, ["successes", "prompt_len", "p1"]),
                                "p5": get_nested(results, ["successes", "prompt_len", "p5"]),
                                "p10": get_nested(results, ["successes", "prompt_len", "p10"]),
                                "p25": get_nested(results, ["successes", "prompt_len", "p25"]),
                                "p50": get_nested(results, ["successes", "prompt_len", "median"]),
                                "p75": get_nested(results, ["successes", "prompt_len", "p75"]),
                                "p90": get_nested(results, ["successes", "prompt_len", "p90"]),
                                "p95": get_nested(results, ["successes", "prompt_len", "p95"]),
                                "p99": get_nested(results, ["successes", "prompt_len", "p99"]),
                                "p99p9": get_nested(results, ["successes", "prompt_len", "p99.9"]),
                                "max": get_nested(results, ["successes", "prompt_len", "max"]),
                            },
                            "output_length": {
                                "units": Units.COUNT,
                                "mean": get_nested(results, ["successes", "output_len", "mean"]),
                                "min": get_nested(results, ["successes", "output_len", "min"]),
                                "p0p1": get_nested(results, ["successes", "output_len", "p0.1"]),
                                "p1": get_nested(results, ["successes", "output_len", "p1"]),
                                "p5": get_nested(results, ["successes", "output_len", "p5"]),
                                "p10": get_nested(results, ["successes", "output_len", "p10"]),
                                "p25": get_nested(results, ["successes", "output_len", "p25"]),
                                "p50": get_nested(results, ["successes", "output_len", "median"]),
                                "p75": get_nested(results, ["successes", "output_len", "p75"]),
                                "p90": get_nested(results, ["successes", "output_len", "p90"]),
                                "p95": get_nested(results, ["successes", "output_len", "p95"]),
                                "p99": get_nested(results, ["successes", "output_len", "p99"]),
                                "p99p9": get_nested(results, ["successes", "output_len", "p99.9"]),
                                "max": get_nested(results, ["successes", "output_len", "max"]),
                            },
                        },
                        "latency": {
                            "time_to_first_token": {
                                "units": Units.S,
                                "mean": get_nested(results, ["successes", "latency", "time_to_first_token", "mean"]),
                                "min": get_nested(results, ["successes", "latency", "time_to_first_token", "min"]),
                                "p0p1": get_nested(results, ["successes", "latency", "time_to_first_token", "p0.1"]),
                                "p1": get_nested(results, ["successes", "latency", "time_to_first_token", "p1"]),
                                "p5": get_nested(results, ["successes", "latency", "time_to_first_token", "p5"]),
                                "p10": get_nested(results, ["successes", "latency", "time_to_first_token", "p10"]),
                                "p25": get_nested(results, ["successes", "latency", "time_to_first_token", "p25"]),
                                "p50": get_nested(results, ["successes", "latency", "time_to_first_token", "median"]),
                                "p75": get_nested(results, ["successes", "latency", "time_to_first_token", "p75"]),
                                "p90": get_nested(results, ["successes", "latency", "time_to_first_token", "p90"]),
                                "p95": get_nested(results, ["successes", "latency", "time_to_first_token", "p95"]),
                                "p99": get_nested(results, ["successes", "latency", "time_to_first_token", "p99"]),
                                "p99p9": get_nested(results, ["successes", "latency", "time_to_first_token", "p99.9"]),
                                "max": get_nested(results, ["successes", "latency", "time_to_first_token", "max"]),
                            },
                            "normalized_time_per_output_token": {
                                "units": Units.S_PER_TOKEN,
                                "mean": get_nested(results, ["successes", "latency", "normalized_time_per_output_token", "mean"]),
                                "min": get_nested(results, ["successes", "latency", "normalized_time_per_output_token", "min"]),
                                "p0p1": get_nested(results, ["successes", "latency", "normalized_time_per_output_token", "p0.1"]),
                                "p1": get_nested(results, ["successes", "latency", "normalized_time_per_output_token", "p1"]),
                                "p5": get_nested(results, ["successes", "latency", "normalized_time_per_output_token", "p5"]),
                                "p10": get_nested(results, ["successes", "latency", "normalized_time_per_output_token", "p10"]),
                                "p25": get_nested(results, ["successes", "latency", "normalized_time_per_output_token", "p25"]),
                                "p50": get_nested(results, ["successes", "latency", "normalized_time_per_output_token", "median"]),
                                "p75": get_nested(results, ["successes", "latency", "normalized_time_per_output_token", "p75"]),
                                "p90": get_nested(results, ["successes", "latency", "normalized_time_per_output_token", "p90"]),
                                "p95": get_nested(results, ["successes", "latency", "normalized_time_per_output_token", "p95"]),
                                "p99": get_nested(results, ["successes", "latency", "normalized_time_per_output_token", "p99"]),
                                "p99p9": get_nested(results, ["successes", "latency", "normalized_time_per_output_token", "p99.9"]),
                                "max": get_nested(results, ["successes", "latency", "normalized_time_per_output_token", "max"]),
                            },
                            "time_per_output_token": {
                                "units": Units.S_PER_TOKEN,
                                "mean": get_nested(results, ["successes", "latency", "time_per_output_token", "mean"]),
                                "min": get_nested(results, ["successes", "latency", "time_per_output_token", "min"]),
                                "p0p1": get_nested(results, ["successes", "latency", "time_per_output_token", "p0.1"]),
                                "p1": get_nested(results, ["successes", "latency", "time_per_output_token", "p1"]),
                                "p5": get_nested(results, ["successes", "latency", "time_per_output_token", "p5"]),
                                "p10": get_nested(results, ["successes", "latency", "time_per_output_token", "p10"]),
                                "p25": get_nested(results, ["successes", "latency", "time_per_output_token", "p25"]),
                                "p50": get_nested(results, ["successes", "latency", "time_per_output_token", "median"]),
                                "p75": get_nested(results, ["successes", "latency", "time_per_output_token", "p75"]),
                                "p90": get_nested(results, ["successes", "latency", "time_per_output_token", "p90"]),
                                "p95": get_nested(results, ["successes", "latency", "time_per_output_token", "p95"]),
                                "p99": get_nested(results, ["successes", "latency", "time_per_output_token", "p99"]),
                                "p99p9": get_nested(results, ["successes", "latency", "time_per_output_token", "p99.9"]),
                                "max": get_nested(results, ["successes", "latency", "time_per_output_token", "max"]),
                            },
                            "inter_token_latency": {
                                "units": Units.S_PER_TOKEN,
                                "mean": get_nested(results, ["successes", "latency", "inter_token_latency", "mean"]),
                                "min": get_nested(results, ["successes", "latency", "inter_token_latency", "min"]),
                                "p0p1": get_nested(results, ["successes", "latency", "inter_token_latency", "p0.1"]),
                                "p1": get_nested(results, ["successes", "latency", "inter_token_latency", "p1"]),
                                "p5": get_nested(results, ["successes", "latency", "inter_token_latency", "p5"]),
                                "p10": get_nested(results, ["successes", "latency", "inter_token_latency", "p10"]),
                                "p25": get_nested(results, ["successes", "latency", "inter_token_latency", "p25"]),
                                "p50": get_nested(results, ["successes", "latency", "inter_token_latency", "median"]),
                                "p75": get_nested(results, ["successes", "latency", "inter_token_latency", "p75"]),
                                "p90": get_nested(results, ["successes", "latency", "inter_token_latency", "p90"]),
                                "p95": get_nested(results, ["successes", "latency", "inter_token_latency", "p95"]),
                                "p99": get_nested(results, ["successes", "latency", "inter_token_latency", "p99"]),
                                "p99p9": get_nested(results, ["successes", "latency", "inter_token_latency", "p99.9"]),
                                "max": get_nested(results, ["successes", "latency", "inter_token_latency", "max"]),
                            },
                            "request_latency": {
                                "units": Units.S,
                                "mean": get_nested(results, ["successes", "latency", "request_latency", "mean"]),
                                "min": get_nested(results, ["successes", "latency", "request_latency", "min"]),
                                "p0p1": get_nested(results, ["successes", "latency", "request_latency", "p0.1"]),
                                "p1": get_nested(results, ["successes", "latency", "request_latency", "p1"]),
                                "p5": get_nested(results, ["successes", "latency", "request_latency", "p5"]),
                                "p10": get_nested(results, ["successes", "latency", "request_latency", "p10"]),
                                "p25": get_nested(results, ["successes", "latency", "request_latency", "p25"]),
                                "p50": get_nested(results, ["successes", "latency", "request_latency", "median"]),
                                "p75": get_nested(results, ["successes", "latency", "request_latency", "p75"]),
                                "p90": get_nested(results, ["successes", "latency", "request_latency", "p90"]),
                                "p95": get_nested(results, ["successes", "latency", "request_latency", "p95"]),
                                "p99": get_nested(results, ["successes", "latency", "request_latency", "p99"]),
                                "p99p9": get_nested(results, ["successes", "latency", "request_latency", "p99.9"]),
                                "max": get_nested(results, ["successes", "latency", "request_latency", "max"]),
                            },
                        },
                        "throughput": {
                            "output_token_rate": {
                                "units": Units.TOKEN_PER_S,
                                "mean": get_nested(results, ["successes", "throughput", "output_tokens_per_sec"]),
                            },
                            "total_token_rate": {
                                "units": Units.TOKEN_PER_S,
                                "mean": get_nested(results, ["successes", "throughput", "total_tokens_per_sec"]),
                            },
                            "request_rate": {
                                "units": Units.QUERY_PER_S,
                                "mean": get_nested(results, ["successes", "throughput", "requests_per_sec"]),
                            },
                        },
                    },
                },
            },
        },
    )

    return load_benchmark_report(br_dict)


def import_guidellm(results_file: str, index: int = 0) -> BenchmarkReportV02:
    """Import data from a GuideLLM run as a BenchmarkReportV02.

    Args:
        results_file (str): Results file to import.
        index (int): Benchmark index to import.

    Returns:
        BenchmarkReportV02: Imported data.
    """
    check_file(results_file)

    data = import_yaml(results_file)

    results = data["benchmarks"][index]

    # Convert Unix epoch floats to ISO-8601 timestamps
    t_start = (
        datetime.fromtimestamp(results["start_time"], tz=timezone.utc)
        .astimezone()
        .isoformat(timespec="seconds")
    )
    t_stop = (
        datetime.fromtimestamp(results["end_time"], tz=timezone.utc)
        .astimezone()
        .isoformat(timespec="seconds")
    )

    # Get environment variables from llm-d-benchmark run as a dict following the
    # schema of BenchmarkReportV02
    br_dict = _populate_benchmark_report_from_envars()

    # Add to that dict the data from GuideLLM
    update_dict(
        br_dict,
        {
            "run": {
                "time": {
                    "duration": f"PT{results['duration']}S",
                    "start": t_start,
                    "end": t_stop,
                },
            },
            "scenario": {
                "load": {
                    "metadata": {
                        "schema_version": "0.0.1",
                        "cfg_id": "",  # TODO
                    },
                    "standardized": {
                        "tool": WorkloadGenerator.GUIDELLM,
                        "stage": index,
                        "source": LoadSource.RANDOM, # TODO
                        "input_seq_len": {
                            "distribution": Distribution.FIXED, # TODO
                            "value": 1, # TODO
                        },
                    },
                    "native": {
                        "args": data["args"],
                    },
                },
            },
            "results": {
                "request_performance": {
                    "aggregate": {
                        "requests": {
                            "total": get_nested(results, ["metrics", "request_totals", "total"]),
                            "failures": get_nested(results, ["metrics", "request_totals", "errored"]),
                            "incomplete": get_nested(results, ["metrics", "request_totals", "incomplete"]),
                            "input_length": {
                                "units": Units.COUNT,
                                "mean": get_nested(results, ["metrics", "prompt_token_count", "successful", "mean"]),
                                "mode": get_nested(results, ["metrics", "prompt_token_count", "successful", "mode"]),
                                "stddev": get_nested(results, ["metrics", "prompt_token_count", "successful", "std_dev"]),
                                "min": get_nested(results, ["metrics", "prompt_token_count", "successful", "min"]),
                                "p0p1": get_nested(results, ["metrics", "prompt_token_count", "successful", "percentiles", "p001"]),
                                "p1": get_nested(results, ["metrics", "prompt_token_count", "successful", "percentiles", "p01"]),
                                "p5": get_nested(results, ["metrics", "prompt_token_count", "successful", "percentiles", "p05"]),
                                "p10": get_nested(results, ["metrics", "prompt_token_count", "successful", "percentiles", "p10"]),
                                "p25": get_nested(results, ["metrics", "prompt_token_count", "successful", "percentiles", "p25"]),
                                "p50": get_nested(results, ["metrics", "prompt_token_count", "successful", "percentiles", "p50"]),
                                "p75": get_nested(results, ["metrics", "prompt_token_count", "successful", "percentiles", "p75"]),
                                "p90": get_nested(results, ["metrics", "prompt_token_count", "successful", "percentiles", "p90"]),
                                "p95": get_nested(results, ["metrics", "prompt_token_count", "successful", "percentiles", "p95"]),
                                "p99": get_nested(results, ["metrics", "prompt_token_count", "successful", "percentiles", "p99"]),
                                "p99p9": get_nested(results, ["metrics", "prompt_token_count", "successful", "percentiles", "p999"]),
                                "max": get_nested(results, ["metrics", "prompt_token_count", "successful", "max"]),
                            },
                            "output_length": {
                                "units": Units.COUNT,
                                "mean": get_nested(results, ["metrics", "output_token_count", "successful", "mean"]),
                                "mode": get_nested(results, ["metrics", "output_token_count", "successful", "mode"]),
                                "stddev": get_nested(results, ["metrics", "output_token_count", "successful", "std_dev"]),
                                "min": get_nested(results, ["metrics", "output_token_count", "successful", "min"]),
                                "p0p1": get_nested(results, ["metrics", "output_token_count", "successful", "percentiles", "p001"]),
                                "p1": get_nested(results, ["metrics", "output_token_count", "successful", "percentiles", "p01"]),
                                "p5": get_nested(results, ["metrics", "output_token_count", "successful", "percentiles", "p05"]),
                                "p10": get_nested(results, ["metrics", "output_token_count", "successful", "percentiles", "p10"]),
                                "p25": get_nested(results, ["metrics", "output_token_count", "successful", "percentiles", "p25"]),
                                "p50": get_nested(results, ["metrics", "output_token_count", "successful", "percentiles", "p50"]),
                                "p75": get_nested(results, ["metrics", "output_token_count", "successful", "percentiles", "p75"]),
                                "p90": get_nested(results, ["metrics", "output_token_count", "successful", "percentiles", "p90"]),
                                "p95": get_nested(results, ["metrics", "output_token_count", "successful", "percentiles", "p95"]),
                                "p99": get_nested(results, ["metrics", "output_token_count", "successful", "percentiles", "p99"]),
                                "p99p9": get_nested(results, ["metrics", "output_token_count", "successful", "percentiles", "p999"]),
                                "max": get_nested(results, ["metrics", "output_token_count", "successful", "max"]),
                            },
                        },
                        "latency": {
                            "time_to_first_token": {
                                "units": Units.MS,
                                "mean": get_nested(results, ["metrics", "time_to_first_token_ms", "successful", "mean"]),
                                "mode": get_nested(results, ["metrics", "time_to_first_token_ms", "successful", "mode"]),
                                "stddev": get_nested(results, ["metrics", "time_to_first_token_ms", "successful", "std_dev"]),
                                "min": get_nested(results, ["metrics", "time_to_first_token_ms", "successful", "min"]),
                                "p0p1": get_nested(results, ["metrics", "time_to_first_token_ms", "successful", "percentiles", "p001"]),
                                "p1": get_nested(results, ["metrics", "time_to_first_token_ms", "successful", "percentiles", "p01"]),
                                "p5": get_nested(results, ["metrics", "time_to_first_token_ms", "successful", "percentiles", "p05"]),
                                "p10": get_nested(results, ["metrics", "time_to_first_token_ms", "successful", "percentiles", "p10"]),
                                "p25": get_nested(results, ["metrics", "time_to_first_token_ms", "successful", "percentiles", "p25"]),
                                "p50": get_nested(results, ["metrics", "time_to_first_token_ms", "successful", "percentiles", "p50"]),
                                "p75": get_nested(results, ["metrics", "time_to_first_token_ms", "successful", "percentiles", "p75"]),
                                "p90": get_nested(results, ["metrics", "time_to_first_token_ms", "successful", "percentiles", "p90"]),
                                "p95": get_nested(results, ["metrics", "time_to_first_token_ms", "successful", "percentiles", "p95"]),
                                "p99": get_nested(results, ["metrics", "time_to_first_token_ms", "successful", "percentiles", "p99"]),
                                "p99p9": get_nested(results, ["metrics", "time_to_first_token_ms", "successful", "percentiles", "p999"]),
                                "max": get_nested(results, ["metrics", "time_to_first_token_ms", "successful", "max"]),
                            },
                            "time_per_output_token": {
                                "units": Units.MS_PER_TOKEN,
                                "mean": get_nested(results, ["metrics", "time_per_output_token_ms", "successful", "mean"]),
                                "mode": get_nested(results, ["metrics", "time_per_output_token_ms", "successful", "mode"]),
                                "stddev": get_nested(results, ["metrics", "time_per_output_token_ms", "successful", "std_dev"]),
                                "min": get_nested(results, ["metrics", "time_per_output_token_ms", "successful", "min"]),
                                "p0p1": get_nested(results, ["metrics", "time_per_output_token_ms", "successful", "percentiles", "p001"]),
                                "p1": get_nested(results, ["metrics", "time_per_output_token_ms", "successful", "percentiles", "p01"]),
                                "p5": get_nested(results, ["metrics", "time_per_output_token_ms", "successful", "percentiles", "p05"]),
                                "p10": get_nested(results, ["metrics", "time_per_output_token_ms", "successful", "percentiles", "p10"]),
                                "p25": get_nested(results, ["metrics", "time_per_output_token_ms", "successful", "percentiles", "p25"]),
                                "p50": get_nested(results, ["metrics", "time_per_output_token_ms", "successful", "percentiles", "p50"]),
                                "p75": get_nested(results, ["metrics", "time_per_output_token_ms", "successful", "percentiles", "p75"]),
                                "p90": get_nested(results, ["metrics", "time_per_output_token_ms", "successful", "percentiles", "p90"]),
                                "p95": get_nested(results, ["metrics", "time_per_output_token_ms", "successful", "percentiles", "p95"]),
                                "p99": get_nested(results, ["metrics", "time_per_output_token_ms", "successful", "percentiles", "p99"]),
                                "p99p9": get_nested(results, ["metrics", "time_per_output_token_ms", "successful", "percentiles", "p999"]),
                                "max": get_nested(results, ["metrics", "time_per_output_token_ms", "successful", "max"]),
                            },
                            "inter_token_latency": {
                                "units": Units.MS_PER_TOKEN,
                                "mean": get_nested(results, ["metrics", "inter_token_latency_ms", "successful", "mean"]),
                                "mode": get_nested(results, ["metrics", "inter_token_latency_ms", "successful", "mode"]),
                                "stddev": get_nested(results, ["metrics", "inter_token_latency_ms", "successful", "std_dev"]),
                                "min": get_nested(results, ["metrics", "inter_token_latency_ms", "successful", "min"]),
                                "p0p1": get_nested(results, ["metrics", "inter_token_latency_ms", "successful", "percentiles", "p001"]),
                                "p1": get_nested(results, ["metrics", "inter_token_latency_ms", "successful", "percentiles", "p01"]),
                                "p5": get_nested(results, ["metrics", "inter_token_latency_ms", "successful", "percentiles", "p05"]),
                                "p10": get_nested(results, ["metrics", "inter_token_latency_ms", "successful", "percentiles", "p10"]),
                                "p25": get_nested(results, ["metrics", "inter_token_latency_ms", "successful", "percentiles", "p25"]),
                                "p50": get_nested(results, ["metrics", "inter_token_latency_ms", "successful", "percentiles", "p50"]),
                                "p75": get_nested(results, ["metrics", "inter_token_latency_ms", "successful", "percentiles", "p75"]),
                                "p90": get_nested(results, ["metrics", "inter_token_latency_ms", "successful", "percentiles", "p90"]),
                                "p95": get_nested(results, ["metrics", "inter_token_latency_ms", "successful", "percentiles", "p95"]),
                                "p99": get_nested(results, ["metrics", "inter_token_latency_ms", "successful", "percentiles", "p99"]),
                                "p99p9": get_nested(results, ["metrics", "inter_token_latency_ms", "successful", "percentiles", "p999"]),
                                "max": get_nested(results, ["metrics", "inter_token_latency_ms", "successful", "max"]),
                            },
                            "request_latency": {
                                "units": Units.MS,
                                "mean": get_nested(results, ["metrics", "request_latency", "successful", "mean"]),
                                "mode": get_nested(results, ["metrics", "request_latency", "successful", "mode"]),
                                "stddev": get_nested(results, ["metrics", "request_latency", "successful", "std_dev"]),
                                "min": get_nested(results, ["metrics", "request_latency", "successful", "min"]),
                                "p0p1": get_nested(results, ["metrics", "request_latency", "successful", "percentiles", "p001"]),
                                "p1": get_nested(results, ["metrics", "request_latency", "successful", "percentiles", "p01"]),
                                "p5": get_nested(results, ["metrics", "request_latency", "successful", "percentiles", "p05"]),
                                "p10": get_nested(results, ["metrics", "request_latency", "successful", "percentiles", "p10"]),
                                "p25": get_nested(results, ["metrics", "request_latency", "successful", "percentiles", "p25"]),
                                "p50": get_nested(results, ["metrics", "request_latency", "successful", "percentiles", "p50"]),
                                "p75": get_nested(results, ["metrics", "request_latency", "successful", "percentiles", "p75"]),
                                "p90": get_nested(results, ["metrics", "request_latency", "successful", "percentiles", "p90"]),
                                "p95": get_nested(results, ["metrics", "request_latency", "successful", "percentiles", "p95"]),
                                "p99": get_nested(results, ["metrics", "request_latency", "successful", "percentiles", "p99"]),
                                "p99p9": get_nested(results, ["metrics", "request_latency", "successful", "percentiles", "p999"]),
                                "max": get_nested(results, ["metrics", "request_latency", "successful", "max"]),
                            },
                        },
                        "throughput": {
                            "output_token_rate": {
                                "units": Units.TOKEN_PER_S,
                                "mean": get_nested(results, ["metrics", "output_tokens_per_second", "successful", "mean"]),
                                "mode": get_nested(results, ["metrics", "output_tokens_per_second", "successful", "mode"]),
                                "stddev": get_nested(results, ["metrics", "output_tokens_per_second", "successful", "std_dev"]),
                                "min": get_nested(results, ["metrics", "output_tokens_per_second", "successful", "min"]),
                                "p0p1": get_nested(results, ["metrics", "output_tokens_per_second", "successful", "percentiles", "p001"]),
                                "p1": get_nested(results, ["metrics", "output_tokens_per_second", "successful", "percentiles", "p01"]),
                                "p5": get_nested(results, ["metrics", "output_tokens_per_second", "successful", "percentiles", "p05"]),
                                "p10": get_nested(results, ["metrics", "output_tokens_per_second", "successful", "percentiles", "p10"]),
                                "p25": get_nested(results, ["metrics", "output_tokens_per_second", "successful", "percentiles", "p25"]),
                                "p50": get_nested(results, ["metrics", "output_tokens_per_second", "successful", "percentiles", "p50"]),
                                "p75": get_nested(results, ["metrics", "output_tokens_per_second", "successful", "percentiles", "p75"]),
                                "p90": get_nested(results, ["metrics", "output_tokens_per_second", "successful", "percentiles", "p90"]),
                                "p95": get_nested(results, ["metrics", "output_tokens_per_second", "successful", "percentiles", "p95"]),
                                "p99": get_nested(results, ["metrics", "output_tokens_per_second", "successful", "percentiles", "p99"]),
                                "p99p9": get_nested(results, ["metrics", "output_tokens_per_second", "successful", "percentiles", "p999"]),
                                "max": get_nested(results, ["metrics", "output_tokens_per_second", "successful", "max"]),
                            },
                            "total_token_rate": {
                                "units": Units.TOKEN_PER_S,
                                "mean": get_nested(results, ["metrics", "tokens_per_second", "successful", "mean"]),
                                "mode": get_nested(results, ["metrics", "tokens_per_second", "successful", "mode"]),
                                "stddev": get_nested(results, ["metrics", "tokens_per_second", "successful", "std_dev"]),
                                "min": get_nested(results, ["metrics", "tokens_per_second", "successful", "min"]),
                                "p0p1": get_nested(results, ["metrics", "tokens_per_second", "successful", "percentiles", "p001"]),
                                "p1": get_nested(results, ["metrics", "tokens_per_second", "successful", "percentiles", "p01"]),
                                "p5": get_nested(results, ["metrics", "tokens_per_second", "successful", "percentiles", "p05"]),
                                "p10": get_nested(results, ["metrics", "tokens_per_second", "successful", "percentiles", "p10"]),
                                "p25": get_nested(results, ["metrics", "tokens_per_second", "successful", "percentiles", "p25"]),
                                "p50": get_nested(results, ["metrics", "tokens_per_second", "successful", "percentiles", "p50"]),
                                "p75": get_nested(results, ["metrics", "tokens_per_second", "successful", "percentiles", "p75"]),
                                "p90": get_nested(results, ["metrics", "tokens_per_second", "successful", "percentiles", "p90"]),
                                "p95": get_nested(results, ["metrics", "tokens_per_second", "successful", "percentiles", "p95"]),
                                "p99": get_nested(results, ["metrics", "tokens_per_second", "successful", "percentiles", "p99"]),
                                "p99p9": get_nested(results, ["metrics", "tokens_per_second", "successful", "percentiles", "p999"]),
                                "max": get_nested(results, ["metrics", "tokens_per_second", "successful", "max"]),
                            },
                            "request_rate": {
                                "units": Units.QUERY_PER_S,
                                "mean": get_nested(results, ["metrics", "requests_per_second", "successful", "mean"]),
                                "mode": get_nested(results, ["metrics", "requests_per_second", "successful", "mode"]),
                                "stddev": get_nested(results, ["metrics", "requests_per_second", "successful", "std_dev"]),
                                "min": get_nested(results, ["metrics", "requests_per_second", "successful", "min"]),
                                "p0p1": get_nested(results, ["metrics", "requests_per_second", "successful", "percentiles", "p001"]),
                                "p1": get_nested(results, ["metrics", "requests_per_second", "successful", "percentiles", "p01"]),
                                "p5": get_nested(results, ["metrics", "requests_per_second", "successful", "percentiles", "p05"]),
                                "p10": get_nested(results, ["metrics", "requests_per_second", "successful", "percentiles", "p10"]),
                                "p25": get_nested(results, ["metrics", "requests_per_second", "successful", "percentiles", "p25"]),
                                "p50": get_nested(results, ["metrics", "requests_per_second", "successful", "percentiles", "p50"]),
                                "p75": get_nested(results, ["metrics", "requests_per_second", "successful", "percentiles", "p75"]),
                                "p90": get_nested(results, ["metrics", "requests_per_second", "successful", "percentiles", "p90"]),
                                "p95": get_nested(results, ["metrics", "requests_per_second", "successful", "percentiles", "p95"]),
                                "p99": get_nested(results, ["metrics", "requests_per_second", "successful", "percentiles", "p99"]),
                                "p99p9": get_nested(results, ["metrics", "requests_per_second", "successful", "percentiles", "p999"]),
                                "max": get_nested(results, ["metrics", "requests_per_second", "successful", "max"]),
                            },
                        },
                    },
                },
            },
        },
    )

    return load_benchmark_report(br_dict)


def _get_num_guidellm_runs(results_file: str) -> int:
    """Get the number of benchmark runs in a GuideLLM results JSON file.

    Args:
        results_file (str): Results file to get number of runs from.

    Returns:
        int: Number of runs.
    """
    check_file(results_file)

    results = import_yaml(results_file)
    return len(results["benchmarks"])


def import_guidellm_all(results_file: str) -> list[BenchmarkReportV02]:
    """Import all data from a GuideLLM results JSON as BenchmarkReport.

    Args:
        results_file (str): Results file to import.

    Returns:
        list[BenchmarkReportV02]: Imported data.
    """
    reports = []
    for index in range(_get_num_guidellm_runs(results_file)):
        reports.append(import_guidellm(results_file, index))
    return reports
