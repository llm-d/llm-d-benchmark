#!/usr/bin/env python3

"""
Benchmark 'nop' harness
"""

from __future__ import annotations
import ast
from dataclasses import dataclass, field, fields
from datetime import datetime
from enum import StrEnum
import io
import json
import os
import re
import subprocess
import time
import logging
from typing import Any
from urllib.parse import urljoin, urlparse
from pathlib import Path
import requests
import yaml

from kubernetes import client, config

# Configure logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")

REQUEST_TIMEOUT = 60.0  # time (seconds) to wait for request
MAX_VLLM_WAIT = 15.0 * 60.0  # time (seconds) to wait for vllm to respond

DEFINED_CATEGORIES = [
    {
        "title": "Detect Platform",
        "start": "No plugins for group",
        "end": "detected platform",
    },
    {
        "title": "LLM Imports",
        "start": "detected platform",
        "end": "All plugins in this group will be loaded",
    },
    {
        "title": "Add CLI Args",
        "start": "All plugins in this group will be loaded",
        "end": "vLLM API server version",
    },
    {
        "title": "Get Model Info",
        "start": "non-default args:",
        "end": "Using max model len",
    },
    {
        "title": "Worker Initialization",
        "start": "Setting max_num_batched_tokens",
        "end": "Initializing a V1 LLM engine",
    },
    {
        "title": "Model Loading",
        "start": "Starting to load model",
        "end": "Model loading took",
    },
    {
        "title": "Pytorch Compilation",
        "start": "Start compiling function",
        "end": "torch.compile takes",
        "children": [
            {
                "title": "Dynamo",
                "start": "Start compiling function",
                "end": "Dynamo bytecode transform",
            },
            {
                "title": "Inductor",
                "start": "De-functionalized",
                "end": "Compiling a graph",
            },
            {
                "title": "Dynamo Serialization",
                "start": "Computation graph saved",
                "end": "torch.compile takes",
            },
        ],
    },
    {
        "title": "CUDA Graph Capture",
        "start": "Capturing CUDA graph shapes:   0%",
        "end": "Capturing CUDA graph shapes: 100%",
    },
    {
        "title": "API Server Starts",
        "start": "EngineCore waiting for work",
        "end": "Available routes are",
    },
]


@dataclass
class BenchmarkCategory:
    """Benchmark category"""

    title: str = ""
    start_time: datetime | None = None
    end_time: datetime | None = None
    start: str = ""
    end: str = ""
    start_line: str = ""
    start_line_number: int = 0
    end_line: str = ""
    end_line_number: int = 0
    next: BenchmarkCategory | None = None
    parent: BenchmarkCategory | None = None
    root_child: BenchmarkCategory | None = None

    def dump(self) -> list[dict[str, Any]]:
        """Convert LogCategory to list.

        Returns:
            list: Defined fields of LogCategory.
        """
        return BenchmarkCategory._dump(self)

    @staticmethod
    def _dump(benchmark_category: BenchmarkCategory) -> list[dict[str, Any]]:
        categories = []
        category = benchmark_category
        while category is not None:
            dump_dict = {}
            dump_dict["title"] = category.title
            dump_dict["elapsed"] = (
                (category.end_time - category.start_time).total_seconds()
                if category.start_time is not None and category.end_time is not None
                else 0.0
            )

            if category.root_child is not None:
                dump_dict["categories"] = BenchmarkCategory._dump(category.root_child)

            categories.append(dump_dict)
            category = category.next

        return categories


class LoadFormat(StrEnum):
    """Type of model formats"""

    UNKNOWN = "unknown"
    AUTO = "auto"
    PT = "pt"
    SAFETENSORS = "safetensors"
    NPCACHE = "npcache"
    DUMMY = "dummy"
    TENSORIZER = "tensorizer"
    SHARDED_STATE = "sharded_state"
    GGUF = "gguf"
    BITSANDBYTES = "bitsandbytes"
    MISTRAL = "mistral"
    RUNAI_STREAMER = "runai_streamer"
    RUNAI_STREAMER_SHARDED = "runai_streamer_sharded"
    FASTSAFETENSORS = "fastsafetensors"

    def dump(self) -> str:
        """Convert LoadFormat to str.

        Returns:
            str: LoadFormat value.
        """
        return self.value


@dataclass
class ModelScenario:
    """Model Scenario"""

    name: str = ""

    def dump(self) -> dict[str, Any]:
        """Convert ModelScenario to dict.

        Returns:
            dict: Defined fields of ModelScenario.
        """
        dump_dict = {}
        for f in fields(self):
            dump_dict[f.name] = getattr(self, f.name)

        return dump_dict


@dataclass
class PlatformEngineScenario:
    """Platform Engine Scenario"""

    name: str = ""
    version: str = ""
    args: dict[str, Any] = field(default_factory=dict)

    def dump(self) -> dict[str, Any]:
        """Convert PlatformEngineScenario to dict.

        Returns:
            dict: Defined fields of PlatformEngineScenario.
        """
        dump_dict = {}
        for f in fields(self):
            dump_dict[f.name] = getattr(self, f.name)

        return dump_dict


@dataclass
class PlatformScenario:
    """Platform Scenario"""

    engine: PlatformEngineScenario = field(default_factory=PlatformEngineScenario)

    def dump(self) -> dict[str, Any]:
        """Convert PlatformScenario to dict.

        Returns:
            dict: Defined fields of PlatformScenario.
        """
        return {"engine": self.engine.dump()}


@dataclass
class BenchmarkScenario:
    """Benchmark Scenario"""

    load_format: LoadFormat = LoadFormat.UNKNOWN
    sleep_mode: bool = False
    model: ModelScenario = field(default_factory=ModelScenario)
    platform: PlatformScenario = field(default_factory=PlatformScenario)

    def dump(self) -> dict[str, Any]:
        """Convert BenchmarkScenario to dict.

        Returns:
            dict: Defined fields of BenchmarkScenario.
        """
        dump_dict = {}
        for f in fields(self):
            value = getattr(self, f.name)
            dump_dict[f.name] = (
                value.dump()
                if hasattr(value, "dump") and callable(value.dump)
                else value
            )

        return dump_dict


@dataclass
class MetricsTime:
    """Timing details of benchmark run."""

    start: float = 0.0
    """Start time of benchmark run, in seconds from Unix epoch."""
    stop: float = 0.0
    """End time of benchmark run, in seconds from Unix epoch."""

    def dump(self) -> dict[str, Any]:
        """Convert MetricsTime to dict.

        Returns:
            dict: Defined fields of MetricsTime.
        """
        dump_dict = {}
        for f in fields(self):
            value = getattr(self, f.name)
            dump_dict[f.name] = (
                value.dump()
                if hasattr(value, "dump") and callable(value.dump)
                else value
            )
        dump_dict["duration"] = self.stop - self.start
        return dump_dict


@dataclass
class BenchmarkMetrics:
    """Benchmark Metrics"""

    time: MetricsTime = field(default_factory=MetricsTime)
    load_time: float = 0.0
    size: float = 0.0
    sleep: float = 0.0
    gpu_freed: float = 0.0
    gpu_in_use: float = 0.0
    wake: float = 0.0
    root_category: BenchmarkCategory | None = None

    def dump(self) -> dict[str, Any]:
        """Convert BenchmarkMetrics to dict.

        Returns:
            dict: Defined fields of BenchmarkMetrics.
        """
        dump_dict = {}
        for f in fields(self):
            if f.name == "root_category":
                continue

            value = getattr(self, f.name)
            dump_dict[f.name] = (
                value.dump()
                if hasattr(value, "dump") and callable(value.dump)
                else value
            )
        transfer_rate = 0.0
        if self.load_time != 0.0:
            transfer_rate = self.size / self.load_time
        dump_dict["transfer_rate"] = transfer_rate

        if self.root_category is not None:
            dump_dict["categories"] = self.root_category.dump()
        return dump_dict


@dataclass
class BenchmarkResult:
    """Results of one benchmark run"""

    version: str = "0.1"
    scenario: BenchmarkScenario = field(default_factory=BenchmarkScenario)
    metrics: BenchmarkMetrics = field(default_factory=BenchmarkMetrics)

    def dump(self) -> dict[str, Any]:
        """Convert BenchmarkResult to dict.

        Returns:
            dict: Defined fields of BenchmarkResult.
        """
        dump_dict = {}
        for f in fields(self):
            value = getattr(self, f.name)
            dump_dict[f.name] = (
                value.dump()
                if hasattr(value, "dump") and callable(value.dump)
                else value
            )

        return dump_dict


def get_env_variables(keys: list[str]) -> list[str]:
    """get environment variables"""

    logger.info("Environment variables:")

    env_vars = os.environ

    envs = []
    missing_envs = []
    for key in keys:
        value = env_vars.get(key)
        if value is None:
            missing_envs.append(key)
        else:
            envs.append(value)
            logger.info("  '%s': '%s'", key, value)

    if len(missing_envs) > 0:
        raise RuntimeError(f"Env. variables not found: {','.join(missing_envs)}.")
    return envs


def get_vllm_version(base_url: str, timeout: float) -> str:
    """get vLLM version"""

    path = "version"
    url = urljoin(base_url, path)
    response = requests.get(url, timeout=timeout)
    if response.status_code != 200:
        raise RuntimeError(f"server {url} error code {response.status_code}.")

    logger.info("vLLM server version: %s", response.json().get(path))
    return response.json().get(path)


def get_vllm_model(base_url: str, timeout: float) -> str:
    """get vLLM models"""

    path = "/v1/models"
    url = urljoin(base_url, path)
    response = requests.get(url, timeout=timeout)
    if response.status_code != 200:
        raise RuntimeError(f"server {url} error code {response.status_code}.")

    json_contents = response.json()
    logger.info("vLLM server models: %s", json.dumps(json_contents))
    object_type = json_contents.get("object")
    if object_type is not None and object_type == "list":
        data = json_contents.get("data")
        if data is not None and len(data) > 0:
            model_data = data[0]
            model_id = model_data.get("id")
            if model_id is not None:
                return model_id

    return ""


def get_server_status_sleep(base_url: str, timeout: float) -> bool:
    """get server sleep status"""

    path = "is_sleeping"
    url = urljoin(base_url, path)
    response = requests.get(url, timeout=timeout)
    if response.status_code != 200:
        raise RuntimeError(f"server {url} error code {response.status_code}.")

    logger.info("sleep status: %s", response.json().get(path))
    return response.json().get(path)


def sleep(base_url: str, level: int, timeout: float):
    """send sleep request"""

    logger.info("sending sleep level %d request with timeout %.1f ...", level, timeout)
    url = urljoin(base_url, "sleep")
    response = requests.post(url, params={"level": str(level)}, timeout=timeout)
    if response.status_code != 200:
        raise RuntimeError(
            f"sleep level {level} url {url} error code {response.status_code}."
        )

    sleeping = False
    start = time.perf_counter()
    while not sleeping:
        try:
            sleeping = get_server_status_sleep(base_url, timeout)
        except requests.Timeout:
            logger.info(
                "is sleeping check timed out after %.1f  secs. Trying again ...",
                timeout,
            )

        time.sleep(0.5)
        elapsed = time.perf_counter() - start
        if elapsed > MAX_VLLM_WAIT:
            raise RuntimeError(f"Server failed sleeping status after {elapsed} secs.")


def wake(base_url: str, timeout: float):
    """send waek request"""

    logger.info("sending wake_up request with timeout %.1f ...", timeout)
    url = urljoin(base_url, "wake_up")
    response = requests.post(url, timeout=timeout)
    if response.status_code != 200:
        raise RuntimeError(f"wake_up url {url} error code {response.status_code}.")

    sleeping = True
    start = time.perf_counter()
    while sleeping:
        try:
            sleeping = get_server_status_sleep(base_url, timeout)
        except requests.Timeout:
            logger.info(
                "is sleeping check timed out after %.1f  secs. Trying again ...",
                timeout,
            )

        time.sleep(0.5)
        elapsed = time.perf_counter() - start
        if elapsed > MAX_VLLM_WAIT:
            raise RuntimeError(f"Server failed sleeping status after {elapsed} secs.")


def get_vllm_pod_info(
    v1: client.CoreV1Api, namespace: str, deployment_name: str
) -> dict[str, str]:
    """get vllm pod name"""

    selectors = get_deployment_selectors(namespace, deployment_name)
    if len(selectors) == 0:
        raise RuntimeError(
            f"No deployment selectors for deployment {deployment_name} on namespace {namespace}."
        )

    selector = selectors[0]
    pod_infos = get_pod_infos(v1, namespace, selector)
    if len(pod_infos) == 0:
        raise RuntimeError(
            f"No pods found on namespace {namespace} with selector 'app={selector}'."
        )

    return pod_infos[0]


def get_deployment_selectors(namespace: str, name: str) -> list[str]:
    """get deployment label selectors based on prefix"""

    deployment = client.AppsV1Api().read_namespaced_deployment(
        name=name, namespace=namespace
    )
    if deployment is None:
        raise RuntimeError(
            f"No deployment found with name {name} on namespace {namespace}."
        )
    selectors = []
    if deployment.spec.selector and deployment.spec.selector.match_labels:
        dict_selector = deployment.spec.selector.match_labels
        if "app" in dict_selector:
            selectors.append(dict_selector["app"])

    return selectors


def get_pod_infos(
    v1: client.CoreV1Api, namespace: str, selector: str
) -> list[dict[str, str]]:
    """get pods by selector"""

    pod_list = v1.list_namespaced_pod(
        namespace=namespace, label_selector=f"app={selector}"
    )
    pod_infos = []
    for pod in pod_list.items:
        image = pod.spec.containers[0].image
        name = pod.metadata.name
        pod_infos.append({"name": name, "image": image})

    return pod_infos


def get_pod_logs(v1: client.CoreV1Api, namespace: str, pod_name: str) -> bytes:
    """get pod logs"""

    response = v1.read_namespaced_pod_log(
        name=pod_name, namespace=namespace, pretty=False, _preload_content=False
    )
    return response.data


def extract_datetime(log_line: str) -> datetime | None:
    """extracts datetime"""

    # MM-DD HH:MM:SS.MMM
    datetime_pattern = r"\d{2}-\d{2} \d{2}:\d{2}:\d{2}.\d{3}"
    match = re.search(datetime_pattern, log_line)
    if match is None:
        # MM-DD HH:MM:SS
        datetime_pattern = r"\d{2}-\d{2} \d{2}:\d{2}:\d{2}"
        match = re.search(datetime_pattern, log_line)

    if match is None:
        logger.info("Timestamp not found in log line '%s'", log_line)
        return None

    value = match.group()

    # Define the format string that matches the input time string
    time_format = "%m-%d %H:%M:%S.%f" if "." in value else "%m-%d %H:%M:%S"

    try:
        return datetime.strptime(value, time_format)
    except ValueError:
        logger.info(
            "Failed converting time value '%s' using format '%s'",
            value,
            time_format,
        )
        return None


def initialize_benchmark_categories(
    defined_categories: list[Any], parent: BenchmarkCategory
) -> BenchmarkCategory:
    """initialize categories"""
    root_benchmark_category = None
    prev_benchmark_category = None
    for defined_category in defined_categories:
        benchmark_category = BenchmarkCategory()
        if root_benchmark_category is None:
            root_benchmark_category = benchmark_category
        if prev_benchmark_category is not None:
            prev_benchmark_category.next = benchmark_category
        prev_benchmark_category = benchmark_category

        benchmark_category.title = defined_category.get("title")
        benchmark_category.start = defined_category.get("start")
        benchmark_category.end = defined_category.get("end")
        benchmark_category.parent = parent
        if (
            benchmark_category.parent is not None
            and benchmark_category.parent.root_child is None
        ):
            benchmark_category.parent.root_child = benchmark_category

        defined_children = defined_category.get("children")
        if defined_children is not None:
            _ = initialize_benchmark_categories(defined_children, benchmark_category)

    return root_benchmark_category


def categorize_logs(vllm_model: str, logs: str) -> BenchmarkCategory:
    """parse logs and categorize it"""

    root_benchmark_category = initialize_benchmark_categories(DEFINED_CATEGORIES, None)
    populate_benchmark_categories(vllm_model, logs, root_benchmark_category)
    # add uncategorized categories
    add_uncategorized_categories(root_benchmark_category)
    return root_benchmark_category


def populate_benchmark_categories(
    vllm_model: str, logs: str, root_benchmark_category: BenchmarkCategory
):
    """populate categories from log lines"""

    tensorizer_serialization_end = f"End model {vllm_model} serialization"
    # log list
    log_list = logs.splitlines()
    index = 0

    # look for possible tensorizer serialization end
    idx = 0
    while idx < len(log_list):
        if tensorizer_serialization_end in log_list[idx]:
            # skips tensorizer serialization lines
            index = idx + 1
            logger.info(
                "Skip tensorizer serialization. Start from log line %d: %s",
                index + 1,
                log_list[index],
            )
            break

        idx += 1

    while index < len(log_list):
        index = populate_benchmark_category(index, log_list, root_benchmark_category)
        index += 1


def add_uncategorized_categories(benchmark_category: BenchmarkCategory):
    """add filler uncategorized categories"""

    category = benchmark_category
    while category is not None:
        if category.root_child is not None:
            add_uncategorized_categories(category.root_child)

        # if exists a gap, create uncategorized
        next_category = category.next
        if (
            next_category is not None
            and category.end_time is not None
            and next_category.start_time is not None
            and category.end_time < next_category.start_time
        ):
            benchmark_category = BenchmarkCategory()
            benchmark_category.title = "Uncategorized"
            benchmark_category.start_time = category.end_time
            benchmark_category.start_line = category.end_line
            benchmark_category.start_line_number = category.end_line_number
            benchmark_category.end_time = next_category.start_time
            benchmark_category.end_line = next_category.start_line
            benchmark_category.end_line_number = next_category.start_line_number
            benchmark_category.parent = category.parent
            benchmark_category.next = next_category
            category.next = benchmark_category
            # skip the uncategorized created category
            category = category.next

        category = category.next


def populate_benchmark_category(
    index: int, log_list: list[str], benchmark_category: BenchmarkCategory
) -> int:
    """populate category from log line"""

    category = benchmark_category
    while category is not None and index < len(log_list):
        if category.start_line == "" and category.start in log_list[index]:
            category.start_time = extract_datetime(log_list[index])
            # if date extract failed, try next log line
            while category.start_time is None:
                index += 1
                if index >= len(log_list):
                    return index

                category.start_time = extract_datetime(log_list[index])

            if category.start_time is not None:
                category.start_line = log_list[index]
                category.start_line_number = index + 1

        if category.end_line == "" and category.end in log_list[index]:
            category.end_time = extract_datetime(log_list[index])
            # if date extract failed, try next log line
            while category.end_time is None:
                index += 1
                if index >= len(log_list):
                    return index

                category.end_time = extract_datetime(log_list[index])

            if category.end_time is not None:
                category.end_line = log_list[index]
                category.end_line_number = index + 1

        if category.root_child is not None:
            index = populate_benchmark_category(index, log_list, category.root_child)

        category = category.next

    return index


def parse_logs(logs: str) -> BenchmarkResult:
    """parse vllm logs"""

    # Strings to be searched on logging ouput in order to extract values

    server_non_default_args = "non-default args:"
    model_sleep_mode = "'enable_sleep_mode':"
    model_load_format = "load_format=LoadFormat."
    # Model loading took 15.2209 GB and 12.221976 seconds
    model_load_string = "Model loading took"
    # It took 0.001315 seconds to fall asleep.
    model_sleep_string = " seconds to fall asleep"
    # It took 0.000018 seconds to wake up.
    model_wake_string = " seconds to wake up"
    model_took_string = " It took "
    # Sleep mode freed 69.50 GiB memory, 0.75 GiB memory is still in use.
    model_gpu_freed = "Sleep mode freed"

    benchmark_result = BenchmarkResult()

    # loop from the bottom to catch latest statistics before old ones
    sleep_mode = ""
    args = None
    for line in reversed(logs.splitlines()):
        if (
            args is not None
            and sleep_mode != ""
            and benchmark_result.scenario.load_format != LoadFormat.UNKNOWN
            and benchmark_result.metrics.load_time != 0
            and benchmark_result.metrics.sleep != 0
            and benchmark_result.metrics.gpu_freed != 0
            and benchmark_result.metrics.gpu_in_use != 0
            and benchmark_result.metrics.wake != 0
        ):
            break

        line = line.strip()

        if args is None:
            start_index = line.find(server_non_default_args)
            if start_index >= 0:
                start_index += len(server_non_default_args)
                args = line[start_index:].strip()
                try:
                    benchmark_result.scenario.platform.engine.args = ast.literal_eval(
                        args
                    )
                except Exception:
                    logger.exception(
                        "log args dict parsing returned error converting: %s",
                        args,
                    )

        if sleep_mode == "":
            start_index = line.find(model_sleep_mode)
            if start_index >= 0:
                start_index += len(model_sleep_mode)
                end_index = line.find(",", start_index)
                if end_index < 0:
                    end_index = line.find("}", start_index)
                if end_index >= 0:
                    sleep_mode = line[start_index:end_index].strip().lower()
                    benchmark_result.scenario.sleep_mode = "true" == sleep_mode

        if benchmark_result.scenario.load_format == LoadFormat.UNKNOWN:
            start_index = line.find(model_load_format)
            if start_index >= 0:
                start_index += len(model_load_format)
                end_index = line.find(",", start_index)
                if end_index >= 0:
                    format_name = line[start_index:end_index].strip()
                    for f in LoadFormat:
                        if f.name == format_name:
                            benchmark_result.scenario.load_format = f
                            break

        if benchmark_result.metrics.load_time == 0:
            floats = find_floats_in_line(model_load_string, line)
            if len(floats) > 1:
                benchmark_result.metrics.size = floats[0]
                benchmark_result.metrics.load_time = floats[1]
                continue

        if benchmark_result.metrics.sleep == 0 and model_sleep_string in line:
            floats = find_floats_in_line(model_took_string, line)
            if len(floats) > 0:
                benchmark_result.metrics.sleep = floats[0]
                continue

        if benchmark_result.metrics.gpu_freed == 0:
            floats = find_floats_in_line(model_gpu_freed, line)
            if len(floats) > 1:
                benchmark_result.metrics.gpu_freed = floats[0]
                benchmark_result.metrics.gpu_in_use = floats[1]
                continue

        if benchmark_result.metrics.wake == 0 and model_wake_string in line:
            floats = find_floats_in_line(model_took_string, line)
            if len(floats) > 0:
                benchmark_result.metrics.wake = floats[0]
                continue

    return benchmark_result


def find_floats_in_line(key: str, line: str) -> list[float]:
    """find fload numbers in log line"""
    index = line.find(key)
    if index >= 0:
        return extract_floats(line[index:])

    return []


def extract_floats(text) -> list[float]:
    """extracts all float numbers from a string"""
    return [float(num) for num in re.findall(r"[-+]?\d*\.\d+|\d+", text)]


def convert_result(result_filepath: str, output_filepath: str) -> tuple[str, str, int]:
    """converts result to universal format"""

    try:
        cmd = ["convert.py", result_filepath, output_filepath, "-w", "nop", "-f"]
        with subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
        ) as proc:
            stdout, stderr = proc.communicate()
            out_str = stdout.strip().decode("ascii")
            err_str = stderr.strip().decode("ascii")
            if proc.returncode != 0:
                logger.info(
                    "convert.py returned with error %s converting: %s",
                    proc.returncode,
                    result_filepath,
                )
            else:
                logger.info("convert.py succeeded converting: %s", result_filepath)

            if err_str != "":
                logger.info("convert.py stderr: %s", err_str)
            if out_str != "":
                logger.info("convert.py stdout: %s", out_str)
    except Exception:
        logger.exception("convert.py returned error converting: %s", result_filepath)


def write_benchmark_categories_to_log(
    level: int, benchmark_category: BenchmarkCategory, file: io.BufferedWriter
):
    """write benchmark category tree log"""
    blank_string = "  " * level if level > 0 else ""
    category = benchmark_category
    while category is not None:
        elapsed = ""
        if category.start_time is not None and category.end_time is not None:
            time_difference = category.end_time - category.start_time
            elapsed = f"{time_difference.total_seconds():.3f}"

        file.write(f"{blank_string}Log category   : '{category.title}'\n")
        time_format = "%m-%d %H:%M:%S.%f"
        date_str = (
            category.start_time.strftime(time_format)[:-3]
            if category.start_time is not None
            else ""
        )
        file.write(f"{blank_string}  start date   : '{date_str}'\n")
        date_str = (
            category.end_time.strftime(time_format)[:-3]
            if category.end_time is not None
            else ""
        )
        file.write(f"{blank_string}  end date     : '{date_str}'\n")
        file.write(f"{blank_string}  elapsed      : {elapsed}\n")
        file.write(f"{blank_string}  start pattern: '{category.start}'\n")
        file.write(f"{blank_string}  end pattern  : '{category.end}'\n")
        file.write(
            f"{blank_string}  start line   : {category.start_line_number} '{category.start_line}'\n"
        )
        file.write(
            f"{blank_string}  end line     : {category.end_line_number} '{category.end_line}'\n"
        )
        if category.root_child is not None:
            write_benchmark_categories_to_log(level + 1, category.root_child, file)
        category = category.next


def main():
    """main entry point"""

    start_time = datetime.now().timestamp()

    envs = get_env_variables(
        [
            "LLMDBENCH_HARNESS_NAMESPACE",
            "LLMDBENCH_HARNESS_STACK_ENDPOINT_URL",
            "LLMDBENCH_CONTROL_WORK_DIR",
        ]
    )

    namespace = envs[0]
    endpoint_url = envs[1]
    control_work_dir = envs[2]
    requests_dir = control_work_dir

    Path(requests_dir).mkdir(parents=True, exist_ok=True)

    file_handler = logging.FileHandler(f"{requests_dir}/stdout.log")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    domain = urlparse(endpoint_url).netloc
    arr = domain.split(".")
    if len(arr) == 0:
        raise RuntimeError(f"Unable to extract service name from {domain}.")

    # Load Kubernetes configuration
    config.load_kube_config()

    v1 = client.CoreV1Api()

    pod_info = None
    try:
        pod_info = get_vllm_pod_info(v1, namespace, arr[0])
        logger.info(
            "vLLM standalone pod name: %s image: %s",
            pod_info["name"],
            pod_info["image"],
        )
    except Exception as e:
        logger.info(
            "Skipping harness because vLLM standalone pod not found: %s", str(e)
        )
        return

    vllm_version = get_vllm_version(endpoint_url, REQUEST_TIMEOUT)
    vllm_model = get_vllm_model(endpoint_url, REQUEST_TIMEOUT)

    pod_logs = get_pod_logs(v1, namespace, pod_info["name"])
    benchmark_result = parse_logs(pod_logs.decode("utf-8"))
    if benchmark_result.scenario.sleep_mode:
        logger.info("Request sleep/wake")
        sleep(endpoint_url, 1, REQUEST_TIMEOUT)
        wake(endpoint_url, REQUEST_TIMEOUT)
        # get logs again with latest sleep/wake statistics
        pod_logs = get_pod_logs(v1, namespace, pod_info["name"])
        benchmark_result = parse_logs(pod_logs.decode("utf-8"))

    benchmark_result.scenario.model.name = vllm_model
    benchmark_result.scenario.platform.engine.name = pod_info["image"]
    benchmark_result.scenario.platform.engine.version = vllm_version

    # categorize logs
    benchmark_result.metrics.root_category = categorize_logs(
        vllm_model, pod_logs.decode("utf-8")
    )

    os.makedirs(requests_dir, exist_ok=True)

    # write vllm log file
    logs_filepath = os.path.join(requests_dir, "vllm.log")
    with open(logs_filepath, "wb") as file:
        file.write(pod_logs)
        logger.info("vllm log file saved to path: %s", logs_filepath)

    benchmark_result.metrics.time.start = start_time
    benchmark_result.metrics.time.stop = datetime.now().timestamp()

    # write results yaml file
    result_filepath = os.path.join(requests_dir, "result.yaml")
    with open(result_filepath, "w", encoding="utf-8", newline="") as file:
        yaml.dump(benchmark_result.dump(), file, indent=2, sort_keys=False)
        logger.info("result yaml file saved to path: %s", result_filepath)

    benchmark_report_filepath = os.path.join(requests_dir, "benchmark_report")
    os.makedirs(benchmark_report_filepath, exist_ok=True)
    benchmark_report_filepath = os.path.join(benchmark_report_filepath, "result.yaml")
    convert_result(result_filepath, benchmark_report_filepath)

    # write log categories log file
    log_categories_filepath = os.path.join(requests_dir, "categories.log")
    with open(log_categories_filepath, "w", encoding="utf-8", newline="") as file:
        write_benchmark_categories_to_log(
            0, benchmark_result.metrics.root_category, file
        )
        logger.info(
            "benchmark categories log file saved to path: %s", log_categories_filepath
        )


if __name__ == "__main__":
    try:
        logger.info("Starting harness run")
        main()
    except Exception:
        logger.exception("Error running harness")
    finally:
        logger.info("End harness run")
