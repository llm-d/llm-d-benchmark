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

# MM-DD HH:MM:SS or MM-DD HH:MM:SS.MMM
DATE_PATTERN = re.compile(r"\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}(?:\.\d{3})?")

PROCESS_PATTERN = re.compile(r"\(.*?\)")

DEFINED_CATEGORIES = [
    {
        "title": "Detect Platform",
        "start": "No plugins for group",
        "end": "detected platform",
    },
    {
        "title": "LLM Imports",
        "start": "detected platform",
        "end": "Available plugins for group",
    },
    {
        "title": "Get Model Info",
        "start": "vLLM API server version",
        "end": "Using max model len",
    },
    {
        "title": "Worker Initialization",
        "start": "Waiting for init message",
        "end": "Starting to load model",
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
                "start": "Dynamo bytecode transform",
                "end": "torch.compile takes",
            },
        ],
    },
    {
        "title": "CUDA Graph Capture",
        "start": "torch.compile takes",
        "end": "init engine",
    },
    {
        "title": "API Server Starts",
        "start": "Starting vLLM API server",
        "end": "Route: /metrics",
    },
]


@dataclass
class PodContainerInfo:
    """Pod container info"""

    name: str = ""
    image: str = ""


@dataclass
class PodInfo:
    """Pod info"""

    name: str = ""
    containers: list[PodContainerInfo] = field(default_factory=list[PodContainerInfo])


@dataclass(frozen=True)
class BenchmarkProcess:
    """Process details"""

    name: str
    pid: int

    @staticmethod
    def process_from_line(line: str) -> BenchmarkProcess | None:
        """access process details from pattern"""

        matches = PROCESS_PATTERN.findall(line)
        for match in reversed(matches):
            start_index = match.find("pid=")
            if start_index < 0:
                continue

            name = match[:start_index].strip("( ")
            start_index += len("pid=")
            end_index = match.find(")", start_index)
            pid = 0
            if end_index > 0:
                try:
                    pid = int(match[start_index:end_index].strip())
                except ValueError:
                    logger.exception("error getting pid from '%s'", match)
            return BenchmarkProcess(name, pid)

        return None

    def desc(self) -> str:
        """process description"""
        if self.name == "" and self.pid == 0:
            return ""
        return f"{self.name} pid={self.pid}"

    def dump(self) -> dict[str, Any]:
        """Convert class BenchmarkProcess to dict.
        Returns:
            dict: Defined fields of BenchmarkProcess.
        """
        dump_dict = {}
        for f in fields(self):
            dump_dict[f.name] = getattr(self, f.name)

        return dump_dict


@dataclass
class LogLine:
    """log line info"""

    time: datetime | None = None
    process: BenchmarkProcess | None = None
    line: str = ""
    line_number: int = 0

    def process_desc(self) -> str:
        """process description"""
        return "" if self.process is None else self.process.desc()


@dataclass
class BenchmarkCategoryDetails:
    """Category details"""

    pattern: re.Pattern[str] | None = None
    log_line: LogLine | None = None

    def matches(self, log_line: LogLine) -> bool:
        """check if line matches"""
        match = self.pattern.search(log_line.line)
        return match is not None

    def pattern_desc(self) -> str:
        """pattern string"""
        return "" if self.pattern is None else self.pattern.pattern


@dataclass
class BenchmarkCategory:
    """Benchmark category"""

    title: str = ""
    defined: bool = False
    start: BenchmarkCategoryDetails = field(default_factory=BenchmarkCategoryDetails)
    end: BenchmarkCategoryDetails = field(default_factory=BenchmarkCategoryDetails)
    next: BenchmarkCategory | None = None
    parent: BenchmarkCategory | None = None
    root_child: BenchmarkCategory | None = None

    def process_desc(self) -> str:
        """process description"""
        procs = [
            "" if self.start.log_line is None else self.start.log_line.process_desc(),
            "" if self.end.log_line is None else self.end.log_line.process_desc(),
        ]
        return procs[0] if procs[0] == procs[1] else ", ".join(procs)

    def dump(self, include_not_defined: bool = False) -> list[dict[str, Any]]:
        """Convert BenchmarkCategory to list.
        Args:
            include_not_defined (bool): includes or not filler categories
        Returns:
            list: Defined fields of BenchmarkCategory.
        """
        return BenchmarkCategory._dump(self, include_not_defined)

    @staticmethod
    def _dump(
        benchmark_category: BenchmarkCategory, include_not_defined: bool
    ) -> list[dict[str, Any]]:
        categories = []
        category = benchmark_category
        while category is not None:
            if category.defined or include_not_defined:
                dump_dict = {"title": category.title}
                if (
                    category.start.log_line is not None
                    and category.end.log_line is not None
                ):
                    procs = [
                        category.start.log_line.process_desc(),
                        category.end.log_line.process_desc(),
                    ]
                    if procs[0] != procs[1]:
                        raise ValueError(
                            f"Category '{category.title}': "
                            f"start process '{procs[0]}' must be "
                            f"the same as end process '{procs[1]}'"
                        )
                    if category.start.log_line.process is not None:
                        dump_dict["process"] = category.start.log_line.process.dump()
                    elif category.end.log_line.process is not None:
                        dump_dict["process"] = category.end.log_line.process.dump()

                dump_dict["elapsed"] = 0.0
                if (
                    category.start.log_line is not None
                    and category.end.log_line is not None
                    and category.start.log_line.time is not None
                    and category.end.log_line.time is not None
                ):
                    dump_dict["elapsed"] = (
                        category.end.log_line.time - category.start.log_line.time
                    ).total_seconds()

                if category.root_child is not None:
                    dump_dict["categories"] = BenchmarkCategory._dump(
                        category.root_child, include_not_defined
                    )

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

    @staticmethod
    def loadformat_from_value(format_value: str) -> LoadFormat:
        """returns LoadFormat given value"""
        for f in LoadFormat:
            if f.value == format_value:
                return f

        return LoadFormat.UNKNOWN


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

    engines: list[PlatformEngineScenario] = field(
        default_factory=list[PlatformEngineScenario]
    )

    def dump(self) -> dict[str, Any]:
        """Convert PlatformScenario to dict.

        Returns:
            dict: Defined fields of PlatformScenario.
        """
        dump_dict = {}
        for f in fields(self):
            value = getattr(self, f.name)
            if f.name == "engines":
                dump_list = []
                for engine in value:
                    dump_list.append(engine.dump())
                dump_dict[f.name] = dump_list
                continue

            dump_dict[f.name] = (
                value.dump()
                if hasattr(value, "dump") and callable(value.dump)
                else value
            )

        return dump_dict


@dataclass
class GPUScenario:
    """GPU Scenario"""

    uuid: str = ""
    name: str = ""
    compute_cap: str = ""
    persistence_mode: str = ""

    def dump(self) -> dict[str, Any]:
        """Convert GPUScenario to dict.

        Returns:
            dict: Defined fields of GPUScenario.
        """
        dump_dict = {}
        for f in fields(self):
            dump_dict[f.name] = getattr(self, f.name)

        return dump_dict


@dataclass
class BenchmarkScenario:
    """Benchmark Scenario"""

    load_format: LoadFormat = LoadFormat.UNKNOWN
    sleep_mode: bool = False
    model: ModelScenario = field(default_factory=ModelScenario)
    platform: PlatformScenario = field(default_factory=PlatformScenario)
    gpus: list[GPUScenario] = field(default_factory=list[GPUScenario])

    def dump(self) -> dict[str, Any]:
        """Convert BenchmarkScenario to dict.

        Returns:
            dict: Defined fields of BenchmarkScenario.
        """
        dump_dict = {}
        for f in fields(self):
            value = getattr(self, f.name)
            if f.name == "gpus":
                dump_list = []
                for gpu in value:
                    dump_list.append(gpu.dump())
                dump_dict[f.name] = dump_list
                continue

            dump_dict[f.name] = (
                value.dump()
                if hasattr(value, "dump") and callable(value.dump)
                else value
            )

        return dump_dict


@dataclass
class BenchmarkTime:
    """Timing details of benchmark run."""

    start: float = 0.0
    """Start time of benchmark run, in seconds from Unix epoch."""
    stop: float = 0.0
    """End time of benchmark run, in seconds from Unix epoch."""

    def dump(self) -> dict[str, Any]:
        """Convert BenchmarkTime to dict.

        Returns:
            dict: Defined fields of BenchmarkTime.
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
class MetricsSleep:
    """Sleep metrics"""

    time: float = 0.0
    gpu_freed: float = 0.0
    gpu_in_use: float = 0.0

    def dump(self) -> dict[str, Any]:
        """Convert MetricsSleep to dict.

        Returns:
            dict: Defined fields of MetricsSleep.
        """
        dump_dict = {}
        for f in fields(self):
            value = getattr(self, f.name)
            dump_dict[f.name] = value

        return dump_dict


@dataclass
class MetricsMemoryProfiling:
    """Memory Profiling metrics"""

    initial_free: float = 0.0
    after_free: float = 0.0
    time: float = 0.0

    def dump(self) -> dict[str, Any]:
        """Convert MetricsMemoryProfiling to dict.

        Returns:
            dict: Defined fields of MetricsMemoryProfiling.
        """
        dump_dict = {}
        for f in fields(self):
            value = getattr(self, f.name)
            dump_dict[f.name] = value

        return dump_dict


@dataclass
class MetricsLoad:
    """Load metrics"""

    time: float = 0.0
    size: float = 0.0

    def dump(self) -> dict[str, Any]:
        """Convert MetricsLoad to dict.

        Returns:
            dict: Defined fields of MetricsLoad.
        """
        dump_dict = {}
        for f in fields(self):
            value = getattr(self, f.name)
            dump_dict[f.name] = value

        transfer_rate = 0.0
        if self.time != 0.0:
            transfer_rate = self.size / self.time
        dump_dict["transfer_rate"] = transfer_rate

        return dump_dict


@dataclass
class BenchmarkMetrics:
    """Benchmark Metrics"""

    name: str = ""
    load: MetricsLoad = field(default_factory=MetricsLoad)
    size: float = 0.0
    dynamo_bytecode_transform: float = 0.0
    load_cached_compiled_graph: float = 0.0
    compile_graph: float = 0.0
    torch_compile: float = 0.0
    memory_profiling: MetricsMemoryProfiling = field(
        default_factory=MetricsMemoryProfiling
    )
    sleep: MetricsSleep = field(default_factory=MetricsSleep)
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
            if f.name in ["load_cached_compiled_graph", "compile_graph"] and value == 0:
                continue

            dump_dict[f.name] = (
                value.dump()
                if hasattr(value, "dump") and callable(value.dump)
                else value
            )

        if self.root_category is not None:
            dump_dict["categories"] = self.root_category.dump()
        return dump_dict


@dataclass
class BenchmarkResult:
    """Results of one benchmark run"""

    version: str = "0.1"
    scenario: BenchmarkScenario = field(default_factory=BenchmarkScenario)
    metrics: list[BenchmarkMetrics] = field(default_factory=list[BenchmarkMetrics])
    time: BenchmarkTime = field(default_factory=BenchmarkTime)

    def dump(self) -> dict[str, Any]:
        """Convert BenchmarkResult to dict.

        Returns:
            dict: Defined fields of BenchmarkResult.
        """
        dump_dict = {}
        for f in fields(self):
            value = getattr(self, f.name)
            if f.name == "metrics":
                dump_list = []
                for metrics in value:
                    dump_list.append(metrics.dump())
                dump_dict[f.name] = dump_list
                continue

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

    response_json = response.json()
    logger.info("vLLM server version: %s", response_json.get(path))
    return response_json.get(path)


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

    response_json = response.json()
    logger.info("sleep status: %s", response_json.get(path))
    return response_json.get(path)


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
) -> PodInfo:
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

    # return first pod info
    if len(pod_infos[0].containers) == 0:
        raise RuntimeError(
            f"No pod container found on namespace {namespace} with pod name '{pod_infos[0].name}'."
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


def get_pod_infos(v1: client.CoreV1Api, namespace: str, selector: str) -> list[PodInfo]:
    """get pods by selector"""

    pod_list = v1.list_namespaced_pod(
        namespace=namespace, label_selector=f"app={selector}"
    )
    pod_infos = []
    for pod in pod_list.items:
        pod_info = PodInfo()
        pod_info.name = pod.metadata.name
        for container in pod.spec.containers:
            pod_container_info = PodContainerInfo()
            pod_container_info.name = container.name
            pod_container_info.image = container.image
            pod_info.containers.append(pod_container_info)
        pod_infos.append(pod_info)

    return pod_infos


def get_pod_logs(
    v1: client.CoreV1Api, namespace: str, pod_name: str, container_name: str
) -> bytes:
    """get pod logs"""

    response = v1.read_namespaced_pod_log(
        name=pod_name,
        container=container_name,
        namespace=namespace,
        pretty=False,
        _preload_content=False,
    )
    return response.data


def extract_datetime(log_line: str) -> datetime | None:
    """extracts datetime"""

    match = DATE_PATTERN.search(log_line)
    if match is None:
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
        benchmark_category.defined = True
        benchmark_category.start.pattern = re.compile(
            rf"{defined_category.get('start')}"
        )
        benchmark_category.end.pattern = re.compile(rf"{defined_category.get('end')}")
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


def get_log_list(logs: str) -> list[LogLine]:
    """get log lines info"""

    log_list = []
    for idx, line in enumerate(logs.splitlines()):
        log_line = LogLine()
        log_line.line_number = idx + 1
        log_line.line = line
        log_line.time = extract_datetime(log_line.line)
        log_line.process = BenchmarkProcess.process_from_line(log_line.line)
        log_list.append(log_line)

    return log_list


def get_log_list_per_process(
    vllm_model: str, log_list: list[LogLine]
) -> dict[BenchmarkProcess, list[LogLine]]:
    """get log list divided by Process"""

    tensorizer_serialization_end = f"End model {vllm_model} serialization"
    uvicorn_running = "Uvicorn running"

    # look for possible tensorizer serialization or uvicorn
    idx = 0
    for log_line in log_list:
        if (
            tensorizer_serialization_end in log_line.line
            or uvicorn_running in log_line.line
        ):
            # skip lines
            idx = log_line.line_number
            break

    log_list_per_process = {}
    if idx > 0:
        if idx >= len(log_list):
            return log_list_per_process
        log_line = log_list[idx]
        logger.info(
            "Skip tensorizer serialization or uvicorn. Start from log line %d: %s",
            log_line.line_number,
            log_line.line,
        )

    for log_line in log_list[idx:]:
        if log_line.process not in log_list_per_process:
            log_list_per_process[log_line.process] = []

        log_list_per_process[log_line.process].append(log_line)

    return log_list_per_process


def categorize_logs(
    log_list_per_process: dict[BenchmarkProcess, list[LogLine]],
) -> BenchmarkCategory:
    """parse logs and categorize it"""

    root_benchmark_category = initialize_benchmark_categories(DEFINED_CATEGORIES, None)
    populate_benchmark_categories(log_list_per_process, root_benchmark_category)
    # add uncategorized categories
    add_uncategorized_categories(root_benchmark_category)
    return root_benchmark_category


def populate_benchmark_categories(
    log_list_per_process: dict[BenchmarkProcess, list[LogLine]],
    root_benchmark_category: BenchmarkCategory,
):
    """populate categories from log lines"""

    for _, log_list_process in log_list_per_process.items():
        index = 0
        while index < len(log_list_process):
            index = populate_benchmark_category(
                index, log_list_process, root_benchmark_category
            )
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
            and category.end.log_line is not None
            and category.end.log_line.time is not None
            and next_category.start.log_line is not None
            and next_category.start.log_line.time is not None
            and category.end.log_line.time < next_category.start.log_line.time
        ):
            benchmark_category = BenchmarkCategory()
            benchmark_category.title = "Uncategorized"
            benchmark_category.start.log_line = category.end.log_line
            benchmark_category.end.log_line = next_category.start.log_line
            benchmark_category.parent = category.parent
            benchmark_category.next = next_category
            category.next = benchmark_category
            # skip the uncategorized created category
            category = category.next

        category = category.next


def populate_benchmark_category(
    index: int, log_list: list[LogLine], benchmark_category: BenchmarkCategory
) -> int:
    """populate category from log line"""

    category = benchmark_category
    while category is not None and index < len(log_list):
        if category.start.log_line is None and category.start.matches(log_list[index]):
            category.start.log_line = log_list[index]
            category.end.log_line = None
            # if no date, try next log line
            while category.start.log_line.time is None:
                index += 1
                if index >= len(log_list):
                    return index

                category.start.log_line = log_list[index]

        if category.end.log_line is None and category.end.matches(log_list[index]):
            category.end.log_line = log_list[index]
            # if no date, try next log line
            while category.end.log_line.time is None:
                index += 1
                if index >= len(log_list):
                    return index

                category.end = log_list[index]

        if category.root_child is not None:
            index = populate_benchmark_category(index, log_list, category.root_child)

        category = category.next

    return index


def parse_gpu_logs(scenario: BenchmarkScenario, logs: str) -> None:
    """parse gpu logs"""

    gpu_start = "--- gpu scenario start name:"
    gpu_id = "gpu_uuid='"
    gpu_name = "gpu_name='"
    compute_cap = "compute_cap='"
    persistence_mode = "persistence_mode='"
    gpu_end = "--- gpu scenario end name:"

    # load from start to get gpus scenario
    gpus_scenario_found = False
    for line in logs.splitlines():
        line = line.strip()
        if gpu_start in line:
            gpus_scenario_found = True
            continue

        if gpu_end in line:
            break

        if not gpus_scenario_found:
            continue

        gpu_dict = {}
        for pattern in [gpu_id, gpu_name, compute_cap, persistence_mode]:
            start_index = line.find(pattern)
            if start_index >= 0:
                start_index += len(pattern)
                end_index = line.find("'", start_index)
                if end_index >= 0:
                    gpu_dict[pattern] = line[start_index:end_index].strip()

        gpu_scenario = GPUScenario()
        gpu_scenario.uuid = gpu_dict.get(gpu_id, "")
        gpu_scenario.name = gpu_dict.get(gpu_name, "")
        gpu_scenario.compute_cap = gpu_dict.get(compute_cap, "")
        gpu_scenario.persistence_mode = gpu_dict.get(persistence_mode, "")
        scenario.gpus.append(gpu_scenario)


def parse_logs(
    scenario: BenchmarkScenario,
    engine: PlatformEngineScenario,
    metrics: BenchmarkMetrics,
    logs: str,
) -> None:
    """parse vllm logs"""

    # Strings to be searched on logging ouput in order to extract values

    server_non_default_args = "non-default args:"
    model_sleep_mode = "'enable_sleep_mode':"
    model_load_format = "load_format="
    # Model loading took 15.2209 GB and 12.221976 seconds
    model_load_string = "Model loading took"

    # Dynamo bytecode transform time: 3.96 s
    dynamo_bytecode_transform = "Dynamo bytecode transform time"

    # Directly load the compiled graph(s) for dynamic shape from the cache, took %.3f s
    # Directly load the compiled graph(s) for shape %s from the cache, took %.3f s
    cached_compiled_graph = "Directly load the compiled graph(s) for "

    # Compiling a graph for dynamic shape takes %.2f s
    # Compiling a graph for shape %s takes %.2f s
    compiled_graph = "Compiling a graph for "

    # torch.compile takes 17.88 s in total
    torch_compile = "torch.compile takes"

    # Initial free memory: 43.90 GiB; Requested memory: 0.95 (util), 42.17 GiB
    initial_free_memory = "Initial free memory:"
    # Free memory after profiling: 42.85 GiB (total), 41.12 GiB (within requested)
    free_memory_after_profiling = "Free memory after profiling:"
    # Memory profiling takes 26.21 seconds. Total non KV cache memory: 1.48GiB
    # torch peak memory increase: 0.52GiB; non-torch forward increase memory: 0.04GiB;
    # weights memory: 0.93GiB.
    memory_profiling = "Memory profiling takes"

    # It took 0.001315 seconds to fall asleep.
    model_sleep_string = " seconds to fall asleep"
    # It took 0.000018 seconds to wake up.
    model_wake_string = " seconds to wake up"
    model_took_string = " It took "
    # Sleep mode freed 69.50 GiB memory, 0.75 GiB memory is still in use.
    model_gpu_freed = "Sleep mode freed"

    # loop from the bottom to catch latest statistics before old ones
    sleep_mode = ""
    args = None
    for line in reversed(logs.splitlines()):
        if (
            args is not None
            and sleep_mode != ""
            and scenario.load_format != LoadFormat.UNKNOWN
            and metrics.load.time != 0
            and metrics.dynamo_bytecode_transform != 0
            and (metrics.load_cached_compiled_graph != 0 or metrics.compile_graph != 0)
            and metrics.memory_profiling.initial_free != 0
            and metrics.memory_profiling.after_free != 0
            and metrics.memory_profiling.time != 0
            and metrics.torch_compile != 0
            and metrics.sleep.time != 0
            and metrics.sleep.gpu_freed != 0
            and metrics.sleep.gpu_in_use != 0
            and metrics.wake != 0
        ):
            break

        line = line.strip()

        if args is None:
            start_index = line.find(server_non_default_args)
            if start_index >= 0:
                start_index += len(server_non_default_args)
                args = line[start_index:].strip()
                try:
                    engine.args = ast.literal_eval(args)
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
                    scenario.sleep_mode = "true" == sleep_mode

        if scenario.load_format == LoadFormat.UNKNOWN:
            start_index = line.find(model_load_format)
            if start_index >= 0:
                start_index += len(model_load_format)
                end_index = line.find(",", start_index)
                if end_index >= 0:
                    format_value = line[start_index:end_index].strip()
                    scenario.load_format = LoadFormat.loadformat_from_value(
                        format_value
                    )

        if metrics.load.time == 0:
            floats = find_floats_in_line(model_load_string, line)
            if len(floats) > 1:
                metrics.load.size = floats[0]
                metrics.load.time = floats[1]
                continue

        if metrics.dynamo_bytecode_transform == 0:
            floats = find_floats_in_line(dynamo_bytecode_transform, line)
            if len(floats) > 0:
                metrics.dynamo_bytecode_transform = floats[0]
                continue

        if metrics.load_cached_compiled_graph == 0 and metrics.compile_graph == 0:
            floats = find_floats_in_line(cached_compiled_graph, line)
            if len(floats) > 0:
                metrics.load_cached_compiled_graph = floats[0]
                continue
            floats = find_floats_in_line(compiled_graph, line)
            if len(floats) > 0:
                metrics.compile_graph = floats[0]
                continue

        if metrics.torch_compile == 0:
            floats = find_floats_in_line(torch_compile, line)
            if len(floats) > 0:
                metrics.torch_compile = floats[0]
                continue

        if metrics.memory_profiling.initial_free == 0:
            floats = find_floats_in_line(initial_free_memory, line)
            if len(floats) > 0:
                metrics.memory_profiling.initial_free = floats[0]
                continue

        if metrics.memory_profiling.after_free == 0:
            floats = find_floats_in_line(free_memory_after_profiling, line)
            if len(floats) > 0:
                metrics.memory_profiling.after_free = floats[0]
                continue

        if metrics.memory_profiling.time == 0:
            floats = find_floats_in_line(memory_profiling, line)
            if len(floats) > 0:
                metrics.memory_profiling.time = floats[0]
                continue

        if metrics.sleep.time == 0 and model_sleep_string in line:
            floats = find_floats_in_line(model_took_string, line)
            if len(floats) > 0:
                metrics.sleep.time = floats[0]
                continue

        if metrics.sleep.gpu_freed == 0:
            floats = find_floats_in_line(model_gpu_freed, line)
            if len(floats) > 1:
                metrics.sleep.gpu_freed = floats[0]
                metrics.sleep.gpu_in_use = floats[1]
                continue

        if metrics.wake == 0 and model_wake_string in line:
            floats = find_floats_in_line(model_took_string, line)
            if len(floats) > 0:
                metrics.wake = floats[0]
                continue


def find_floats_in_line(key: str, line: str) -> list[float]:
    """find fload numbers in log line"""
    index = line.find(key)
    if index >= 0:
        return extract_floats(line[index:])

    return []


def extract_floats(text: str) -> list[float]:
    """extracts all float numbers from a string"""
    return [float(num) for num in re.findall(r"[-+]?\d*\.\d+|\d+", text)]


def convert_result(result_filepath: str, output_filepath: str) -> tuple[str, str, int]:
    """converts result to universal format"""

    try:
        cmd = ["benchmark-report", result_filepath, output_filepath, "-w", "nop", "-f"]
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
                    "benchmark-report returned with error %s converting: %s",
                    proc.returncode,
                    result_filepath,
                )
            else:
                logger.info("benchmark-report succeeded converting: %s", result_filepath)

            if err_str != "":
                logger.info("benchmark-report stderr: %s", err_str)
            if out_str != "":
                logger.info("benchmark-report stdout: %s", out_str)
    except Exception:
        logger.exception("benchmark-report returned error converting: %s", result_filepath)


def write_benchmark_categories_to_log(
    level: int, benchmark_category: BenchmarkCategory, file: io.BufferedWriter
):
    """write benchmark category tree log"""
    blank_string = "  " * level if level > 0 else ""
    category = benchmark_category
    while category is not None:
        elapsed = ""
        if (
            category.start.log_line is not None
            and category.start.log_line.time is not None
            and category.end.log_line is not None
            and category.end.log_line.time is not None
        ):
            time_difference = category.end.log_line.time - category.start.log_line.time
            elapsed = f"{time_difference.total_seconds():.3f}"

        file.write("\n")
        file.write(f"{blank_string}Log category   : '{category.title}'\n")
        file.write(f"{blank_string}  Process      : '{category.process_desc()}'\n")
        time_format = "%m-%d %H:%M:%S.%f"
        date_str = (
            category.start.log_line.time.strftime(time_format)[:-3]
            if category.start.log_line is not None
            and category.start.log_line.time is not None
            else ""
        )
        file.write(f"{blank_string}  Start date   : '{date_str}'\n")
        date_str = (
            category.end.log_line.time.strftime(time_format)[:-3]
            if category.end.log_line is not None
            and category.end.log_line.time is not None
            else ""
        )
        file.write(f"{blank_string}  End date     : '{date_str}'\n")
        file.write(f"{blank_string}  Elapsed      : {elapsed}\n")
        file.write(
            f"{blank_string}  Start pattern: '{category.start.pattern_desc()}'\n"
        )
        file.write(f"{blank_string}  End pattern  : '{category.end.pattern_desc()}'\n")
        if category.start.log_line is None:
            file.write(f"{blank_string}  Start line   :\n")
        else:
            file.write(
                f"{blank_string}  Start line   : "
                f"{category.start.log_line.line_number} '{category.start.log_line.line}'\n"
            )
        if category.end.log_line is None:
            file.write(f"{blank_string}  End line     :\n")
        else:
            file.write(
                f"{blank_string}  End line     : "
                f"{category.end.log_line.line_number} '{category.end.log_line.line}'\n"
            )
        if category.root_child is not None:
            write_benchmark_categories_to_log(level + 1, category.root_child, file)
        category = category.next


def convert_vllm_args(args: dict[str, Any], vllm_port: str) -> list[str]:
    """convert vLLM args to launcher options"""

    # --no-enable-prefix-caching \
    # --load-format auto \
    # --port 8000 \
    # --max-model-len 16384 \
    # --disable-log-requests \
    # --gpu-memory-utilization 0.95 \
    # --tensor-parallel-size 1 \
    # --model-loader-extra-config "$LLMDBENCH_VLLM_COMMON_MODEL_LOADER_EXTRA_CONFIG" \
    # --enable-sleep-mode

    # {'enable_prefix_caching': False, 'enable_sleep_mode': True,
    # 'gpu_memory_utilization': 0.95, 'max_model_len': 16384,
    # 'model': 'Qwen/Qwen2.5-0.5B-Instruct',
    # 'model_loader_extra_config':
    # {'enable_multithread_load': True, 'num_threads': 8},
    # 'model_tag': 'Qwen/Qwen2.5-0.5B-Instruct'}

    vllm_args = ["--port", vllm_port]
    for key, value in args.items():
        if key in ("model_tag", "port"):
            continue
        name = key.replace("_", "-")
        if isinstance(value, bool):
            name = "--" + name if value else "--no-" + name
            vllm_args.append(name)
            continue
        if isinstance(value, (list, dict)):
            value = json.dumps(value, separators=(",", ":"))
        elif not isinstance(value, str):
            value = str(value)

        name = "--" + name
        vllm_args.append(name)
        vllm_args.append(value)

    return vllm_args


def start_vllm_server(base_launcher_url: str, args: list[str], timeout: float) -> str:
    """start vllm server"""

    launcher_args = {
        "options": " ".join(args),
    }

    url = urljoin(base_launcher_url, "v2/vllm/instances")
    response = requests.post(url, json=launcher_args, timeout=timeout)
    if response.status_code not in (200, 201):
        raise RuntimeError(
            f"launcher url '{url}' "
            f"options '{launcher_args}' error code {response.status_code}: '{response.text}'."
        )

    response_json = response.json()
    status = response_json.get("status")
    if status != "started":
        raise RuntimeError(
            f"launcher url '{url}' options '{launcher_args}' status {status}."
        )
    instance_id = response_json.get("instance_id")
    logger.info(
        "launcher vLLM started instance_id %s with options %s",
        instance_id,
        launcher_args,
    )
    return instance_id


def stop_vllm_server(base_url: str, instance_id, timeout: float) -> None:
    """stop vllm server"""

    url = urljoin(base_url, f"v2/vllm/instances/{instance_id}")
    response = requests.delete(url, timeout=timeout)
    if response.status_code != 200:
        raise RuntimeError(f"launcher url '{url}' error code {response.status_code}.")

    response_json = response.json()
    status = response_json.get("status")
    if status != "terminated":
        raise RuntimeError(f"launcher url '{url}' status {status}.")
    response_instance_id = response_json.get("instance_id")
    if response_instance_id != instance_id:
        raise RuntimeError(
            f"launcher url '{url}' stopped '{response_instance_id}' instead of '{instance_id}'."
        )


def get_vllm_server_status(base_url: str, timeout: float) -> bool:
    """stop vllm server"""

    url = urljoin(base_url, "v2/vllm/instances")
    response = requests.get(url, timeout=timeout)
    if response.status_code != 200:
        raise RuntimeError(f"launcher url '{url}' error code {response.status_code}.")

    response_json = response.json()
    total_instances = response_json.get("total_instances")
    running_instances = response_json.get("running_instances")
    instances = response_json.get("instances")

    logger.info(
        "launcher vllm server instances: total: %d running: %d",
        total_instances,
        running_instances,
    )

    for instance_status in instances:
        status = instance_status.get("status")
        instance_id = instance_status.get("instance_id")
        logger.info("launcher vllm server instance: %s status: %s", instance_id, status)

    return running_instances > 0


def wait_for_launcher(base_url: str, timeout: float) -> None:
    """wait for launcher to be ready"""
    url = urljoin(base_url, "health")
    start = time.perf_counter()
    while True:
        try:
            response = requests.get(url, timeout=timeout)
            if response.status_code == 200:
                status = response.json().get("status").strip()
                logger.info("launcher health status: %s", status)
                if status.lower() == "ok":
                    break
            logger.info(
                "launcher health check http code '%s' . Trying again ...",
                response.status_code,
            )
        except requests.Timeout:
            logger.info(
                "launcher health check timed out after '%d' secs. Trying again ...",
                timeout,
            )
        except Exception as e:
            logger.info(
                "launcher health check exception '%s'. Trying again ...", str(e)
            )

        time.sleep(0.5)
        elapsed = time.perf_counter() - start
        if elapsed > MAX_VLLM_WAIT:
            raise RuntimeError(f"launcher server failed to start after {elapsed} secs.")


def wait_for_vllm(base_url: str, timeout: float) -> None:
    """wait for vllm to be ready"""

    url = urljoin(base_url, "health")
    start = time.perf_counter()
    while True:
        try:
            response = requests.get(url, timeout=timeout)
            if response.status_code == 200:
                break
            logger.info(
                "vLLM health check http code '%s' . Trying again ...",
                response.status_code,
            )
        except requests.Timeout:
            logger.info(
                "vLLM health check timed out after '%d' secs. Trying again ...", timeout
            )
        except Exception as e:
            logger.info("vLLM health check exception '%s'. Trying again ...", str(e))

        time.sleep(0.5)
        elapsed = time.perf_counter() - start
        if elapsed > MAX_VLLM_WAIT:
            raise RuntimeError(f"vLLM server failed to start after {elapsed} secs.")


def populate_benchmark(
    name: str,
    v1: client.CoreV1Api,
    namespace: str,
    pod_name: str,
    container_name: str,
    load_format: LoadFormat,
    base_url: str,
    benchmark_result: BenchmarkResult,
    engine: PlatformEngineScenario,
    requests_dir: str,
    parse_gpus: bool,
    write_log_per_process: bool,
):
    """populate benchmark result"""

    engine.version = get_vllm_version(base_url, REQUEST_TIMEOUT)
    benchmark_result.scenario.model.name = get_vllm_model(base_url, REQUEST_TIMEOUT)

    metrics = BenchmarkMetrics()
    metrics.name = name
    benchmark_result.metrics.append(metrics)

    pod_logs = get_pod_logs(v1, namespace, pod_name, container_name)
    pod_logs_str = pod_logs.decode("utf-8")
    if parse_gpus:
        parse_gpu_logs(benchmark_result.scenario, pod_logs_str)

    parse_logs(
        benchmark_result.scenario,
        engine,
        metrics,
        pod_logs_str,
    )
    if benchmark_result.scenario.sleep_mode:
        logger.info("%s: request sleep/wake", name)
        sleep(base_url, 1, REQUEST_TIMEOUT)
        wake(base_url, REQUEST_TIMEOUT)
        # get logs again with latest sleep/wake statistics
        pod_logs = get_pod_logs(v1, namespace, pod_name, container_name)
        pod_logs_str = pod_logs.decode("utf-8")
        parse_logs(
            benchmark_result.scenario,
            engine,
            metrics,
            pod_logs_str,
        )

    # if failed to extract from logs
    if benchmark_result.scenario.load_format == LoadFormat.UNKNOWN:
        logger.info("%s: using load format from env. variable", name)
        benchmark_result.scenario.load_format = load_format

    # categorize logs
    log_list = get_log_list(pod_logs_str)
    log_list_per_process = get_log_list_per_process(
        benchmark_result.scenario.model.name, log_list
    )
    metrics.root_category = categorize_logs(log_list_per_process)

    output_dir = os.path.join(requests_dir, metrics.name.replace(" ", "_"))
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # write vllm log file
    logs_filepath = os.path.join(output_dir, "vllm.log")
    with open(logs_filepath, "wb") as file:
        file.write(pod_logs)
        logger.info("%s: vllm log file saved to path: %s", metrics.name, logs_filepath)

    if write_log_per_process:
        # write vllm logs per process
        for idx, (_, log_list_process) in enumerate(log_list_per_process.items()):
            logs_filepath = os.path.join(output_dir, f"vllm_{idx}.log")
            with open(logs_filepath, "w", encoding="utf-8") as file:
                for log_line in log_list_process:
                    file.write(f"{log_line.line_number:5d} {log_line.line}\n")
                logger.info(
                    "%s: vllm log file saved to path: %s", metrics.name, logs_filepath
                )

    # write log categories log file
    log_categories_filepath = os.path.join(output_dir, "categories.log")
    with open(log_categories_filepath, "w", encoding="utf-8", newline="") as file:
        write_benchmark_categories_to_log(0, metrics.root_category, file)
        logger.info(
            "%s: benchmark categories log file saved to path: %s",
            metrics.name,
            log_categories_filepath,
        )


def main():
    """main entry point"""

    start_time = datetime.now().timestamp()

    envs = get_env_variables(["LLMDBENCH_CONTROL_WORK_DIR"])
    requests_dir = envs[0]
    Path(requests_dir).mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(f"{requests_dir}/stdout.log")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    envs = get_env_variables(
        [
            "LLMDBENCH_HARNESS_NAMESPACE",
            "LLMDBENCH_HARNESS_STACK_ENDPOINT_URL",
            "LLMDBENCH_VLLM_COMMON_VLLM_LOAD_FORMAT",
            "LLMDBENCH_VLLM_STANDALONE_LAUNCHER",
            "LLMDBENCH_HARNESS_STACK_ENDPOINT_LAUNCHER_URL",
            "LLMDBENCH_HARNESS_STACK_ENDPOINT_LAUNCHER_VLLM_URL",
            "LLMDBENCH_VLLM_STANDALONE_LAUNCHER_VLLM_PORT",
        ]
    )

    namespace = envs[0]
    endpoint_url = envs[1]
    load_format = LoadFormat.loadformat_from_value(envs[2])
    launcher = envs[3].strip().lower() == "true"
    endpoint_launcher_url = envs[4]
    endpoint_launcher_vllm_url = envs[5]
    launcher_vllm_port = envs[6]

    write_log_per_process = False

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
        for container in pod_info.containers:
            logger.info(
                "vLLM standalone pod name: %s container: %s image: %s",
                pod_info.name,
                container.name,
                container.image,
            )

    except Exception as e:
        logger.info(
            "Skipping harness because vLLM standalone pod not found: %s", str(e)
        )
        return

    benchmark_result = BenchmarkResult()
    for container in pod_info.containers:
        engine = PlatformEngineScenario()
        engine.name = container.image
        benchmark_result.scenario.platform.engines.append(engine)

    benchmark_name = "vLLM Standalone"
    try:
        populate_benchmark(
            benchmark_name,
            v1,
            namespace,
            pod_info.name,
            pod_info.containers[0].name,
            load_format,
            endpoint_url,
            benchmark_result,
            benchmark_result.scenario.platform.engines[0],
            requests_dir,
            True,
            write_log_per_process,
        )
    except Exception:
        logger.exception("error on benchmark %s", benchmark_name)

    # Benchmark launcher if requested
    if launcher:
        benchmark_name = "vLLM Launcher"
        try:
            logger.info("Benchmark launcher start...")
            wait_for_launcher(endpoint_launcher_url, REQUEST_TIMEOUT)
            if not get_vllm_server_status(endpoint_launcher_url, REQUEST_TIMEOUT):
                # grab vLLM arguments from standalone
                args = convert_vllm_args(
                    benchmark_result.scenario.platform.engines[0].args,
                    launcher_vllm_port,
                )
                # start vLLM server
                _ = start_vllm_server(
                    endpoint_launcher_url,
                    args,
                    REQUEST_TIMEOUT,
                )
                wait_for_vllm(endpoint_launcher_vllm_url, REQUEST_TIMEOUT)
            populate_benchmark(
                benchmark_name,
                v1,
                namespace,
                pod_info.name,
                pod_info.containers[1].name,
                load_format,
                endpoint_launcher_vllm_url,
                benchmark_result,
                benchmark_result.scenario.platform.engines[1],
                requests_dir,
                False,
                write_log_per_process,
            )
        except Exception:
            logger.exception("error on benchmark %s", benchmark_name)
        finally:
            logger.info("Benchmark launcher end")

    benchmark_result.time.start = start_time
    benchmark_result.time.stop = datetime.now().timestamp()

    # write results yaml file
    result_filepath = os.path.join(requests_dir, "result.yaml")
    with open(result_filepath, "w", encoding="utf-8", newline="") as file:
        yaml.dump(benchmark_result.dump(), file, indent=2, sort_keys=False)
        logger.info("result yaml file saved to path: %s", result_filepath)

    benchmark_report_filepath = os.path.join(requests_dir, "benchmark_report")
    os.makedirs(benchmark_report_filepath, exist_ok=True)
    benchmark_report_filepath = os.path.join(benchmark_report_filepath, "result.yaml")
    convert_result(result_filepath, benchmark_report_filepath)


if __name__ == "__main__":
    try:
        logger.info("Starting harness run")
        main()
    except Exception:
        logger.exception("Error running harness")
    finally:
        logger.info("End harness run")
