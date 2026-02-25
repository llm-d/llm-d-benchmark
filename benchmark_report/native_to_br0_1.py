"""
Convert application native output formats into a Benchmark Report.
"""

import base64
import datetime
import os
import re
import sys
from typing import Any
import yaml

import numpy as np

from .base import Units
from .core import (
    check_file,
    get_nested,
    import_yaml,
    load_benchmark_report,
    update_dict,
)
from .schema_v0_1 import BenchmarkReportV01, HostType, WorkloadGenerator


def _get_llmd_benchmark_envars() -> dict:
    """Get information from environment variables for the benchmark report.

    Returns:
        dict: Imported data about scenario following schema of BenchmarkReportV01.
    """
    # We make the assumption that if the environment variable
    # LLMDBENCH_MAGIC_ENVAR is defined, then we are inside a harness pod.
    if "LLMDBENCH_MAGIC_ENVAR" not in os.environ:
        # We are not in a harness pod
        return {}

    if "LLMDBENCH_DEPLOY_METHODS" not in os.environ:
        sys.stderr.write(
            "Warning: LLMDBENCH_DEPLOY_METHODS undefined, cannot determine deployment method."
        )
        return {}

    if os.environ["LLMDBENCH_DEPLOY_METHODS"] == "standalone":
        # Given a 'standalone' deployment, we expect the following environment
        # variables to be available
        return {
            "scenario": {
                "model": {"name": os.environ["LLMDBENCH_DEPLOY_CURRENT_MODEL"]},
                "host": {
                    "type": ["replica"]
                    * int(os.environ["LLMDBENCH_VLLM_COMMON_REPLICAS"]),
                    "accelerator": [
                        {
                            "model": os.environ["LLMDBENCH_VLLM_COMMON_AFFINITY"].split(
                                ":", 1
                            )[-1],
                            "count": int(
                                os.environ["LLMDBENCH_VLLM_COMMON_TENSOR_PARALLELISM"]
                            )
                            * int(os.environ["LLMDBENCH_VLLM_COMMON_DATA_PARALLELISM"]),
                            "parallelism": {
                                "tp": int(
                                    os.environ[
                                        "LLMDBENCH_VLLM_COMMON_TENSOR_PARALLELISM"
                                    ]
                                ),
                                "dp": int(
                                    os.environ["LLMDBENCH_VLLM_COMMON_DATA_PARALLELISM"]
                                ),
                            },
                        }
                    ]
                    * int(os.environ["LLMDBENCH_VLLM_COMMON_REPLICAS"]),
                },
                "platform": {
                    "engine": [
                        {
                            "name": os.environ[
                                "LLMDBENCH_VLLM_STANDALONE_IMAGE_REGISTRY"
                            ]
                            + "/"
                            + os.environ["LLMDBENCH_VLLM_STANDALONE_IMAGE_REPO"]
                            + "/"
                            + os.environ["LLMDBENCH_VLLM_STANDALONE_IMAGE_NAME"]
                            + ":"
                            + os.environ["LLMDBENCH_VLLM_STANDALONE_IMAGE_TAG"],
                        }
                    ]
                    * int(os.environ["LLMDBENCH_VLLM_COMMON_REPLICAS"])
                },
                "load": {
                    "metadata": {
                        "load_parallel": os.environ[
                            "LLMDBENCH_HARNESS_LOAD_PARALLELISM"
                        ],
                    },
                },
                "metadata": {
                    "load_format": os.environ["LLMDBENCH_VLLM_COMMON_VLLM_LOAD_FORMAT"],
                    "logging_level": os.environ[
                        "LLMDBENCH_VLLM_COMMON_VLLM_LOGGING_LEVEL"
                    ],
                    "vllm_server_dev_mode": os.environ[
                        "LLMDBENCH_VLLM_COMMON_VLLM_SERVER_DEV_MODE"
                    ],
                    "preprocess": os.environ["LLMDBENCH_VLLM_STANDALONE_PREPROCESS"],
                },
            },
            "metadata": {
                "eid": os.environ["LLMDBENCH_RUN_EXPERIMENT_ID"],
            },
        }

    if os.environ["LLMDBENCH_DEPLOY_METHODS"] == "modelservice":
        # Given a 'modelservice' deployment, we expect the following environment
        # variables to be available

        # Get EPP configuration
        epp_config = {}
        epp_config_content = os.getenv(
            "LLMDBENCH_VLLM_MODELSERVICE_GAIE_PRESETS_CONFIG", ""
        )
        if epp_config_content == "":
            sys.stderr.write(
                "Warning: LLMDBENCH_VLLM_MODELSERVICE_GAIE_PRESETS_CONFIG empty."
            )
        else:
            epp_config_content = base64.b64decode(epp_config_content).decode("utf-8")
            epp_config = yaml.safe_load(epp_config_content)

            # Insert default parameter values for scorers if left undefined
            for ii, plugin in enumerate(epp_config["plugins"]):
                if plugin["type"] == "prefix-cache-scorer":
                    if "parameters" not in plugin:
                        plugin["parameters"] = {}

                    parameters = plugin["parameters"]
                    if "blockSize" not in parameters:
                        parameters["blockSize"] = 16
                    if "maxPrefixBlocksToMatch" not in parameters:
                        parameters["maxPrefixBlocksToMatch"] = 256
                    if "lruCapacityPerServer" not in parameters:
                        parameters["lruCapacityPerServer"] = 31250

                    epp_config["plugins"][ii]["parameters"] = parameters

        return {
            "scenario": {
                "model": {"name": os.environ["LLMDBENCH_DEPLOY_CURRENT_MODEL"]},
                "host": {
                    "type": ["prefill"]
                    * int(os.environ["LLMDBENCH_VLLM_MODELSERVICE_PREFILL_REPLICAS"])
                    + ["decode"]
                    * int(os.environ["LLMDBENCH_VLLM_MODELSERVICE_DECODE_REPLICAS"]),
                    "accelerator": [
                        {
                            "model": os.environ["LLMDBENCH_VLLM_COMMON_AFFINITY"].split(
                                ":", 1
                            )[-1],
                            "count": int(
                                os.environ[
                                    "LLMDBENCH_VLLM_MODELSERVICE_PREFILL_TENSOR_PARALLELISM"
                                ]
                            )
                            * int(
                                os.environ[
                                    "LLMDBENCH_VLLM_MODELSERVICE_PREFILL_DATA_LOCAL_PARALLELISM"
                                ]
                            ),
                            "parallelism": {
                                "tp": int(
                                    os.environ[
                                        "LLMDBENCH_VLLM_MODELSERVICE_PREFILL_TENSOR_PARALLELISM"
                                    ]
                                ),
                                "dp": int(
                                    os.environ[
                                        "LLMDBENCH_VLLM_MODELSERVICE_PREFILL_DATA_PARALLELISM"
                                    ]
                                ),
                                "dpLocal": int(
                                    os.environ[
                                        "LLMDBENCH_VLLM_MODELSERVICE_PREFILL_DATA_LOCAL_PARALLELISM"
                                    ]
                                ),
                                "workers": int(
                                    os.environ[
                                        "LLMDBENCH_VLLM_MODELSERVICE_PREFILL_NUM_WORKERS_PARALLELISM"
                                    ]
                                ),
                            },
                        }
                    ]
                    * int(os.environ["LLMDBENCH_VLLM_MODELSERVICE_PREFILL_REPLICAS"])
                    + [
                        {
                            "model": os.environ["LLMDBENCH_VLLM_COMMON_AFFINITY"].split(
                                ":", 1
                            )[-1],
                            "count": int(
                                os.environ[
                                    "LLMDBENCH_VLLM_MODELSERVICE_DECODE_TENSOR_PARALLELISM"
                                ]
                            )
                            * int(
                                os.environ[
                                    "LLMDBENCH_VLLM_MODELSERVICE_DECODE_DATA_LOCAL_PARALLELISM"
                                ]
                            ),
                            "parallelism": {
                                "tp": int(
                                    os.environ[
                                        "LLMDBENCH_VLLM_MODELSERVICE_DECODE_TENSOR_PARALLELISM"
                                    ]
                                ),
                                "dp": int(
                                    os.environ[
                                        "LLMDBENCH_VLLM_MODELSERVICE_DECODE_DATA_PARALLELISM"
                                    ]
                                ),
                                "dpLocal": int(
                                    os.environ[
                                        "LLMDBENCH_VLLM_MODELSERVICE_DECODE_DATA_LOCAL_PARALLELISM"
                                    ]
                                ),
                                "workers": int(
                                    os.environ[
                                        "LLMDBENCH_VLLM_MODELSERVICE_DECODE_NUM_WORKERS_PARALLELISM"
                                    ]
                                ),
                            },
                        }
                    ]
                    * int(os.environ["LLMDBENCH_VLLM_MODELSERVICE_DECODE_REPLICAS"]),
                },
                "platform": {
                    "metadata": {
                        "inferenceScheduler": epp_config,
                    },
                    "load": {
                        "metadata": {
                            "load_parallel": os.environ[
                                "LLMDBENCH_HARNESS_LOAD_PARALLELISM"
                            ],
                        },
                    },
                    "engine": [
                        {
                            "name": os.environ["LLMDBENCH_LLMD_IMAGE_REGISTRY"]
                            + "/"
                            + os.environ["LLMDBENCH_LLMD_IMAGE_REPO"]
                            + "/"
                            + os.environ["LLMDBENCH_LLMD_IMAGE_NAME"]
                            + ":"
                            + os.environ["LLMDBENCH_LLMD_IMAGE_TAG"],
                        }
                    ]
                    * (
                        int(os.environ["LLMDBENCH_VLLM_MODELSERVICE_PREFILL_REPLICAS"])
                        + int(os.environ["LLMDBENCH_VLLM_MODELSERVICE_DECODE_REPLICAS"])
                    ),
                },
            },
            "metadata": {
                "eid": os.environ["LLMDBENCH_RUN_EXPERIMENT_ID"],
            },
        }

    # Pre-existing deployment, cannot extract details about unknown inference
    # service environment
    sys.stderr.write(
        'Warning: LLMDBENCH_DEPLOY_METHODS is not "modelservice" or'
        ' "standalone", cannot extract environmental details.'
    )
    return {}


def _vllm_timestamp_to_epoch(date_str: str) -> int:
    """Convert timestamp from vLLM benchmark into seconds from Unix epoch.

    This also works with InferenceMAX.
    String format is YYYYMMDD-HHMMSS in UTC.

    Args:
        date_str (str): Timestamp from vLLM benchmark.

    Returns:
        int: Seconds from Unix epoch.
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
    return datetime.datetime(year, month, day, hour, minute, second).timestamp()


def import_vllm_benchmark(results_file: str) -> BenchmarkReportV01:
    """Import data from a vLLM benchmark run as a BenchmarkReportV01.

    Args:
        results_file (str): Results file to import.

    Returns:
        BenchmarkReportV01: Imported data.
    """
    check_file(results_file)

    # Import results file from vLLM benchmark
    results = import_yaml(results_file)

    # Get environment variables from llm-d-benchmark run as a dict following the
    # schema of BenchmarkReportV01
    br_dict = _get_llmd_benchmark_envars()
    # Append to that dict the data from vLLM benchmark.
    update_dict(
        br_dict,
        {
            "version": "0.1",
            "scenario": {
                "model": {"name": results.get("model_id")},
                "load": {
                    "name": WorkloadGenerator.VLLM_BENCHMARK,
                    "args": {
                        "num_prompts": results.get("num_prompts"),
                        "request_rate": results.get("request_rate"),
                        "burstiness": results.get("burstiness"),
                        "max_concurrency": results.get("max_concurrency"),
                    },
                },
            },
            "metrics": {
                "time": {
                    "duration": results.get("duration"),
                    "start": _vllm_timestamp_to_epoch(results.get("date", "")),
                },
                "requests": {
                    "total": results.get("completed"),
                    "input_length": {
                        "units": Units.COUNT,
                        "mean": results.get("total_input_tokens", 0)
                        / (results.get("completed", 0) or 1),
                    },
                    "output_length": {
                        "units": Units.COUNT,
                        "mean": results.get("total_output_tokens", 0)
                        / (results.get("completed", 0) or 1),
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
                        "p50": results.get("median_itl_ms"),
                        "p75": results.get("p75_itl_ms"),
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
                        "p50": results.get("median_e2el_ms"),
                        "p75": results.get("p75_e2el_ms"),
                        "p90": results.get("p90_e2el_ms"),
                        "p95": results.get("p95_e2el_ms"),
                        "p99": results.get("p99_e2el_ms"),
                        "p99p9": results.get("p99.9_e2el_ms"),
                    },
                },
                "throughput": {
                    "output_tokens_per_sec": results.get("output_throughput"),
                    "total_tokens_per_sec": results.get("total_token_throughput"),
                    "requests_per_sec": results.get("request_throughput"),
                },
            },
        },
    )

    return load_benchmark_report(br_dict)


def import_guidellm(results_file: str, index: int = 0) -> BenchmarkReportV01:
    """Import data from a GuideLLM run as a BenchmarkReportV01.

    Args:
        results_file (str): Results file to import.
        index (int): Benchmark index to import.

    Returns:
        BenchmarkReportV01: Imported data.
    """
    check_file(results_file)

    data = import_yaml(results_file)

    results: dict[str:Any] = data["benchmarks"][index]

    # Get environment variables from llm-d-benchmark run as a dict following the
    # schema of BenchmarkReportV01
    br_dict = _get_llmd_benchmark_envars()
    # Append to that dict the data from GuideLLM
    update_dict(
        br_dict,
        {
            "version": "0.1",
            "scenario": {
                "model": {"name": data["args"].get("model", "unknown")},
                "load": {
                    "name": WorkloadGenerator.GUIDELLM,
                    "args": data.get("args"),
                    "metadata": {
                        "stage": index,
                    },
                },
            },
            "metrics": {
                "time": {
                    "duration": results.get("duration"),
                    "start": results.get("start_time"),
                    "stop": results.get("end_time"),
                },
                "requests": {
                    "total": get_nested(
                        results, ["metrics", "request_totals", "total"]
                    ),
                    "failures": get_nested(
                        results, ["metrics", "request_totals", "errored"]
                    ),
                    "incomplete": get_nested(
                        results, ["metrics", "request_totals", "incomplete"]
                    ),
                    "input_length": {
                        "units": Units.COUNT,
                        "mean": get_nested(
                            results,
                            ["metrics", "prompt_token_count", "successful", "mean"],
                        ),
                        "mode": get_nested(
                            results,
                            ["metrics", "prompt_token_count", "successful", "mode"],
                        ),
                        "stddev": get_nested(
                            results,
                            ["metrics", "prompt_token_count", "successful", "std_dev"],
                        ),
                        "min": get_nested(
                            results,
                            ["metrics", "prompt_token_count", "successful", "min"],
                        ),
                        "p0p1": get_nested(
                            results,
                            [
                                "metrics",
                                "prompt_token_count",
                                "successful",
                                "percentiles",
                                "p001",
                            ],
                        ),
                        "p1": get_nested(
                            results,
                            [
                                "metrics",
                                "prompt_token_count",
                                "successful",
                                "percentiles",
                                "p01",
                            ],
                        ),
                        "p5": get_nested(
                            results,
                            [
                                "metrics",
                                "prompt_token_count",
                                "successful",
                                "percentiles",
                                "p05",
                            ],
                        ),
                        "p10": get_nested(
                            results,
                            [
                                "metrics",
                                "prompt_token_count",
                                "successful",
                                "percentiles",
                                "p10",
                            ],
                        ),
                        "p25": get_nested(
                            results,
                            [
                                "metrics",
                                "prompt_token_count",
                                "successful",
                                "percentiles",
                                "p25",
                            ],
                        ),
                        "p50": get_nested(
                            results,
                            [
                                "metrics",
                                "prompt_token_count",
                                "successful",
                                "percentiles",
                                "p50",
                            ],
                        ),
                        "p75": get_nested(
                            results,
                            [
                                "metrics",
                                "prompt_token_count",
                                "successful",
                                "percentiles",
                                "p75",
                            ],
                        ),
                        "p90": get_nested(
                            results,
                            [
                                "metrics",
                                "prompt_token_count",
                                "successful",
                                "percentiles",
                                "p90",
                            ],
                        ),
                        "p95": get_nested(
                            results,
                            [
                                "metrics",
                                "prompt_token_count",
                                "successful",
                                "percentiles",
                                "p95",
                            ],
                        ),
                        "p99": get_nested(
                            results,
                            [
                                "metrics",
                                "prompt_token_count",
                                "successful",
                                "percentiles",
                                "p99",
                            ],
                        ),
                        "p99p9": get_nested(
                            results,
                            [
                                "metrics",
                                "prompt_token_count",
                                "successful",
                                "percentiles",
                                "p999",
                            ],
                        ),
                        "max": get_nested(
                            results,
                            ["metrics", "prompt_token_count", "successful", "max"],
                        ),
                    },
                    "output_length": {
                        "units": Units.COUNT,
                        "mean": get_nested(
                            results,
                            ["metrics", "output_token_count", "successful", "mean"],
                        ),
                        "mode": get_nested(
                            results,
                            ["metrics", "output_token_count", "successful", "mode"],
                        ),
                        "stddev": get_nested(
                            results,
                            ["metrics", "output_token_count", "successful", "std_dev"],
                        ),
                        "min": get_nested(
                            results,
                            ["metrics", "output_token_count", "successful", "min"],
                        ),
                        "p0p1": get_nested(
                            results,
                            [
                                "metrics",
                                "output_token_count",
                                "successful",
                                "percentiles",
                                "p001",
                            ],
                        ),
                        "p1": get_nested(
                            results,
                            [
                                "metrics",
                                "output_token_count",
                                "successful",
                                "percentiles",
                                "p01",
                            ],
                        ),
                        "p5": get_nested(
                            results,
                            [
                                "metrics",
                                "output_token_count",
                                "successful",
                                "percentiles",
                                "p05",
                            ],
                        ),
                        "p10": get_nested(
                            results,
                            [
                                "metrics",
                                "output_token_count",
                                "successful",
                                "percentiles",
                                "p10",
                            ],
                        ),
                        "p25": get_nested(
                            results,
                            [
                                "metrics",
                                "output_token_count",
                                "successful",
                                "percentiles",
                                "p25",
                            ],
                        ),
                        "p50": get_nested(
                            results,
                            [
                                "metrics",
                                "output_token_count",
                                "successful",
                                "percentiles",
                                "p50",
                            ],
                        ),
                        "p75": get_nested(
                            results,
                            [
                                "metrics",
                                "output_token_count",
                                "successful",
                                "percentiles",
                                "p75",
                            ],
                        ),
                        "p90": get_nested(
                            results,
                            [
                                "metrics",
                                "output_token_count",
                                "successful",
                                "percentiles",
                                "p90",
                            ],
                        ),
                        "p95": get_nested(
                            results,
                            [
                                "metrics",
                                "output_token_count",
                                "successful",
                                "percentiles",
                                "p95",
                            ],
                        ),
                        "p99": get_nested(
                            results,
                            [
                                "metrics",
                                "output_token_count",
                                "successful",
                                "percentiles",
                                "p99",
                            ],
                        ),
                        "p99p9": get_nested(
                            results,
                            [
                                "metrics",
                                "output_token_count",
                                "successful",
                                "percentiles",
                                "p999",
                            ],
                        ),
                        "max": get_nested(
                            results,
                            ["metrics", "output_token_count", "successful", "max"],
                        ),
                    },
                },
                "latency": {
                    "time_to_first_token": {
                        "units": Units.MS,
                        "mean": get_nested(
                            results,
                            ["metrics", "time_to_first_token_ms", "successful", "mean"],
                        ),
                        "mode": get_nested(
                            results,
                            ["metrics", "time_to_first_token_ms", "successful", "mode"],
                        ),
                        "stddev": get_nested(
                            results,
                            [
                                "metrics",
                                "time_to_first_token_ms",
                                "successful",
                                "std_dev",
                            ],
                        ),
                        "min": get_nested(
                            results,
                            ["metrics", "time_to_first_token_ms", "successful", "min"],
                        ),
                        "p0p1": get_nested(
                            results,
                            [
                                "metrics",
                                "time_to_first_token_ms",
                                "successful",
                                "percentiles",
                                "p001",
                            ],
                        ),
                        "p1": get_nested(
                            results,
                            [
                                "metrics",
                                "time_to_first_token_ms",
                                "successful",
                                "percentiles",
                                "p01",
                            ],
                        ),
                        "p5": get_nested(
                            results,
                            [
                                "metrics",
                                "time_to_first_token_ms",
                                "successful",
                                "percentiles",
                                "p05",
                            ],
                        ),
                        "p10": get_nested(
                            results,
                            [
                                "metrics",
                                "time_to_first_token_ms",
                                "successful",
                                "percentiles",
                                "p10",
                            ],
                        ),
                        "p25": get_nested(
                            results,
                            [
                                "metrics",
                                "time_to_first_token_ms",
                                "successful",
                                "percentiles",
                                "p25",
                            ],
                        ),
                        "p50": get_nested(
                            results,
                            [
                                "metrics",
                                "time_to_first_token_ms",
                                "successful",
                                "percentiles",
                                "p50",
                            ],
                        ),
                        "p75": get_nested(
                            results,
                            [
                                "metrics",
                                "time_to_first_token_ms",
                                "successful",
                                "percentiles",
                                "p75",
                            ],
                        ),
                        "p90": get_nested(
                            results,
                            [
                                "metrics",
                                "time_to_first_token_ms",
                                "successful",
                                "percentiles",
                                "p90",
                            ],
                        ),
                        "p95": get_nested(
                            results,
                            [
                                "metrics",
                                "time_to_first_token_ms",
                                "successful",
                                "percentiles",
                                "p95",
                            ],
                        ),
                        "p99": get_nested(
                            results,
                            [
                                "metrics",
                                "time_to_first_token_ms",
                                "successful",
                                "percentiles",
                                "p99",
                            ],
                        ),
                        "p99p9": get_nested(
                            results,
                            [
                                "metrics",
                                "time_to_first_token_ms",
                                "successful",
                                "percentiles",
                                "p999",
                            ],
                        ),
                        "max": get_nested(
                            results,
                            ["metrics", "time_to_first_token_ms", "successful", "max"],
                        ),
                    },
                    "time_per_output_token": {
                        "units": Units.MS_PER_TOKEN,
                        "mean": get_nested(
                            results,
                            [
                                "metrics",
                                "time_per_output_token_ms",
                                "successful",
                                "mean",
                            ],
                        ),
                        "mode": get_nested(
                            results,
                            [
                                "metrics",
                                "time_per_output_token_ms",
                                "successful",
                                "mode",
                            ],
                        ),
                        "stddev": get_nested(
                            results,
                            [
                                "metrics",
                                "time_per_output_token_ms",
                                "successful",
                                "std_dev",
                            ],
                        ),
                        "min": get_nested(
                            results,
                            [
                                "metrics",
                                "time_per_output_token_ms",
                                "successful",
                                "min",
                            ],
                        ),
                        "p0p1": get_nested(
                            results,
                            [
                                "metrics",
                                "time_per_output_token_ms",
                                "successful",
                                "percentiles",
                                "p001",
                            ],
                        ),
                        "p1": get_nested(
                            results,
                            [
                                "metrics",
                                "time_per_output_token_ms",
                                "successful",
                                "percentiles",
                                "p01",
                            ],
                        ),
                        "p5": get_nested(
                            results,
                            [
                                "metrics",
                                "time_per_output_token_ms",
                                "successful",
                                "percentiles",
                                "p05",
                            ],
                        ),
                        "p10": get_nested(
                            results,
                            [
                                "metrics",
                                "time_per_output_token_ms",
                                "successful",
                                "percentiles",
                                "p10",
                            ],
                        ),
                        "p25": get_nested(
                            results,
                            [
                                "metrics",
                                "time_per_output_token_ms",
                                "successful",
                                "percentiles",
                                "p25",
                            ],
                        ),
                        "p50": get_nested(
                            results,
                            [
                                "metrics",
                                "time_per_output_token_ms",
                                "successful",
                                "percentiles",
                                "p50",
                            ],
                        ),
                        "p75": get_nested(
                            results,
                            [
                                "metrics",
                                "time_per_output_token_ms",
                                "successful",
                                "percentiles",
                                "p75",
                            ],
                        ),
                        "p90": get_nested(
                            results,
                            [
                                "metrics",
                                "time_per_output_token_ms",
                                "successful",
                                "percentiles",
                                "p90",
                            ],
                        ),
                        "p95": get_nested(
                            results,
                            [
                                "metrics",
                                "time_per_output_token_ms",
                                "successful",
                                "percentiles",
                                "p95",
                            ],
                        ),
                        "p99": get_nested(
                            results,
                            [
                                "metrics",
                                "time_per_output_token_ms",
                                "successful",
                                "percentiles",
                                "p99",
                            ],
                        ),
                        "p99p9": get_nested(
                            results,
                            [
                                "metrics",
                                "time_per_output_token_ms",
                                "successful",
                                "percentiles",
                                "p999",
                            ],
                        ),
                        "max": get_nested(
                            results,
                            [
                                "metrics",
                                "time_per_output_token_ms",
                                "successful",
                                "max",
                            ],
                        ),
                    },
                    "inter_token_latency": {
                        "units": Units.MS_PER_TOKEN,
                        "mean": get_nested(
                            results,
                            ["metrics", "inter_token_latency_ms", "successful", "mean"],
                        ),
                        "mode": get_nested(
                            results,
                            ["metrics", "inter_token_latency_ms", "successful", "mode"],
                        ),
                        "stddev": get_nested(
                            results,
                            [
                                "metrics",
                                "inter_token_latency_ms",
                                "successful",
                                "std_dev",
                            ],
                        ),
                        "min": get_nested(
                            results,
                            ["metrics", "inter_token_latency_ms", "successful", "min"],
                        ),
                        "p0p1": get_nested(
                            results,
                            [
                                "metrics",
                                "inter_token_latency_ms",
                                "successful",
                                "percentiles",
                                "p001",
                            ],
                        ),
                        "p1": get_nested(
                            results,
                            [
                                "metrics",
                                "inter_token_latency_ms",
                                "successful",
                                "percentiles",
                                "p01",
                            ],
                        ),
                        "p5": get_nested(
                            results,
                            [
                                "metrics",
                                "inter_token_latency_ms",
                                "successful",
                                "percentiles",
                                "p05",
                            ],
                        ),
                        "p10": get_nested(
                            results,
                            [
                                "metrics",
                                "inter_token_latency_ms",
                                "successful",
                                "percentiles",
                                "p10",
                            ],
                        ),
                        "p25": get_nested(
                            results,
                            [
                                "metrics",
                                "inter_token_latency_ms",
                                "successful",
                                "percentiles",
                                "p25",
                            ],
                        ),
                        "p50": get_nested(
                            results,
                            [
                                "metrics",
                                "inter_token_latency_ms",
                                "successful",
                                "percentiles",
                                "p50",
                            ],
                        ),
                        "p75": get_nested(
                            results,
                            [
                                "metrics",
                                "inter_token_latency_ms",
                                "successful",
                                "percentiles",
                                "p75",
                            ],
                        ),
                        "p90": get_nested(
                            results,
                            [
                                "metrics",
                                "inter_token_latency_ms",
                                "successful",
                                "percentiles",
                                "p90",
                            ],
                        ),
                        "p95": get_nested(
                            results,
                            [
                                "metrics",
                                "inter_token_latency_ms",
                                "successful",
                                "percentiles",
                                "p95",
                            ],
                        ),
                        "p99": get_nested(
                            results,
                            [
                                "metrics",
                                "inter_token_latency_ms",
                                "successful",
                                "percentiles",
                                "p99",
                            ],
                        ),
                        "p99p9": get_nested(
                            results,
                            [
                                "metrics",
                                "inter_token_latency_ms",
                                "successful",
                                "percentiles",
                                "p999",
                            ],
                        ),
                        "max": get_nested(
                            results,
                            ["metrics", "inter_token_latency_ms", "successful", "max"],
                        ),
                    },
                    "request_latency": {
                        "units": Units.MS,
                        "mean": get_nested(
                            results,
                            ["metrics", "request_latency", "successful", "mean"],
                        ),
                        "mode": get_nested(
                            results,
                            ["metrics", "request_latency", "successful", "mode"],
                        ),
                        "stddev": get_nested(
                            results,
                            ["metrics", "request_latency", "successful", "std_dev"],
                        ),
                        "min": get_nested(
                            results, ["metrics", "request_latency", "successful", "min"]
                        ),
                        "p0p1": get_nested(
                            results,
                            [
                                "metrics",
                                "request_latency",
                                "successful",
                                "percentiles",
                                "p001",
                            ],
                        ),
                        "p1": get_nested(
                            results,
                            [
                                "metrics",
                                "request_latency",
                                "successful",
                                "percentiles",
                                "p01",
                            ],
                        ),
                        "p5": get_nested(
                            results,
                            [
                                "metrics",
                                "request_latency",
                                "successful",
                                "percentiles",
                                "p05",
                            ],
                        ),
                        "p10": get_nested(
                            results,
                            [
                                "metrics",
                                "request_latency",
                                "successful",
                                "percentiles",
                                "p10",
                            ],
                        ),
                        "p25": get_nested(
                            results,
                            [
                                "metrics",
                                "request_latency",
                                "successful",
                                "percentiles",
                                "p25",
                            ],
                        ),
                        "p50": get_nested(
                            results,
                            [
                                "metrics",
                                "request_latency",
                                "successful",
                                "percentiles",
                                "p50",
                            ],
                        ),
                        "p75": get_nested(
                            results,
                            [
                                "metrics",
                                "request_latency",
                                "successful",
                                "percentiles",
                                "p75",
                            ],
                        ),
                        "p90": get_nested(
                            results,
                            [
                                "metrics",
                                "request_latency",
                                "successful",
                                "percentiles",
                                "p90",
                            ],
                        ),
                        "p95": get_nested(
                            results,
                            [
                                "metrics",
                                "request_latency",
                                "successful",
                                "percentiles",
                                "p95",
                            ],
                        ),
                        "p99": get_nested(
                            results,
                            [
                                "metrics",
                                "request_latency",
                                "successful",
                                "percentiles",
                                "p99",
                            ],
                        ),
                        "p99p9": get_nested(
                            results,
                            [
                                "metrics",
                                "request_latency",
                                "successful",
                                "percentiles",
                                "p999",
                            ],
                        ),
                        "max": get_nested(
                            results, ["metrics", "request_latency", "successful", "max"]
                        ),
                    },
                },
                "throughput": {
                    "output_tokens_per_sec": get_nested(
                        results,
                        ["metrics", "output_tokens_per_second", "successful", "mean"],
                    ),
                    "total_tokens_per_sec": get_nested(
                        results, ["metrics", "tokens_per_second", "successful", "mean"]
                    ),
                    "requests_per_sec": get_nested(
                        results,
                        ["metrics", "requests_per_second", "successful", "mean"],
                    ),
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


def import_guidellm_all(results_file: str) -> list[BenchmarkReportV01]:
    """Import all data from a GuideLLM results JSON as BenchmarkReportV01.

    Args:
        results_file (str): Results file to import.

    Returns:
        list[BenchmarkReportV01]: Imported data.
    """
    reports = []
    for index in range(_get_num_guidellm_runs(results_file)):
        reports.append(import_guidellm(results_file, index))
    return reports


def import_inference_perf(results_file: str) -> BenchmarkReportV01:
    """Import data from a Inference Perf run as a BenchmarkReportV01.

    Args:
        results_file (str): Results file to import.

    Returns:
        BenchmarkReportV01: Imported data.
    """
    check_file(results_file)

    # Import results from Inference Perf
    results = import_yaml(results_file)

    # Get stage number from metrics filename
    stage = int(results_file.rsplit("stage_")[-1].split("_", 1)[0])

    # Import Inference Perf config file
    config_file = os.path.join(os.path.dirname(results_file), "config.yaml")
    if os.path.isfile(config_file):
        config = import_yaml(config_file)
    else:
        config = {}

    # Get environment variables from llm-d-benchmark run as a dict following the
    # schema of BenchmarkReportV01
    br_dict = _get_llmd_benchmark_envars()
    if br_dict:
        model_name = get_nested(br_dict, ["scenario", "model", "name"])
    else:
        model_name = "unknown"
    # Append to that dict the data from Inference Perf
    update_dict(
        br_dict,
        {
            "version": "0.1",
            "scenario": {
                "model": {"name": model_name},
                "load": {
                    "name": WorkloadGenerator.INFERENCE_PERF,
                    "args": config,
                    "metadata": {
                        "stage": stage,
                    },
                },
            },
            "metrics": {
                "time": {
                    # TODO this isn't exactly what we need, we may need to pull
                    # apart per_request_lifecycle_metrics.json
                    "duration": get_nested(results, ["load_summary", "send_duration"]),
                },
                "requests": {
                    "total": get_nested(results, ["load_summary", "count"]),
                    "failures": get_nested(results, ["failures", "count"]),
                    "input_length": {
                        "units": Units.COUNT,
                        "mean": get_nested(
                            results,
                            ["successes", "prompt_len", "mean"],
                            get_nested(results, ["failures", "prompt_len", "mean"], 0),
                        ),
                        "min": get_nested(results, ["successes", "prompt_len", "min"]),
                        "p0p1": get_nested(
                            results, ["successes", "prompt_len", "p0.1"]
                        ),
                        "p1": get_nested(results, ["successes", "prompt_len", "p1"]),
                        "p5": get_nested(results, ["successes", "prompt_len", "p5"]),
                        "p10": get_nested(results, ["successes", "prompt_len", "p10"]),
                        "p25": get_nested(results, ["successes", "prompt_len", "p25"]),
                        "p50": get_nested(
                            results, ["successes", "prompt_len", "median"]
                        ),
                        "p75": get_nested(results, ["successes", "prompt_len", "p75"]),
                        "p90": get_nested(results, ["successes", "prompt_len", "p90"]),
                        "p95": get_nested(results, ["successes", "prompt_len", "p95"]),
                        "p99": get_nested(results, ["successes", "prompt_len", "p99"]),
                        "p99p9": get_nested(
                            results, ["successes", "prompt_len", "p99.9"]
                        ),
                        "max": get_nested(results, ["successes", "prompt_len", "max"]),
                    },
                    "output_length": {
                        "units": Units.COUNT,
                        "mean": get_nested(
                            results, ["successes", "output_len", "mean"], 0
                        ),
                        "min": get_nested(results, ["successes", "output_len", "min"]),
                        "p0p1": get_nested(
                            results, ["successes", "output_len", "p0.1"]
                        ),
                        "p1": get_nested(results, ["successes", "output_len", "p1"]),
                        "p5": get_nested(results, ["successes", "output_len", "p5"]),
                        "p10": get_nested(results, ["successes", "output_len", "p10"]),
                        "p25": get_nested(results, ["successes", "output_len", "p25"]),
                        "p50": get_nested(
                            results, ["successes", "output_len", "median"]
                        ),
                        "p75": get_nested(results, ["successes", "output_len", "p75"]),
                        "p90": get_nested(results, ["successes", "output_len", "p90"]),
                        "p95": get_nested(results, ["successes", "output_len", "p95"]),
                        "p99": get_nested(results, ["successes", "output_len", "p99"]),
                        "p99p9": get_nested(
                            results, ["successes", "output_len", "p99.9"]
                        ),
                        "max": get_nested(results, ["successes", "output_len", "max"]),
                    },
                },
                "latency": {
                    "time_to_first_token": {
                        "units": Units.S,
                        "mean": get_nested(
                            results,
                            ["successes", "latency", "time_to_first_token", "mean"],
                            0,
                        ),
                        "min": get_nested(
                            results,
                            ["successes", "latency", "time_to_first_token", "min"],
                        ),
                        "p0p1": get_nested(
                            results,
                            ["successes", "latency", "time_to_first_token", "p0.1"],
                        ),
                        "p1": get_nested(
                            results,
                            ["successes", "latency", "time_to_first_token", "p1"],
                        ),
                        "p5": get_nested(
                            results,
                            ["successes", "latency", "time_to_first_token", "p5"],
                        ),
                        "p10": get_nested(
                            results,
                            ["successes", "latency", "time_to_first_token", "p10"],
                        ),
                        "p25": get_nested(
                            results,
                            ["successes", "latency", "time_to_first_token", "p25"],
                        ),
                        "p50": get_nested(
                            results,
                            ["successes", "latency", "time_to_first_token", "median"],
                        ),
                        "p75": get_nested(
                            results,
                            ["successes", "latency", "time_to_first_token", "p75"],
                        ),
                        "p90": get_nested(
                            results,
                            ["successes", "latency", "time_to_first_token", "p90"],
                        ),
                        "p95": get_nested(
                            results,
                            ["successes", "latency", "time_to_first_token", "p95"],
                        ),
                        "p99": get_nested(
                            results,
                            ["successes", "latency", "time_to_first_token", "p99"],
                        ),
                        "p99p9": get_nested(
                            results,
                            ["successes", "latency", "time_to_first_token", "p99.9"],
                        ),
                        "max": get_nested(
                            results,
                            ["successes", "latency", "time_to_first_token", "max"],
                        ),
                    },
                    "normalized_time_per_output_token": {
                        "units": Units.S_PER_TOKEN,
                        "mean": get_nested(
                            results,
                            [
                                "successes",
                                "latency",
                                "normalized_time_per_output_token",
                                "mean",
                            ],
                            0,
                        ),
                        "min": get_nested(
                            results,
                            [
                                "successes",
                                "latency",
                                "normalized_time_per_output_token",
                                "min",
                            ],
                        ),
                        "p0p1": get_nested(
                            results,
                            [
                                "successes",
                                "latency",
                                "normalized_time_per_output_token",
                                "p0.1",
                            ],
                        ),
                        "p1": get_nested(
                            results,
                            [
                                "successes",
                                "latency",
                                "normalized_time_per_output_token",
                                "p1",
                            ],
                        ),
                        "p5": get_nested(
                            results,
                            [
                                "successes",
                                "latency",
                                "normalized_time_per_output_token",
                                "p5",
                            ],
                        ),
                        "p10": get_nested(
                            results,
                            [
                                "successes",
                                "latency",
                                "normalized_time_per_output_token",
                                "p10",
                            ],
                        ),
                        "p25": get_nested(
                            results,
                            [
                                "successes",
                                "latency",
                                "normalized_time_per_output_token",
                                "p25",
                            ],
                        ),
                        "p50": get_nested(
                            results,
                            [
                                "successes",
                                "latency",
                                "normalized_time_per_output_token",
                                "median",
                            ],
                        ),
                        "p75": get_nested(
                            results,
                            [
                                "successes",
                                "latency",
                                "normalized_time_per_output_token",
                                "p75",
                            ],
                        ),
                        "p90": get_nested(
                            results,
                            [
                                "successes",
                                "latency",
                                "normalized_time_per_output_token",
                                "p90",
                            ],
                        ),
                        "p95": get_nested(
                            results,
                            [
                                "successes",
                                "latency",
                                "normalized_time_per_output_token",
                                "p95",
                            ],
                        ),
                        "p99": get_nested(
                            results,
                            [
                                "successes",
                                "latency",
                                "normalized_time_per_output_token",
                                "p99",
                            ],
                        ),
                        "p99p9": get_nested(
                            results,
                            [
                                "successes",
                                "latency",
                                "normalized_time_per_output_token",
                                "p99.9",
                            ],
                        ),
                        "max": get_nested(
                            results,
                            [
                                "successes",
                                "latency",
                                "normalized_time_per_output_token",
                                "max",
                            ],
                        ),
                    },
                    "time_per_output_token": {
                        "units": Units.S_PER_TOKEN,
                        "mean": get_nested(
                            results,
                            ["successes", "latency", "time_per_output_token", "mean"],
                            0,
                        ),
                        "min": get_nested(
                            results,
                            ["successes", "latency", "time_per_output_token", "min"],
                        ),
                        "p0p1": get_nested(
                            results,
                            ["successes", "latency", "time_per_output_token", "p0.1"],
                        ),
                        "p1": get_nested(
                            results,
                            ["successes", "latency", "time_per_output_token", "p1"],
                        ),
                        "p5": get_nested(
                            results,
                            ["successes", "latency", "time_per_output_token", "p5"],
                        ),
                        "p10": get_nested(
                            results,
                            ["successes", "latency", "time_per_output_token", "p10"],
                        ),
                        "p25": get_nested(
                            results,
                            ["successes", "latency", "time_per_output_token", "p25"],
                        ),
                        "p50": get_nested(
                            results,
                            ["successes", "latency", "time_per_output_token", "median"],
                        ),
                        "p75": get_nested(
                            results,
                            ["successes", "latency", "time_per_output_token", "p75"],
                        ),
                        "p90": get_nested(
                            results,
                            ["successes", "latency", "time_per_output_token", "p90"],
                        ),
                        "p95": get_nested(
                            results,
                            ["successes", "latency", "time_per_output_token", "p95"],
                        ),
                        "p99": get_nested(
                            results,
                            ["successes", "latency", "time_per_output_token", "p99"],
                        ),
                        "p99p9": get_nested(
                            results,
                            ["successes", "latency", "time_per_output_token", "p99.9"],
                        ),
                        "max": get_nested(
                            results,
                            ["successes", "latency", "time_per_output_token", "max"],
                        ),
                    },
                    "inter_token_latency": {
                        "units": Units.S_PER_TOKEN,
                        "mean": get_nested(
                            results,
                            ["successes", "latency", "inter_token_latency", "mean"],
                            0,
                        ),
                        "min": get_nested(
                            results,
                            ["successes", "latency", "inter_token_latency", "min"],
                        ),
                        "p0p1": get_nested(
                            results,
                            ["successes", "latency", "inter_token_latency", "p0.1"],
                        ),
                        "p1": get_nested(
                            results,
                            ["successes", "latency", "inter_token_latency", "p1"],
                        ),
                        "p5": get_nested(
                            results,
                            ["successes", "latency", "inter_token_latency", "p5"],
                        ),
                        "p10": get_nested(
                            results,
                            ["successes", "latency", "inter_token_latency", "p10"],
                        ),
                        "p25": get_nested(
                            results,
                            ["successes", "latency", "inter_token_latency", "p25"],
                        ),
                        "p50": get_nested(
                            results,
                            ["successes", "latency", "inter_token_latency", "median"],
                        ),
                        "p75": get_nested(
                            results,
                            ["successes", "latency", "inter_token_latency", "p75"],
                        ),
                        "p90": get_nested(
                            results,
                            ["successes", "latency", "inter_token_latency", "p90"],
                        ),
                        "p95": get_nested(
                            results,
                            ["successes", "latency", "inter_token_latency", "p95"],
                        ),
                        "p99": get_nested(
                            results,
                            ["successes", "latency", "inter_token_latency", "p99"],
                        ),
                        "p99p9": get_nested(
                            results,
                            ["successes", "latency", "inter_token_latency", "p99.9"],
                        ),
                        "max": get_nested(
                            results,
                            ["successes", "latency", "inter_token_latency", "max"],
                        ),
                    },
                    "request_latency": {
                        "units": Units.S,
                        "mean": get_nested(
                            results,
                            ["successes", "latency", "request_latency", "mean"],
                            0,
                        ),
                        "min": get_nested(
                            results, ["successes", "latency", "request_latency", "min"]
                        ),
                        "p0p1": get_nested(
                            results, ["successes", "latency", "request_latency", "p0.1"]
                        ),
                        "p1": get_nested(
                            results, ["successes", "latency", "request_latency", "p1"]
                        ),
                        "p5": get_nested(
                            results, ["successes", "latency", "request_latency", "p5"]
                        ),
                        "p10": get_nested(
                            results, ["successes", "latency", "request_latency", "p10"]
                        ),
                        "p25": get_nested(
                            results, ["successes", "latency", "request_latency", "p25"]
                        ),
                        "p50": get_nested(
                            results,
                            ["successes", "latency", "request_latency", "median"],
                        ),
                        "p75": get_nested(
                            results, ["successes", "latency", "request_latency", "p75"]
                        ),
                        "p90": get_nested(
                            results, ["successes", "latency", "request_latency", "p90"]
                        ),
                        "p95": get_nested(
                            results, ["successes", "latency", "request_latency", "p95"]
                        ),
                        "p99": get_nested(
                            results, ["successes", "latency", "request_latency", "p99"]
                        ),
                        "p99p9": get_nested(
                            results,
                            ["successes", "latency", "request_latency", "p99.9"],
                        ),
                        "max": get_nested(
                            results, ["successes", "latency", "request_latency", "max"]
                        ),
                    },
                },
                "throughput": {
                    "output_tokens_per_sec": get_nested(
                        results, ["successes", "throughput", "output_tokens_per_sec"]
                    ),
                    "total_tokens_per_sec": get_nested(
                        results, ["successes", "throughput", "total_tokens_per_sec"]
                    ),
                    "requests_per_sec": get_nested(
                        results, ["successes", "throughput", "requests_per_sec"]
                    ),
                },
            },
        },
    )

    return load_benchmark_report(br_dict)


def import_inference_max(results_file: str) -> BenchmarkReportV01:
    """Import data from an InferenceMAX benchmark run as a BenchmarkReportV01.

    Args:
        results_file (str): Results file to import.

    Returns:
        BenchmarkReportV01: Imported data.
    """
    check_file(results_file)

    # Import results file from InferenceMAX benchmark
    results = import_yaml(results_file)

    # Get environment variables from llm-d-benchmark run as a dict following the
    # schema of BenchmarkReportV01
    br_dict = _get_llmd_benchmark_envars()
    # Append to that dict the data from InferenceMAX benchmark.
    update_dict(
        br_dict,
        {
            "version": "0.1",
            "scenario": {
                "model": {"name": results.get("model_id")},
                "load": {
                    "name": WorkloadGenerator.INFERENCE_MAX,
                    "args": {
                        "num_prompts": results.get("num_prompts"),
                        "request_rate": results.get("request_rate"),
                        "burstiness": results.get("burstiness"),
                        "max_concurrency": results.get("max_concurrency"),
                    },
                },
            },
            "metrics": {
                "time": {
                    "duration": results.get("duration"),
                    "start": _vllm_timestamp_to_epoch(results.get("date", "")),
                },
                "requests": {
                    "total": results.get("completed"),
                    "input_length": {
                        "units": Units.COUNT,
                        "mean": np.array(results.get("input_lens", [0])).mean(),
                    },
                    "output_length": {
                        "units": Units.COUNT,
                        "mean": np.array(results.get("output_lens", [0])).mean(),
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
                    "output_tokens_per_sec": results.get("output_throughput"),
                    "total_tokens_per_sec": results.get("total_token_throughput"),
                    "requests_per_sec": results.get("request_throughput"),
                },
            },
        },
    )

    return load_benchmark_report(br_dict)


def import_nop(results_file: str) -> BenchmarkReportV01:
    """Import data from a nop run as a BenchmarkReportV01.

    Args:
        results_file (str): Results file to import.

    Returns:
        BenchmarkReportV01: Imported data.
    """
    check_file(results_file)

    results = import_yaml(results_file)

    def _import_categories(cat_list: list[dict[str, Any]]) -> list[dict[str, Any]]:
        new_cat_list = []
        for cat in cat_list:
            cat_dict = {}
            cat_dict["title"] = cat["title"]
            process = cat.get("process")
            if process is not None:
                cat_dict["process"] = process["name"]
            cat_dict["elapsed"] = {
                "units": Units.S,
                "value": cat["elapsed"],
            }
            categories = cat.get("categories")
            if categories is not None:
                cat_dict["categories"] = _import_categories(categories)

            new_cat_list.append(cat_dict)

        return new_cat_list

    # Get environment variables from llm-d-benchmark run as a dict following the
    # schema of BenchmarkReportV01
    br_dict = _get_llmd_benchmark_envars()

    results_dict = {
        "version": "0.1",
        "scenario": {
            "model": {"name": results["scenario"]["model"]["name"]},
            "load": {
                "name": WorkloadGenerator.NOP,
            },
            "platform": {"engine": results["scenario"]["platform"]["engines"]},
            "host": {
                "accelerator": [],
                "type": [],
            },
            "metadata": {
                "load_format": results["scenario"]["load_format"],
                "sleep_mode": results["scenario"]["sleep_mode"],
                "gpus": results["scenario"]["gpus"],
            },
        },
        "metrics": {
            "time": {
                "duration": results["time"]["duration"],
                "start": results["time"]["start"],
                "stop": results["time"]["stop"],
            },
            "metadata": [],
            "requests": {
                "total": 0,
                "failures": 0,
                "input_length": {
                    "units": Units.COUNT,
                    "mean": 0,
                    "min": 0,
                    "p10": 0,
                    "p50": 0,
                    "p90": 0,
                    "max": 0,
                },
                "output_length": {
                    "units": Units.COUNT,
                    "mean": 0,
                    "min": 0,
                    "p10": 0,
                    "p50": 0,
                    "p90": 0,
                    "max": 0,
                },
            },
            "latency": {
                "time_to_first_token": {
                    "units": Units.MS,
                    "mean": 0,
                    "min": 0,
                    "p10": 0,
                    "p50": 0,
                    "p90": 0,
                    "max": 0,
                },
                "normalized_time_per_output_token": {
                    "units": Units.MS_PER_TOKEN,
                    "mean": 0,
                    "min": 0,
                    "p10": 0,
                    "p50": 0,
                    "p90": 0,
                    "max": 0,
                },
                "time_per_output_token": {
                    "units": Units.MS_PER_TOKEN,
                    "mean": 0,
                    "min": 0,
                    "p10": 0,
                    "p50": 0,
                    "p90": 0,
                    "max": 0,
                },
                "inter_token_latency": {
                    "units": Units.MS_PER_TOKEN,
                    "mean": 0,
                    "min": 0,
                    "p10": 0,
                    "p50": 0,
                    "p90": 0,
                    "max": 0,
                },
                "request_latency": {
                    "units": Units.MS,
                    "mean": 0,
                    "min": 0,
                    "p10": 0,
                    "p50": 0,
                    "p90": 0,
                    "max": 0,
                },
            },
            "throughput": {
                "output_tokens_per_sec": 0,
                "total_tokens_per_sec": 0,
                "requests_per_sec": 0,
            },
        },
    }

    for _ in range(len(results["scenario"]["platform"]["engines"])):
        results_dict["scenario"]["host"]["accelerator"].append(
            {
                "count": 1,
                "model": "auto",
            }
        )
        results_dict["scenario"]["host"]["type"].append(HostType.REPLICA)

    for metrics in results["metrics"]:
        categories = _import_categories(metrics["categories"])
        metadata_dict = {
            "name": metrics["name"],
            "load": {
                "time": {
                    "units": Units.S,
                    "value": metrics["load"]["time"],
                },
                "size": {
                    "units": Units.GIB,
                    "value": metrics["load"]["size"],
                },
                "transfer_rate": {
                    "units": Units.GIB_PER_S,
                    "value": metrics["load"]["transfer_rate"],
                },
            },
            "dynamo_bytecode_transform": {
                "units": Units.S,
                "value": metrics["dynamo_bytecode_transform"],
            },
            "torch_compile": {
                "units": Units.S,
                "value": metrics["torch_compile"],
            },
            "memory_profiling": {
                "initial_free": {
                    "units": Units.GIB,
                    "value": metrics["memory_profiling"]["initial_free"],
                },
                "after_free": {
                    "units": Units.GIB,
                    "value": metrics["memory_profiling"]["after_free"],
                },
                "time": {
                    "units": Units.S,
                    "value": metrics["memory_profiling"]["time"],
                },
            },
            "sleep": {
                "time": {
                    "units": Units.S,
                    "value": metrics["sleep"]["time"],
                },
                "gpu_freed": {
                    "units": Units.GIB,
                    "value": metrics["sleep"]["gpu_freed"],
                },
                "gpu_in_use": {
                    "units": Units.GIB,
                    "value": metrics["sleep"]["gpu_in_use"],
                },
            },
            "wake": {
                "units": Units.S,
                "value": metrics["wake"],
            },
            "categories": categories,
        }
        for name in ["load_cached_compiled_graph", "compile_graph"]:
            value = metrics.get(name)
            if value is not None:
                metadata_dict[name] = {
                    "units": Units.S,
                    "value": value,
                }
        results_dict["metrics"]["metadata"].append(metadata_dict)

    update_dict(br_dict, results_dict)

    return load_benchmark_report(br_dict)
