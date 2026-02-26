"""
Convert application native output formats into a Benchmark Report.
"""

import base64
import os
import re
import ssl
import sys
import tempfile
import uuid
import hashlib
import json
import binascii
from typing import Any
from datetime import datetime, timezone

import numpy as np
import yaml

from .base import Units, WorkloadGenerator
from .core import (
    check_file,
    get_nested,
    import_yaml,
    load_benchmark_report,
    update_dict,
)
from .schema_v0_2 import BenchmarkReportV02, Component, Distribution, LoadSource
from .schema_v0_2_components import HostType


def config_hash(config: dict) -> str:
    """Compute a deterministic hash for a configuration dictionary.

    Args:
        config (dict): Configuration data.

    Returns:
        str: Hash of configuration.
    """
    # Convert configuration to a JSON string with consistent ordering
    canonical = json.dumps(config, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def b64_decode_envar(envar: str) -> str:
    """Get base64 encoded contents from an environment variable and decode it.

    Args:
        envar (str): Environment variable to get data from.

    Returns:
        str: Decoded data, if it exists and is properly formatted, otherwise
            return an empty string.
    """
    envar_value = os.environ.get(envar)
    if not envar_value:
        sys.stderr.write(f"Environment variable empty: {envar}\n")
        return ""
    try:
        return base64.b64decode(envar_value).decode("utf-8")
    except binascii.Error:
        sys.stderr.write(f"Malformed base64 data in environment variable: {envar}\n")
        return ""


def get_context_from_envar(envar: str) -> dict:
    """Get Kubernetes context from a base64 encoded environment variable.

    Args:
        envar (str): Environment variable name containing base64 encoded context.

    Returns:
        dict: Kubernetes context as a dictionary, or empty dict if retrieval fails.
    """
    context_yaml = b64_decode_envar(envar)
    if not context_yaml:
        sys.stderr.write(
            f"Failed to get Kubernetes context from environment variable: {envar}\n"
        )
        return {}

    try:
        context_dict = yaml.safe_load(context_yaml)
        return context_dict
    except yaml.YAMLError as e:
        sys.stderr.write(f"Failed to parse Kubernetes context YAML: {e}\n")
        return {}


def get_configmap(
    context_dict: dict, configmap_name: str, namespace: str = None, timeout: int = 5
) -> dict:
    """Get ConfigMap contents using a Kubernetes context dictionary.

    Args:
        context_dict (dict): Kubernetes context as a dictionary.
        configmap_name (str): Name of the ConfigMap to retrieve.
        namespace (str): Namespace of the ConfigMap. If None, try to detect
            from service account or environment variable.
        timeout (int): Timeout in seconds for the API call.

    Returns:
        dict: ConfigMap contents as a dict, or empty dict if retrieval fails.
    """
    if not context_dict:
        sys.stderr.write("Empty context dictionary provided\n")
        return {}

    try:
        from kubernetes import client, config as k8s_config

        # Write context to a temporary file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(context_dict, f)
            kubeconfig_path = f.name

        # Load the Kubernetes config from the temporary file
        k8s_config.load_kube_config(config_file=kubeconfig_path)

        # Create API client
        v1 = client.CoreV1Api()

        # Determine namespace if not provided
        if namespace is None:
            try:
                with open(
                    "/var/run/secrets/kubernetes.io/serviceaccount/namespace",
                    "r",
                    encoding="utf-8",
                ) as ff:
                    namespace = ff.read().strip()
            except FileNotFoundError:
                namespace = os.environ.get("LLMDBENCH_VLLM_COMMON_NAMESPACE", "default")

        # Get the ConfigMap with timeout
        configmap = v1.read_namespaced_config_map(
            name=configmap_name, namespace=namespace, _request_timeout=timeout
        )

        # Convert to dict and return
        return configmap.to_dict()

    except Exception as e:
        sys.stderr.write(f"Failed to retrieve ConfigMap '{configmap_name}': {e}\n")
        return {}


def _populate_run(ev_dict: dict) -> dict:
    """Create a benchmark report with run details from environment variables.

    Args:
        ev_dict (dict): Environment variable values.

    Returns:
        dict: dict with run section of BenchmarkReport.
    """
    # Unique ID for pod
    pid = os.environ.get("POD_UID")
    # Create an experiment ID from the results directory used (includes a timestamp)
    eid = str(uuid.uuid5(uuid.NAMESPACE_URL, ev_dict.get("run_experiment_id", "")))
    # Create cluster ID from the API server certificate
    host = os.environ.get("KUBERNETES_SERVICE_HOST")
    port = int(os.environ.get("KUBERNETES_SERVICE_PORT", 0))
    try:
        cert = ssl.get_server_certificate((host, port), timeout=5)
    except (TimeoutError, OSError):
        # As a failover, just use the service host
        cert = host
    cid = str(uuid.uuid5(uuid.NAMESPACE_DNS, cert))

    # Use the namespace for "user"
    try:
        with open(
            "/var/run/secrets/kubernetes.io/serviceaccount/namespace",
            "r",
            encoding="utf-8",
        ) as ff:
            namespace = ff.read().strip()
    except FileNotFoundError:
        namespace = ev_dict.get("vllm_common_namespace", "")

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
    args_str = os.environ.get("LLMDBENCH_HARNESS_ARGS", "")
    kv_pairs = [kv.strip() for kv in args_str.split("--") if kv.strip()]
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

    # Import config file, if it exists
    config_file = os.environ.get("LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME", "")
    try:
        with open(config_file, "r", encoding="UTF-8") as file:
            config = yaml.safe_load(file)
    except (FileNotFoundError, IsADirectoryError):
        config = None

    br_dict = {
        "scenario": {
            "load": {
                "standardized": {
                    "tool_version": os.environ.get("LLMDBENCH_HARNESS_VERSION", ""),
                    "parallelism": os.environ.get(
                        "LLMDBENCH_HARNESS_LOAD_PARALLELISM", 1
                    ),
                },
                "native": {
                    "args": args,
                    "config": config,
                },
            },
        },
    }
    return br_dict


def _populate_aggregate_stack(ev_dict: dict) -> dict:
    """Create a benchmark report with scenario.stack from environment variables
    for aggregate.

    Args:
        ev_dict (dict): Environment variable values.

    Returns:
        dict: dict with scenario.stack part of of BenchmarkReport.
    """
    model = ev_dict.get("deploy_current_model", "")
    accelerator = ev_dict.get("vllm_common_affinity", "").rsplit(":", 1)[-1]
    replicas = int(ev_dict.get("vllm_common_replicas", 1))
    tp = int(ev_dict.get("vllm_common_tensor_parallelism", 1))
    dp = int(ev_dict.get("vllm_common_data_parallelism", 1))
    dp_local = int(ev_dict.get("vllm_common_data_local_parallelism", 1))
    workers = int(os.environ.get("LLMDBENCH_VLLM_COMMON_NUM_WORKERS_PARALLELISM", 1))
    img_reg = ev_dict.get("vllm_standalone_image_registry", "")
    img_repo = ev_dict.get("vllm_standalone_image_repo", "")
    img_name = ev_dict.get("vllm_standalone_image_name", "")
    img_tag = ev_dict.get("vllm_standalone_image_tag", "")

    cli_args_str = ev_dict.get("vllm_standalone_args")
    # Parse CLI arguments into a dict
    cli_args_dict = {}
    if cli_args_str:
        # Remove line continuations and extra whitespace
        cleaned_cmd = " ".join(cli_args_str.replace("\\\n", " ").split())
        # Split by -- to get individual flags
        parts = [p.strip() for p in cleaned_cmd.split("--") if p.strip()]
        for part in parts:
            # Skip the command itself
            if "vllm serve" in part or part.startswith("python"):
                continue
            # Split flag and value
            if " " in part:
                flag, value = part.split(" ", 1)
                cli_args_dict[flag] = value.strip()
            else:
                # Flag without value
                cli_args_dict[part] = None

    # Parse through environment variables YAML
    envars = {}
    envars_yaml_str = b64_decode_envar("LLMDBENCH_VLLM_COMMON_ENVVARS_TO_YAML")
    if envars_yaml_str:
        envars_list: list[dict[str, Any]] = yaml.safe_load(envars_yaml_str)
        for envar_dict in envars_list:
            value = envar_dict.get("value", envar_dict.get("valueFrom"))
            envars[envar_dict["name"]] = value

    cfg_id = config_hash({"args": cli_args_dict, "envars": envars})

    inference_engine = {
        "metadata": {
            "label": "",  # TODO
            "cfg_id": cfg_id,
        },
        "standardized": {
            "kind": "inference_engine",
            "tool": img_repo,
            "tool_version": f"{img_reg}/{img_repo}/{img_name}:{img_tag}",
            "role": HostType.REPLICA,
            "replicas": replicas,
            "model": {"name": model},
            "accelerator": {
                "model": accelerator,
                "count": tp * dp_local,
                "parallelism": {
                    "tp": tp,
                    "dp": dp,
                    "dp_local": dp_local,
                    "workers": workers,
                },
            },
        },
        "native": {
            "args": cli_args_dict,
            "envars": envars,
        },
    }

    br_dict = {
        "scenario": {
            "stack": [inference_engine],
        },
    }
    return br_dict


def _add_inference_scheduler_component(br_dict: dict, ev_dict: dict) -> None:
    """Add inference scheduler details to scenario.stack section of a dict
    following BenchmarkReport format.

    Args:
        br_dict (dict): Benchmark report dict to amend to.
        ev_dict (dict): Environment variable values.
    """
    epp_config_str = b64_decode_envar("LLMDBENCH_VLLM_MODELSERVICE_GAIE_PRESETS_CONFIG")
    if not epp_config_str:
        return

    epp_config = yaml.safe_load(epp_config_str)
    # Inference scheduler component
    epp = {
        "metadata": {
            "label": "EPP",  # TODO
            "cfg_id": config_hash(epp_config),
        },
        "standardized": {
            "kind": "generic",
            "tool": "request_router",
            "tool_version": "",  # TODO get version somehow
        },
        "native": {
            "config": epp_config,
        },
    }

    stack: list[Component] = br_dict["scenario"]["stack"]
    stack.append(epp)


def _populate_disaggregate_stack(ev_dict: dict) -> dict:
    """Create a benchmark report with scenario.stack from environment variables
    for disaggregate.

    Args:
        ev_dict (dict): Environment variable values.

    Returns:
        dict: dict with scenario.stack part of of BenchmarkReport.
    """

    model = ev_dict.get("deploy_current_model", "")
    accelerator = ev_dict.get("vllm_common_affinity", "").rsplit(":", 1)[-1]
    p_replicas = int(ev_dict.get("vllm_modelservice_prefill_replicas", 0))
    d_replicas = int(ev_dict.get("vllm_modelservice_decode_replicas", 1))
    p_tp = int(ev_dict.get("vllm_modelservice_prefill_tensor_parallelism", 1))
    p_dp = int(ev_dict.get("vllm_modelservice_prefill_data_parallelism", 1))
    p_dp_local = int(ev_dict.get("vllm_modelservice_prefill_data_local_parallelism", 1))
    d_tp = int(ev_dict.get("vllm_modelservice_decode_tensor_parallelism", 1))
    d_dp = int(ev_dict.get("vllm_modelservice_decode_data_parallelism", 1))
    d_dp_local = int(ev_dict.get("vllm_modelservice_decode_data_local_parallelism", 1))
    p_workers = int(ev_dict.get("vllm_modelservice_prefill_num_workers_parallelism", 1))
    d_workers = int(ev_dict.get("vllm_modelservice_decode_num_workers_parallelism", 1))
    img_reg = ev_dict.get("vllm_standalone_image_registry", "")
    img_repo = ev_dict.get("vllm_standalone_image_repo", "")
    img_name = ev_dict.get("vllm_standalone_image_name", "")
    img_tag = ev_dict.get("vllm_standalone_image_tag", "")

    p_cli_args_str = ev_dict.get("vllm_modelservice_prefill_extra_args")
    # Parse prefill CLI arguments into a dict
    p_cli_args_dict = {}
    if p_cli_args_str:
        # Remove line continuations and extra whitespace
        cleaned_cmd = " ".join(p_cli_args_str.replace("\\\n", " ").split())
        # Split by -- to get individual flags
        parts = [p.strip() for p in cleaned_cmd.split("--") if p.strip()]
        for part in parts:
            # Skip the command itself
            if "vllm serve" in part or part.startswith("python"):
                continue
            # Split flag and value
            if " " in part:
                flag, value = part.split(" ", 1)
                p_cli_args_dict[flag] = value.strip()
            else:
                # Flag without value
                p_cli_args_dict[part] = None

    d_cli_args_str = ev_dict.get("vllm_modelservice_decode_extra_args")
    # Parse decode CLI arguments into a dict
    d_cli_args_dict = {}
    if d_cli_args_str:
        # Remove line continuations and extra whitespace
        cleaned_cmd = " ".join(d_cli_args_str.replace("\\\n", " ").split())
        # Split by -- to get individual flags
        parts = [p.strip() for p in cleaned_cmd.split("--") if p.strip()]
        for part in parts:
            # Skip the command itself
            if "vllm serve" in part or part.startswith("python"):
                continue
            # Split flag and value
            if " " in part:
                flag, value = part.split(" ", 1)
                d_cli_args_dict[flag] = value.strip()
            else:
                # Flag without value
                d_cli_args_dict[part] = None

    # Parse through environment variables YAML
    p_envars = {}
    envars_yaml_str = ev_dict.get("vllm_modelservice_decode_envvars_to_yaml")
    if envars_yaml_str:
        envars_list: list[dict[str, Any]] = yaml.safe_load(envars_yaml_str)
        for envar_dict in envars_list:
            value = envar_dict.get("value", envar_dict.get("valueFrom"))
            p_envars[envar_dict["name"]] = value
    d_envars = {}
    envars_yaml_str = ev_dict.get("vllm_modelservice_decode_envvars_to_yaml")
    if envars_yaml_str:
        envars_list: list[dict[str, Any]] = yaml.safe_load(envars_yaml_str)
        for envar_dict in envars_list:
            value = envar_dict.get("value", envar_dict.get("valueFrom"))
            d_envars[envar_dict["name"]] = value

    p_cfg_id = config_hash({"args": p_cli_args_dict, "envars": p_envars})
    d_cfg_id = config_hash({"args": d_cli_args_dict, "envars": d_envars})

    p_inference_engine = {
        "metadata": {
            "label": "",  # TODO
            "cfg_id": p_cfg_id,
        },
        "standardized": {
            "kind": "inference_engine",
            "tool": img_repo,
            "tool_version": f"{img_reg}/{img_repo}/{img_name}:{img_tag}",
            "role": HostType.PREFILL,
            "replicas": p_replicas,
            "model": {"name": model},
            "accelerator": {
                "model": accelerator,
                "count": p_tp * p_dp_local,
                "parallelism": {
                    "tp": p_tp,
                    "dp": p_dp,
                    "dp_local": p_dp_local,
                    "workers": p_workers,
                },
            },
        },
        "native": {
            "args": p_cli_args_dict,
            "envars": p_envars,
        },
    }

    d_inference_engine = {
        "metadata": {
            "label": "",  # TODO
            "cfg_id": d_cfg_id,
        },
        "standardized": {
            "kind": "inference_engine",
            "tool": img_repo,
            "tool_version": f"{img_reg}/{img_repo}/{img_name}:{img_tag}",
            "role": HostType.DECODE,
            "replicas": d_replicas,
            "model": {"name": model},
            "accelerator": {
                "model": accelerator,
                "count": d_tp * d_dp_local,
                "parallelism": {
                    "tp": d_tp,
                    "dp": d_dp,
                    "dp_local": d_dp_local,
                    "workers": d_workers,
                },
            },
        },
        "native": {
            "args": d_cli_args_dict,
            "envars": d_envars,
        },
    }

    stack = (
        [p_inference_engine, d_inference_engine] if p_replicas else [d_inference_engine]
    )

    br_dict = {
        "scenario": {
            "stack": stack,
        },
    }

    # Add inference scheduler component to stack
    _add_inference_scheduler_component(br_dict, ev_dict)
    return br_dict


def _populate_stack(ev_dict: dict) -> dict:
    """Create a benchmark report with scenario.stack from environment variables.

    Args:
        ev_dict (dict): Environment variable values.

    Returns:
        dict: dict with scenario.stack part of of BenchmarkReport.
    """

    if "LLMDBENCH_DEPLOY_METHODS" not in os.environ:
        sys.stderr.write(
            "Warning: LLMDBENCH_DEPLOY_METHODS undefined, cannot determine deployment method\n"
        )
        return {}

    if os.environ.get("LLMDBENCH_DEPLOY_METHODS") == "standalone":
        # This is an aggregate serving setup
        return _populate_aggregate_stack(ev_dict)

    if os.environ.get("LLMDBENCH_DEPLOY_METHODS") == "modelservice":
        # This is a disaggregated serving setup
        return _populate_disaggregate_stack(ev_dict)

    sys.stderr.write(
        f"Warning: Unknown deployment method LLMDBENCH_DEPLOY_METHODS={os.environ.get('LLMDBENCH_DEPLOY_METHODS')}\n"
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
            "uid": str(uuid.uuid4()),  # Initial UID, may be updated
        },
        "scenario": {"load": {"standardized": {"tool_version": ""}}},
    }

    # We make the assumption that if the environment variable
    # LLMDBENCH_MAGIC_ENVAR is defined, then we are inside a harness pod.
    if "LLMDBENCH_MAGIC_ENVAR" not in os.environ:
        # We are not in a harness pod
        return br_dict

    # Get Kubernetes context
    context_dict = get_context_from_envar("LLMDBENCH_BASE64_CONTEXT_CONTENTS")
    # Get configmap with standup parameters
    params_cm = get_configmap(context_dict, "llm-d-benchmark-standup-parameters")

    if params_cm:
        ev_str: str = get_nested(params_cm, ["data", "ev.yaml"])
        ev_dict = yaml.safe_load(ev_str) if ev_str else {}
    else:
        # Could not get parameters from ConfigMap, try /standup/ev.yaml
        try:
            ev_file = "/standup/ev.yaml"
            ev_dict = import_yaml(ev_file)
        except Exception as e:
            sys.stderr.write(f"Failed to retrieve {ev_file}: {e}\n")
            ev_dict = {}

    # Fill in more run details
    update_dict(br_dict, _populate_run(ev_dict))
    # Populate part of scenario.load
    update_dict(br_dict, _populate_load())
    # Populate part of scenario.stack
    update_dict(br_dict, _populate_stack(ev_dict))

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

    cfg_id = config_hash(get_nested(br_dict, ["scenario", "load", "native"]))

    # Get CLI arguments, if available
    args: dict[str, str] = get_nested(
        br_dict, ["scenario", "load", "native", "args"], {}
    )

    ds_name = args.get("dataset-name", "sharegpt")
    source = LoadSource.RANDOM if ds_name == "random" else LoadSource.SAMPLED

    # Calculate ISL, as fallback option
    isl_value = results.get("total_input_tokens", 0) / (
        results.get("completed", 0) or 1
    )
    # Get requested ISL, if it is in arguments from --sonnet-input-len or
    # --random-input-len
    for arg, value in args.items():
        if arg.endswith("input-len"):
            isl_value = int(value)
            break

    isl_dist = (
        Distribution.FIXED if ds_name in ["random", "sonnet"] else Distribution.OTHER
    )

    # See if OSL is in args
    osl_value = None
    for arg, value in args.items():
        if arg.endswith("output-len"):
            try:
                osl_value = int(value)
            except ValueError:
                osl_value = None
            break
    osl = None
    if osl_value and osl_value >= 1:
        osl = {
            "value": osl_value,
            "distribution": Distribution.FIXED,
        }

    # Add to that dict the data from vLLM benchmark.
    update_dict(
        br_dict,
        {
            "run": {"time": {"end": _vllm_timestamp_to_iso(results.get("date"))}},
            "scenario": {
                "load": {
                    "metadata": {
                        "schema_version": "0.0.1",
                        "cfg_id": cfg_id,
                    },
                    "standardized": {
                        "tool": WorkloadGenerator.VLLM_BENCHMARK,
                        "stage": 0,
                        "rate_qps": results.get("request_rate"),
                        "concurrency": results.get("max_concurrency"),
                        "source": source,
                        "input_seq_len": {
                            "distribution": isl_dist,
                            "value": isl_value,
                        },
                        "output_seq_len": osl,
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

    cfg_id = config_hash(get_nested(br_dict, ["scenario", "load", "native"]))

    # Get CLI arguments, if available
    args: dict[str, str] = get_nested(
        br_dict, ["scenario", "load", "native", "args"], {}
    )

    ds_name = args.get("dataset-name", "sharegpt")
    source = LoadSource.RANDOM if ds_name == "random" else LoadSource.SAMPLED

    # Calculate ISL, as fallback option
    isl_value = results.get("total_input_tokens", 0) / (
        results.get("completed", 0) or 1
    )
    # Get requested ISL, if it is in arguments from --sonnet-input-len or
    # --random-input-len
    for arg, value in args.items():
        if arg.endswith("input-len"):
            isl_value = int(value)
            break

    isl_dist = (
        Distribution.FIXED if ds_name in ["random", "sonnet"] else Distribution.OTHER
    )

    # See if OSL is in args
    osl_value = None
    for arg, value in args.items():
        if arg.endswith("output-len"):
            try:
                osl_value = int(value)
            except ValueError:
                osl_value = None
            break
    osl = None
    if osl_value and osl_value >= 1:
        osl = {
            "value": osl_value,
            "distribution": Distribution.FIXED,
        }

    # Add to that dict the data from vLLM benchmark.
    update_dict(
        br_dict,
        {
            "run": {
                "time": {
                    "end": _vllm_timestamp_to_iso(results.get("date")),
                    "duration": f"PT{results.get('duration')}S",
                }
            },
            "scenario": {
                "load": {
                    "metadata": {
                        "schema_version": "0.0.1",
                        "cfg_id": cfg_id,
                    },
                    "standardized": {
                        "tool": WorkloadGenerator.INFERENCE_MAX,
                        "stage": 0,
                        "rate_qps": results.get("request_rate"),
                        "concurrency": results.get("max_concurrency"),
                        "source": source,
                        "input_seq_len": {
                            "distribution": isl_dist,
                            "value": isl_value,
                        },
                        "output_seq_len": osl,
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
    cfg_id = config_hash(config)

    data_type = get_nested(config, ["data", "type"])
    source = LoadSource.UNKNOWN
    prefix = None
    multi_turn = None
    if data_type:
        # The "random" and "shared_prefix" load types sample randomly from the
        # model vocabulary, while others sample from some source of text.
        source = (
            LoadSource.RANDOM
            if data_type in ["random", "shared_prefix"]
            else LoadSource.SAMPLED
        )
        if data_type == "shared_prefix":
            prefix = {
                "prefix_len": {
                    "distribution": Distribution.FIXED,
                    "value": get_nested(
                        config, ["data", "shared_prefix", "system_prompt_len"]
                    ),
                },
                "num_groups": get_nested(
                    config, ["data", "shared_prefix", "num_groups"]
                ),
                "num_users_per_group": get_nested(
                    config, ["data", "shared_prefix", "num_prompts_per_group"]
                ),
                "num_prefixes": 1,
            }
            if get_nested(config, ["data", "shared_prefix", "enable_multi_turn_chat"]):
                multi_turn = {"enabled": True}

    # Add to that dict the data from Inference Perf
    update_dict(
        br_dict,
        {
            "scenario": {
                "load": {
                    "metadata": {
                        "schema_version": "0.0.1",
                        "cfg_id": cfg_id,
                    },
                    "standardized": {
                        "tool": WorkloadGenerator.INFERENCE_PERF,
                        "stage": stage,
                        "rate_qps": get_nested(
                            results, ["load_summary", "requested_rate"]
                        ),
                        "concurrency": get_nested(
                            results, ["load_summary", "concurrency"]
                        ),
                        "source": source,
                        # For ISL and OSL, If br_dict has config file from
                        # _populate_benchmark_report_from_envars, get details
                        # from there, otherwise get what is available from the
                        # results file.
                        "input_seq_len": {
                            "distribution": Distribution.GAUSSIAN,
                            "value": get_nested(
                                config,
                                ["data", "input_distribution", "mean"],
                                get_nested(
                                    results,
                                    ["successes", "prompt_len", "mean"],
                                    get_nested(
                                        results, ["failures", "prompt_len", "mean"]
                                    ),
                                ),
                            ),
                            "std_dev": get_nested(
                                config, ["data", "input_distribution", "std"]
                            ),
                            "min": get_nested(
                                config,
                                ["data", "input_distribution", "min"],
                                get_nested(results, ["successes", "prompt_len", "min"]),
                            ),
                            "max": get_nested(
                                config,
                                ["data", "input_distribution", "max"],
                                get_nested(results, ["successes", "prompt_len", "max"]),
                            ),
                        },
                        "output_seq_len": {
                            "distribution": Distribution.GAUSSIAN,
                            "value": get_nested(
                                config,
                                ["data", "output_distribution", "mean"],
                                get_nested(
                                    results, ["successes", "output_len", "mean"], 0
                                ),
                            ),
                            "std_dev": get_nested(
                                config, ["data", "output_distribution", "std"]
                            ),
                            "min": get_nested(
                                config,
                                ["data", "output_distribution", "min"],
                                get_nested(results, ["successes", "output_len", "min"]),
                            ),
                            "max": get_nested(
                                config,
                                ["data", "output_distribution", "max"],
                                get_nested(results, ["successes", "output_len", "max"]),
                            ),
                        },
                        "prefix": prefix,
                        "multi_turn": multi_turn,
                    },
                    "native": {
                        "config": config,
                    },
                },
            },
        },
    )

    total_reqs = get_nested(results, ["load_summary", "count"])
    failures = get_nested(results, ["failures", "count"])
    if total_reqs == failures:
        aggregate = {
            "requests": {
                "total": total_reqs,
                "failures": failures,
            }
        }
    else:
        aggregate = {
<<<<<<< HEAD
            "requests": {
                "total": total_reqs,
                "failures": failures,
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
=======
                "requests": {
                    "total": total_reqs,
                    "failures": failures,
                    "input_length": {
                        "units": Units.COUNT,
                        "mean": get_nested(
                            results, ["successes", "prompt_len", "mean"]
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
                            results, ["successes", "output_len", "mean"]
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
>>>>>>> 27a53e6 (add pod monitor support and collect metrics data)
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
<<<<<<< HEAD
            },
            "latency": {
                "time_to_first_token": {
                    "units": Units.S,
                    "mean": get_nested(
                        results,
                        [
                            "successes",
                            "latency",
                            "time_to_first_token",
                            "mean",
                        ],
                    ),
                    "min": get_nested(
                        results,
                        [
                            "successes",
                            "latency",
                            "time_to_first_token",
                            "min",
                        ],
                    ),
                    "p0p1": get_nested(
                        results,
                        [
                            "successes",
                            "latency",
                            "time_to_first_token",
                            "p0.1",
                        ],
                    ),
                    "p1": get_nested(
                        results,
                        [
                            "successes",
                            "latency",
                            "time_to_first_token",
                            "p1",
                        ],
                    ),
                    "p5": get_nested(
                        results,
                        [
                            "successes",
                            "latency",
                            "time_to_first_token",
                            "p5",
                        ],
                    ),
                    "p10": get_nested(
                        results,
                        [
                            "successes",
                            "latency",
                            "time_to_first_token",
                            "p10",
                        ],
                    ),
                    "p25": get_nested(
                        results,
                        [
                            "successes",
                            "latency",
                            "time_to_first_token",
                            "p25",
                        ],
                    ),
                    "p50": get_nested(
                        results,
                        [
                            "successes",
                            "latency",
                            "time_to_first_token",
                            "median",
                        ],
                    ),
                    "p75": get_nested(
                        results,
                        [
                            "successes",
                            "latency",
                            "time_to_first_token",
                            "p75",
                        ],
                    ),
                    "p90": get_nested(
                        results,
                        [
                            "successes",
                            "latency",
                            "time_to_first_token",
                            "p90",
                        ],
                    ),
                    "p95": get_nested(
                        results,
                        [
                            "successes",
                            "latency",
                            "time_to_first_token",
                            "p95",
                        ],
                    ),
                    "p99": get_nested(
                        results,
                        [
                            "successes",
                            "latency",
                            "time_to_first_token",
                            "p99",
                        ],
                    ),
                    "p99p9": get_nested(
                        results,
                        [
                            "successes",
                            "latency",
                            "time_to_first_token",
                            "p99.9",
                        ],
                    ),
                    "max": get_nested(
                        results,
                        [
                            "successes",
                            "latency",
                            "time_to_first_token",
                            "max",
                        ],
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
                        [
                            "successes",
                            "latency",
                            "time_per_output_token",
                            "mean",
                        ],
                    ),
                    "min": get_nested(
                        results,
                        [
                            "successes",
                            "latency",
                            "time_per_output_token",
                            "min",
                        ],
                    ),
                    "p0p1": get_nested(
                        results,
                        [
                            "successes",
                            "latency",
                            "time_per_output_token",
                            "p0.1",
                        ],
                    ),
                    "p1": get_nested(
                        results,
                        [
                            "successes",
                            "latency",
                            "time_per_output_token",
                            "p1",
                        ],
                    ),
                    "p5": get_nested(
                        results,
                        [
                            "successes",
                            "latency",
                            "time_per_output_token",
                            "p5",
                        ],
                    ),
                    "p10": get_nested(
                        results,
                        [
                            "successes",
                            "latency",
                            "time_per_output_token",
                            "p10",
                        ],
                    ),
                    "p25": get_nested(
                        results,
                        [
                            "successes",
                            "latency",
                            "time_per_output_token",
                            "p25",
                        ],
                    ),
                    "p50": get_nested(
                        results,
                        [
                            "successes",
                            "latency",
                            "time_per_output_token",
                            "median",
                        ],
                    ),
                    "p75": get_nested(
                        results,
                        [
                            "successes",
                            "latency",
                            "time_per_output_token",
                            "p75",
                        ],
                    ),
                    "p90": get_nested(
                        results,
                        [
                            "successes",
                            "latency",
                            "time_per_output_token",
                            "p90",
                        ],
                    ),
                    "p95": get_nested(
                        results,
                        [
                            "successes",
                            "latency",
                            "time_per_output_token",
                            "p95",
                        ],
                    ),
                    "p99": get_nested(
                        results,
                        [
                            "successes",
                            "latency",
                            "time_per_output_token",
                            "p99",
                        ],
                    ),
                    "p99p9": get_nested(
                        results,
                        [
                            "successes",
                            "latency",
                            "time_per_output_token",
                            "p99.9",
                        ],
                    ),
                    "max": get_nested(
                        results,
                        [
                            "successes",
                            "latency",
                            "time_per_output_token",
                            "max",
                        ],
                    ),
                },
                "inter_token_latency": {
                    "units": Units.S_PER_TOKEN,
                    "mean": get_nested(
                        results,
                        [
                            "successes",
                            "latency",
                            "inter_token_latency",
                            "mean",
                        ],
                    ),
                    "min": get_nested(
                        results,
                        [
                            "successes",
                            "latency",
                            "inter_token_latency",
                            "min",
                        ],
                    ),
                    "p0p1": get_nested(
                        results,
                        [
                            "successes",
                            "latency",
                            "inter_token_latency",
                            "p0.1",
                        ],
                    ),
                    "p1": get_nested(
                        results,
                        [
                            "successes",
                            "latency",
                            "inter_token_latency",
                            "p1",
                        ],
                    ),
                    "p5": get_nested(
                        results,
                        [
                            "successes",
                            "latency",
                            "inter_token_latency",
                            "p5",
                        ],
                    ),
                    "p10": get_nested(
                        results,
                        [
                            "successes",
                            "latency",
                            "inter_token_latency",
                            "p10",
                        ],
                    ),
                    "p25": get_nested(
                        results,
                        [
                            "successes",
                            "latency",
                            "inter_token_latency",
                            "p25",
                        ],
                    ),
                    "p50": get_nested(
                        results,
                        [
                            "successes",
                            "latency",
                            "inter_token_latency",
                            "median",
                        ],
                    ),
                    "p75": get_nested(
                        results,
                        [
                            "successes",
                            "latency",
                            "inter_token_latency",
                            "p75",
                        ],
                    ),
                    "p90": get_nested(
                        results,
                        [
                            "successes",
                            "latency",
                            "inter_token_latency",
                            "p90",
                        ],
                    ),
                    "p95": get_nested(
                        results,
                        [
                            "successes",
                            "latency",
                            "inter_token_latency",
                            "p95",
                        ],
                    ),
                    "p99": get_nested(
                        results,
                        [
                            "successes",
                            "latency",
                            "inter_token_latency",
                            "p99",
                        ],
                    ),
                    "p99p9": get_nested(
                        results,
                        [
                            "successes",
                            "latency",
                            "inter_token_latency",
                            "p99.9",
                        ],
                    ),
                    "max": get_nested(
                        results,
                        [
                            "successes",
                            "latency",
                            "inter_token_latency",
                            "max",
                        ],
                    ),
                },
                "request_latency": {
                    "units": Units.S,
                    "mean": get_nested(
                        results,
                        ["successes", "latency", "request_latency", "mean"],
                    ),
                    "min": get_nested(
                        results,
                        ["successes", "latency", "request_latency", "min"],
                    ),
                    "p0p1": get_nested(
                        results,
                        ["successes", "latency", "request_latency", "p0.1"],
                    ),
                    "p1": get_nested(
                        results,
                        ["successes", "latency", "request_latency", "p1"],
                    ),
                    "p5": get_nested(
                        results,
                        ["successes", "latency", "request_latency", "p5"],
                    ),
                    "p10": get_nested(
                        results,
                        ["successes", "latency", "request_latency", "p10"],
                    ),
                    "p25": get_nested(
                        results,
                        ["successes", "latency", "request_latency", "p25"],
                    ),
                    "p50": get_nested(
                        results,
                        [
                            "successes",
                            "latency",
                            "request_latency",
                            "median",
                        ],
                    ),
                    "p75": get_nested(
                        results,
                        ["successes", "latency", "request_latency", "p75"],
                    ),
                    "p90": get_nested(
                        results,
                        ["successes", "latency", "request_latency", "p90"],
                    ),
                    "p95": get_nested(
                        results,
                        ["successes", "latency", "request_latency", "p95"],
                    ),
                    "p99": get_nested(
                        results,
                        ["successes", "latency", "request_latency", "p99"],
                    ),
                    "p99p9": get_nested(
                        results,
                        [
                            "successes",
                            "latency",
                            "request_latency",
                            "p99.9",
                        ],
                    ),
                    "max": get_nested(
                        results,
                        ["successes", "latency", "request_latency", "max"],
                    ),
                },
            },
            "throughput": {
                "output_token_rate": {
                    "units": Units.TOKEN_PER_S,
                    "mean": get_nested(
                        results,
                        [
                            "successes",
                            "throughput",
                            "output_tokens_per_sec",
                        ],
                    ),
                },
                "total_token_rate": {
                    "units": Units.TOKEN_PER_S,
                    "mean": get_nested(
                        results,
                        ["successes", "throughput", "total_tokens_per_sec"],
                    ),
                },
                "request_rate": {
                    "units": Units.QUERY_PER_S,
                    "mean": get_nested(
                        results,
                        ["successes", "throughput", "requests_per_sec"],
                    ),
                },
            },
        }
=======
            }
>>>>>>> 27a53e6 (add pod monitor support and collect metrics data)

    update_dict(
        br_dict,
        {
            "results": {
                "request_performance": {"aggregate": aggregate},
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

    native = get_nested(br_dict, ["scenario", "load", "native"])
    # If config file was loaded, use that, otherwise extract args from results file
    if not native.get("config"):
        native["config"] = data["args"]
    cfg_id = config_hash(native)

    input_args_list = get_nested(data, ["args", "data"])
    if len(input_args_list) > 1:
        sys.stderr.write(
            "WARNING: Multiple data sources not supported in conversion, will"
            " only record first source\n"
        )
    # Deserialize input arguments
    input_args = yaml.safe_load(input_args_list[0])

    isl = {
        "value": input_args.get("prompt_tokens"),
        "std_dev": input_args.get("prompt_tokens_stdev"),
        "min": input_args.get("prompt_tokens_min"),
        "max": input_args.get("prompt_tokens_max"),
    }
    if isl.get("std_dev"):
        isl["distribution"] = Distribution.GAUSSIAN
    else:
        if isl.get("min"):
            isl["distribution"] = Distribution.UNIFORM
        else:
            isl["distribution"] = Distribution.FIXED

    osl = {
        "value": input_args.get("output_tokens"),
        "std_dev": input_args.get("output_tokens_stdev"),
        "min": input_args.get("output_tokens_min"),
        "max": input_args.get("output_tokens_max"),
    }
    if osl.get("std_dev"):
        osl["distribution"] = Distribution.GAUSSIAN
    else:
        if osl.get("min"):
            osl["distribution"] = Distribution.UNIFORM
        else:
            osl["distribution"] = Distribution.FIXED

    if "source" in input_args:
        source = LoadSource.SAMPLED
    else:
        source = LoadSource.RANDOM

    profile = get_nested(data, ["args", "profile"])

    rate_qps = None
    concurrency = None
    if profile in ["async", "constant", "poisson"]:
        rate_qps = get_nested(data, ["args", "rate"])[index]
    elif profile in ["concurrent", "throughput"]:
        concurrency = int(get_nested(data, ["args", "rate"])[index])

    prefix = None
    if "prefix_tokens" in input_args:
        prefix = {
            "prefix_len": {
                "distribution": Distribution.FIXED,
                "value": input_args.get("prefix_tokens"),
            },
            "num_groups": 1,
            "num_users_per_group": 1,
            "num_prefixes": input_args.get("prefix_count"),
        }
    elif "prefix_buckets" in input_args:
        sys.stderr.write(
            "WARNING: prefix_buckets used, not capturing in standardized"
            " section, as description there is too limited. Utilize native"
            " section to properly capture.\n"
        )

    multi_turn = None

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
                        "cfg_id": cfg_id,
                    },
                    "standardized": {
                        "tool": WorkloadGenerator.GUIDELLM,
                        "stage": index,
                        "rate_qps": rate_qps,
                        "concurrency": concurrency,
                        "source": source,
                        "input_seq_len": isl,
                        "output_seq_len": osl,
                        "prefix": prefix,
                        "multi_turn": multi_turn,
                    },
                    "native": native,
                },
            },
            "results": {
                "request_performance": {
                    "aggregate": {
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
                                    [
                                        "metrics",
                                        "prompt_token_count",
                                        "successful",
                                        "mean",
                                    ],
                                ),
                                "mode": get_nested(
                                    results,
                                    [
                                        "metrics",
                                        "prompt_token_count",
                                        "successful",
                                        "mode",
                                    ],
                                ),
                                "stddev": get_nested(
                                    results,
                                    [
                                        "metrics",
                                        "prompt_token_count",
                                        "successful",
                                        "std_dev",
                                    ],
                                ),
                                "min": get_nested(
                                    results,
                                    [
                                        "metrics",
                                        "prompt_token_count",
                                        "successful",
                                        "min",
                                    ],
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
                                    [
                                        "metrics",
                                        "prompt_token_count",
                                        "successful",
                                        "max",
                                    ],
                                ),
                            },
                            "output_length": {
                                "units": Units.COUNT,
                                "mean": get_nested(
                                    results,
                                    [
                                        "metrics",
                                        "output_token_count",
                                        "successful",
                                        "mean",
                                    ],
                                ),
                                "mode": get_nested(
                                    results,
                                    [
                                        "metrics",
                                        "output_token_count",
                                        "successful",
                                        "mode",
                                    ],
                                ),
                                "stddev": get_nested(
                                    results,
                                    [
                                        "metrics",
                                        "output_token_count",
                                        "successful",
                                        "std_dev",
                                    ],
                                ),
                                "min": get_nested(
                                    results,
                                    [
                                        "metrics",
                                        "output_token_count",
                                        "successful",
                                        "min",
                                    ],
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
                                    [
                                        "metrics",
                                        "output_token_count",
                                        "successful",
                                        "max",
                                    ],
                                ),
                            },
                        },
                        "latency": {
                            "time_to_first_token": {
                                "units": Units.MS,
                                "mean": get_nested(
                                    results,
                                    [
                                        "metrics",
                                        "time_to_first_token_ms",
                                        "successful",
                                        "mean",
                                    ],
                                ),
                                "mode": get_nested(
                                    results,
                                    [
                                        "metrics",
                                        "time_to_first_token_ms",
                                        "successful",
                                        "mode",
                                    ],
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
                                    [
                                        "metrics",
                                        "time_to_first_token_ms",
                                        "successful",
                                        "min",
                                    ],
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
                                    [
                                        "metrics",
                                        "time_to_first_token_ms",
                                        "successful",
                                        "max",
                                    ],
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
                                    [
                                        "metrics",
                                        "inter_token_latency_ms",
                                        "successful",
                                        "mean",
                                    ],
                                ),
                                "mode": get_nested(
                                    results,
                                    [
                                        "metrics",
                                        "inter_token_latency_ms",
                                        "successful",
                                        "mode",
                                    ],
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
                                    [
                                        "metrics",
                                        "inter_token_latency_ms",
                                        "successful",
                                        "min",
                                    ],
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
                                    [
                                        "metrics",
                                        "inter_token_latency_ms",
                                        "successful",
                                        "max",
                                    ],
                                ),
                            },
                            "request_latency": {
                                "units": Units.MS,
                                "mean": get_nested(
                                    results,
                                    [
                                        "metrics",
                                        "request_latency",
                                        "successful",
                                        "mean",
                                    ],
                                ),
                                "mode": get_nested(
                                    results,
                                    [
                                        "metrics",
                                        "request_latency",
                                        "successful",
                                        "mode",
                                    ],
                                ),
                                "stddev": get_nested(
                                    results,
                                    [
                                        "metrics",
                                        "request_latency",
                                        "successful",
                                        "std_dev",
                                    ],
                                ),
                                "min": get_nested(
                                    results,
                                    ["metrics", "request_latency", "successful", "min"],
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
                                    results,
                                    ["metrics", "request_latency", "successful", "max"],
                                ),
                            },
                        },
                        "throughput": {
                            "output_token_rate": {
                                "units": Units.TOKEN_PER_S,
                                "mean": get_nested(
                                    results,
                                    [
                                        "metrics",
                                        "output_tokens_per_second",
                                        "successful",
                                        "mean",
                                    ],
                                ),
                                "mode": get_nested(
                                    results,
                                    [
                                        "metrics",
                                        "output_tokens_per_second",
                                        "successful",
                                        "mode",
                                    ],
                                ),
                                "stddev": get_nested(
                                    results,
                                    [
                                        "metrics",
                                        "output_tokens_per_second",
                                        "successful",
                                        "std_dev",
                                    ],
                                ),
                                "min": get_nested(
                                    results,
                                    [
                                        "metrics",
                                        "output_tokens_per_second",
                                        "successful",
                                        "min",
                                    ],
                                ),
                                "p0p1": get_nested(
                                    results,
                                    [
                                        "metrics",
                                        "output_tokens_per_second",
                                        "successful",
                                        "percentiles",
                                        "p001",
                                    ],
                                ),
                                "p1": get_nested(
                                    results,
                                    [
                                        "metrics",
                                        "output_tokens_per_second",
                                        "successful",
                                        "percentiles",
                                        "p01",
                                    ],
                                ),
                                "p5": get_nested(
                                    results,
                                    [
                                        "metrics",
                                        "output_tokens_per_second",
                                        "successful",
                                        "percentiles",
                                        "p05",
                                    ],
                                ),
                                "p10": get_nested(
                                    results,
                                    [
                                        "metrics",
                                        "output_tokens_per_second",
                                        "successful",
                                        "percentiles",
                                        "p10",
                                    ],
                                ),
                                "p25": get_nested(
                                    results,
                                    [
                                        "metrics",
                                        "output_tokens_per_second",
                                        "successful",
                                        "percentiles",
                                        "p25",
                                    ],
                                ),
                                "p50": get_nested(
                                    results,
                                    [
                                        "metrics",
                                        "output_tokens_per_second",
                                        "successful",
                                        "percentiles",
                                        "p50",
                                    ],
                                ),
                                "p75": get_nested(
                                    results,
                                    [
                                        "metrics",
                                        "output_tokens_per_second",
                                        "successful",
                                        "percentiles",
                                        "p75",
                                    ],
                                ),
                                "p90": get_nested(
                                    results,
                                    [
                                        "metrics",
                                        "output_tokens_per_second",
                                        "successful",
                                        "percentiles",
                                        "p90",
                                    ],
                                ),
                                "p95": get_nested(
                                    results,
                                    [
                                        "metrics",
                                        "output_tokens_per_second",
                                        "successful",
                                        "percentiles",
                                        "p95",
                                    ],
                                ),
                                "p99": get_nested(
                                    results,
                                    [
                                        "metrics",
                                        "output_tokens_per_second",
                                        "successful",
                                        "percentiles",
                                        "p99",
                                    ],
                                ),
                                "p99p9": get_nested(
                                    results,
                                    [
                                        "metrics",
                                        "output_tokens_per_second",
                                        "successful",
                                        "percentiles",
                                        "p999",
                                    ],
                                ),
                                "max": get_nested(
                                    results,
                                    [
                                        "metrics",
                                        "output_tokens_per_second",
                                        "successful",
                                        "max",
                                    ],
                                ),
                            },
                            "total_token_rate": {
                                "units": Units.TOKEN_PER_S,
                                "mean": get_nested(
                                    results,
                                    [
                                        "metrics",
                                        "tokens_per_second",
                                        "successful",
                                        "mean",
                                    ],
                                ),
                                "mode": get_nested(
                                    results,
                                    [
                                        "metrics",
                                        "tokens_per_second",
                                        "successful",
                                        "mode",
                                    ],
                                ),
                                "stddev": get_nested(
                                    results,
                                    [
                                        "metrics",
                                        "tokens_per_second",
                                        "successful",
                                        "std_dev",
                                    ],
                                ),
                                "min": get_nested(
                                    results,
                                    [
                                        "metrics",
                                        "tokens_per_second",
                                        "successful",
                                        "min",
                                    ],
                                ),
                                "p0p1": get_nested(
                                    results,
                                    [
                                        "metrics",
                                        "tokens_per_second",
                                        "successful",
                                        "percentiles",
                                        "p001",
                                    ],
                                ),
                                "p1": get_nested(
                                    results,
                                    [
                                        "metrics",
                                        "tokens_per_second",
                                        "successful",
                                        "percentiles",
                                        "p01",
                                    ],
                                ),
                                "p5": get_nested(
                                    results,
                                    [
                                        "metrics",
                                        "tokens_per_second",
                                        "successful",
                                        "percentiles",
                                        "p05",
                                    ],
                                ),
                                "p10": get_nested(
                                    results,
                                    [
                                        "metrics",
                                        "tokens_per_second",
                                        "successful",
                                        "percentiles",
                                        "p10",
                                    ],
                                ),
                                "p25": get_nested(
                                    results,
                                    [
                                        "metrics",
                                        "tokens_per_second",
                                        "successful",
                                        "percentiles",
                                        "p25",
                                    ],
                                ),
                                "p50": get_nested(
                                    results,
                                    [
                                        "metrics",
                                        "tokens_per_second",
                                        "successful",
                                        "percentiles",
                                        "p50",
                                    ],
                                ),
                                "p75": get_nested(
                                    results,
                                    [
                                        "metrics",
                                        "tokens_per_second",
                                        "successful",
                                        "percentiles",
                                        "p75",
                                    ],
                                ),
                                "p90": get_nested(
                                    results,
                                    [
                                        "metrics",
                                        "tokens_per_second",
                                        "successful",
                                        "percentiles",
                                        "p90",
                                    ],
                                ),
                                "p95": get_nested(
                                    results,
                                    [
                                        "metrics",
                                        "tokens_per_second",
                                        "successful",
                                        "percentiles",
                                        "p95",
                                    ],
                                ),
                                "p99": get_nested(
                                    results,
                                    [
                                        "metrics",
                                        "tokens_per_second",
                                        "successful",
                                        "percentiles",
                                        "p99",
                                    ],
                                ),
                                "p99p9": get_nested(
                                    results,
                                    [
                                        "metrics",
                                        "tokens_per_second",
                                        "successful",
                                        "percentiles",
                                        "p999",
                                    ],
                                ),
                                "max": get_nested(
                                    results,
                                    [
                                        "metrics",
                                        "tokens_per_second",
                                        "successful",
                                        "max",
                                    ],
                                ),
                            },
                            "request_rate": {
                                "units": Units.QUERY_PER_S,
                                "mean": get_nested(
                                    results,
                                    [
                                        "metrics",
                                        "requests_per_second",
                                        "successful",
                                        "mean",
                                    ],
                                ),
                                "mode": get_nested(
                                    results,
                                    [
                                        "metrics",
                                        "requests_per_second",
                                        "successful",
                                        "mode",
                                    ],
                                ),
                                "stddev": get_nested(
                                    results,
                                    [
                                        "metrics",
                                        "requests_per_second",
                                        "successful",
                                        "std_dev",
                                    ],
                                ),
                                "min": get_nested(
                                    results,
                                    [
                                        "metrics",
                                        "requests_per_second",
                                        "successful",
                                        "min",
                                    ],
                                ),
                                "p0p1": get_nested(
                                    results,
                                    [
                                        "metrics",
                                        "requests_per_second",
                                        "successful",
                                        "percentiles",
                                        "p001",
                                    ],
                                ),
                                "p1": get_nested(
                                    results,
                                    [
                                        "metrics",
                                        "requests_per_second",
                                        "successful",
                                        "percentiles",
                                        "p01",
                                    ],
                                ),
                                "p5": get_nested(
                                    results,
                                    [
                                        "metrics",
                                        "requests_per_second",
                                        "successful",
                                        "percentiles",
                                        "p05",
                                    ],
                                ),
                                "p10": get_nested(
                                    results,
                                    [
                                        "metrics",
                                        "requests_per_second",
                                        "successful",
                                        "percentiles",
                                        "p10",
                                    ],
                                ),
                                "p25": get_nested(
                                    results,
                                    [
                                        "metrics",
                                        "requests_per_second",
                                        "successful",
                                        "percentiles",
                                        "p25",
                                    ],
                                ),
                                "p50": get_nested(
                                    results,
                                    [
                                        "metrics",
                                        "requests_per_second",
                                        "successful",
                                        "percentiles",
                                        "p50",
                                    ],
                                ),
                                "p75": get_nested(
                                    results,
                                    [
                                        "metrics",
                                        "requests_per_second",
                                        "successful",
                                        "percentiles",
                                        "p75",
                                    ],
                                ),
                                "p90": get_nested(
                                    results,
                                    [
                                        "metrics",
                                        "requests_per_second",
                                        "successful",
                                        "percentiles",
                                        "p90",
                                    ],
                                ),
                                "p95": get_nested(
                                    results,
                                    [
                                        "metrics",
                                        "requests_per_second",
                                        "successful",
                                        "percentiles",
                                        "p95",
                                    ],
                                ),
                                "p99": get_nested(
                                    results,
                                    [
                                        "metrics",
                                        "requests_per_second",
                                        "successful",
                                        "percentiles",
                                        "p99",
                                    ],
                                ),
                                "p99p9": get_nested(
                                    results,
                                    [
                                        "metrics",
                                        "requests_per_second",
                                        "successful",
                                        "percentiles",
                                        "p999",
                                    ],
                                ),
                                "max": get_nested(
                                    results,
                                    [
                                        "metrics",
                                        "requests_per_second",
                                        "successful",
                                        "max",
                                    ],
                                ),
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
