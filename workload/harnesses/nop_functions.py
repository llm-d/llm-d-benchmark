"""
Benchmark 'nop' harness utility functions
"""
# pylint: disable=too-many-lines

from __future__ import annotations
from abc import ABC, abstractmethod
import ast
from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
from enum import StrEnum
from http import HTTPStatus
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
from kubernetes import client
from kubernetes.client.exceptions import ApiException

logger = logging.getLogger(__name__)

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
        "start": "vLLM API server version| version ",
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
        "start": "Start compiling function|torch/_dynamo",
        "end": "torch.compile takes",
        "children": [
            {
                "title": "Dynamo",
                "start": "Start compiling function|torch/_dynamo",
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


@dataclass(frozen=True)
class VllmInfo(ABC):
    """Abstract class for vllm logs request"""

    v1: client.CoreV1Api
    namespace: str
    pod_name: str
    container_name: str
    timeout: float

    def get_pod_start(self) -> float:
        """get pod start elapsed"""
        try:
            start = time.time()
            elapsed = 0.0
            while elapsed < self.timeout:
                pod = self.v1.read_namespaced_pod(
                    name=self.pod_name, namespace=self.namespace
                )
                start_time = pod.status.start_time
                for cond in pod.status.conditions or []:
                    if cond.type == "Ready" and cond.status == "True":
                        return (cond.last_transition_time - start_time).total_seconds()

                time.sleep(2)
                elapsed = time.time() - start
            raise RuntimeError(f"failed to get pod ready time after {elapsed} secs.")
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("error on pod '%s:%s'", self.namespace, self.pod_name)
            return 0.0

    def get_pod_logs(self) -> bytes:
        """get pod logs"""
        data = bytes()
        try:
            response = self.v1.read_namespaced_pod_log(
                name=self.pod_name,
                container=self.container_name,
                namespace=self.namespace,
                pretty=False,
                _preload_content=False,
            )
            data = response.data
        except ApiException:
            logger.exception("error on pod '%s:%s' logs", self.namespace, self.pod_name)
        return data

    @abstractmethod
    def get_vllm_logs(self) -> bytes:
        """get vllm logs"""

    @abstractmethod
    def calculate_categories(self) -> bool:
        """should calculate or not categories"""

    @abstractmethod
    def write_pod_logs(self) -> bool:
        """should write or not pod logs"""


@dataclass(frozen=True)
class VllmStandaloneInfo(VllmInfo):
    """vllm pod logs"""

    def get_vllm_logs(self) -> bytes:
        """get vllm logs"""

        return self.get_pod_logs()

    def calculate_categories(self) -> bool:
        """should calculate or not categories"""
        return True

    def write_pod_logs(self) -> bool:
        """should write or not pod logs"""
        return False


@dataclass(frozen=True)
class VllmLauncherInfo(VllmInfo):
    """vllm launched logs"""

    base_url: str
    instance_id: str
    categories_calculation: bool = True

    def get_vllm_logs(self) -> bytes:
        """get launched vllm logs"""

        url = urljoin(self.base_url, f"/v2/vllm/instances/{self.instance_id}/log")
        start_index = 0
        data = bytearray()
        while True:
            headers = {"Range": f"bytes={start_index}-"}
            response = requests.get(url, headers=headers, timeout=self.timeout)
            if response.status_code == HTTPStatus.PARTIAL_CONTENT:
                # "bytes 0-1023/12345"
                content_range = response.headers.get("Content-Range")
                range_part, total_size = content_range.split("/")
                start_end = range_part.split(" ")[1]  # remove "bytes "
                start_bytes, end_bytes = map(int, start_end.split("-"))
                total_bytes = int(total_size)
                logger.info(
                    "Vllm logs received bytes %d-%d of %d",
                    start_bytes,
                    end_bytes,
                    total_bytes,
                )

                data.extend(response.content)

                # Prepare next range
                start_index = end_bytes + 1

                # Stop if we've received the last byte
                if start_index >= total_bytes:
                    break
            elif response.status_code == HTTPStatus.OK:
                # Server ignored Range, sent entire file
                logger.info("Vllm logs received full content.")
                data.extend(response.content)
                break
            else:
                logger.info(
                    "launcher url '%s' headers '%s' error code %d: '%s'.",
                    url,
                    headers,
                    response.status_code,
                    response.text,
                )
                break

        return bytes(data)

    def calculate_categories(self) -> bool:
        """should calculate or not categories"""
        return self.categories_calculation

    def write_pod_logs(self) -> bool:
        """should write or not pod logs"""
        return True


@dataclass
class PodContainerInfo:
    """Pod container info"""

    name: str = ""
    image: str = ""


@dataclass
class PodInfo:
    """Pod info"""

    name: str = ""
    ip: str = ""
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

    timestamp: datetime | None = None
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
                    # procs = [
                    #    category.start.log_line.process_desc(),
                    #    category.end.log_line.process_desc(),
                    # ]
                    # if procs[0] != procs[1]:
                    #    raise ValueError(
                    #        f"Category '{category.title}': "
                    #        f"start process '{procs[0]}' must be "
                    #        f"the same as end process '{procs[1]}'"
                    #    )
                    if category.start.log_line.process is not None:
                        dump_dict["process"] = category.start.log_line.process.dump()
                    elif category.end.log_line.process is not None:
                        dump_dict["process"] = category.end.log_line.process.dump()

                dump_dict["elapsed"] = 0.0
                if (
                    category.start.log_line is not None
                    and category.end.log_line is not None
                    and category.start.log_line.timestamp is not None
                    and category.end.log_line.timestamp is not None
                ):
                    dump_dict["elapsed"] = (
                        category.end.log_line.timestamp
                        - category.start.log_line.timestamp
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
    image: str = ""
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

    engines: dict[str, PlatformEngineScenario] = field(
        default_factory=dict[str, PlatformEngineScenario]
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
                for engine in value.values():
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

    deploy_methods: str = ""
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


@dataclass(frozen=True)
class MetricsSleepWake(ABC):
    """Abstract class for sleep/wake vllm logs"""

    timestamp: float
    time: float

    def metrics_type(self) -> str:
        """get metrics type"""

    def dump(self) -> dict[str, Any]:
        """Convert MetricsSleepWake to dict.

        Returns:
            dict: Defined fields of MetricsSleepWake.
        """
        return {
            "timestamp": self.timestamp,
            "type": self.metrics_type(),
            "time": self.time,
        }


@dataclass(frozen=True)
class MetricsSleep(MetricsSleepWake):
    """Sleep metrics"""

    gpu_freed: float
    gpu_in_use: float

    def metrics_type(self) -> str:
        return "sleep"

    def dump(self) -> dict[str, Any]:
        """Convert MetricsSleep to dict.

        Returns:
            dict: Defined fields of MetricsSleep.
        """
        dump_dict = super().dump()
        for f in fields(self):
            value = getattr(self, f.name)
            dump_dict[f.name] = value

        return dump_dict


@dataclass(frozen=True)
class MetricsWake(MetricsSleepWake):
    """Wake metrics"""

    def metrics_type(self) -> str:
        return "wake"


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
class BenchmarkVllmMetrics:
    """Benchmark vLLM Metrics"""

    # pylint: disable=too-many-instance-attributes
    name: str = ""
    pod_start: float = 0.0
    vllm_start_timestamp: float = 0.0
    vllm_ready_timestamp: float = 0.0
    load: MetricsLoad = field(default_factory=MetricsLoad)
    size: float = 0.0
    dynamo_bytecode_transform: float = 0.0
    load_cached_compiled_graph: float = 0.0
    compile_graph: float = 0.0
    torch_compile: float = 0.0
    memory_profiling: MetricsMemoryProfiling = field(
        default_factory=MetricsMemoryProfiling
    )
    sleep_wake: list[MetricsSleepWake] = field(default_factory=list[MetricsSleepWake])

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

            if f.name == "sleep_wake":
                dump_list = []
                for sleeo_wake in value:
                    dump_list.append(sleeo_wake.dump())
                dump_dict[f.name] = dump_list
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
    vllm_metrics: dict[str, BenchmarkVllmMetrics] = field(
        default_factory=dict[str, BenchmarkVllmMetrics]
    )
    extra_metrics: list = field(default_factory=list)
    time: BenchmarkTime = field(default_factory=BenchmarkTime)

    def dump(self) -> dict[str, Any]:
        """Convert BenchmarkResult to dict.

        Returns:
            dict: Defined fields of BenchmarkResult.
        """
        dump_dict = {}
        for f in fields(self):
            value = getattr(self, f.name)
            if f.name == "vllm_metrics":
                dump_list = []
                for m in value.values():
                    dump_list.append(m.dump())
                dump_dict[f.name] = dump_list
                continue
            if f.name == "extra_metrics":
                dump_list = []
                for m in value:
                    v = m.dump() if hasattr(m, "dump") and callable(m.dump) else m
                    dump_list.append(v)
                dump_dict[f.name] = dump_list
                continue

            dump_dict[f.name] = (
                value.dump()
                if hasattr(value, "dump") and callable(value.dump)
                else value
            )

        return dump_dict


def get_env_variables(keys: list[str]) -> dict[str, str]:
    """get environment variables"""

    env_vars = os.environ

    envs = {}
    missing_envs = []
    empty_envs = []
    for key in keys:
        value = env_vars.get(key).strip()
        if value is None:
            missing_envs.append(key)
        elif value == "":
            empty_envs.append(key)
        else:
            envs[key] = value

    if len(missing_envs) > 0 or len(empty_envs) > 0:
        raise RuntimeError(
            f"Env. variables not found: {','.join(missing_envs)} "
            f"or empty: {','.join(empty_envs)}."
        )
    return envs


def get_vllm_version(base_url: str, timeout: float) -> str:
    """get vLLM version"""

    path = "version"
    url = urljoin(base_url, path)
    response = requests.get(url, timeout=timeout)
    if response.status_code != HTTPStatus.OK:
        raise RuntimeError(f"server {url} error code {response.status_code}.")

    response_json = response.json()
    logger.info("vLLM server version: %s", response_json.get(path))
    return response_json.get(path)


def get_vllm_model(base_url: str, timeout: float) -> str:
    """get vLLM models"""

    path = "/v1/models"
    url = urljoin(base_url, path)
    response = requests.get(url, timeout=timeout)
    if response.status_code != HTTPStatus.OK:
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
    if response.status_code != HTTPStatus.OK:
        raise RuntimeError(f"server {url} error code {response.status_code}.")

    response_json = response.json()
    logger.info("sleep status: %s", response_json.get(path))
    return response_json.get(path)


def sleep(base_url: str, level: int, timeout: float, wait: float):
    """send sleep request"""

    logger.info("sending sleep level %d request with timeout %.1f ...", level, timeout)
    url = urljoin(base_url, "sleep")
    response = requests.post(url, params={"level": str(level)}, timeout=timeout)
    if response.status_code != HTTPStatus.OK:
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
        if elapsed > wait:
            raise RuntimeError(f"Server failed sleeping status after {elapsed} secs.")


def wake(base_url: str, timeout: float, wait: float):
    """send waek request"""

    logger.info("sending wake_up request with timeout %.1f ...", timeout)
    url = urljoin(base_url, "wake_up")
    response = requests.post(url, timeout=timeout)
    if response.status_code != HTTPStatus.OK:
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
        if elapsed > wait:
            raise RuntimeError(f"Server failed sleeping status after {elapsed} secs.")


def find_service_by_cluster_ip(v1: client.CoreV1Api, ip: str):
    """find a clusterip service giving an ip"""
    services = v1.list_service_for_all_namespaces().items
    for svc in services:
        if svc.spec.cluster_ip == ip:
            return svc
    return None


def get_pod_infos(v1: client.CoreV1Api, namespace: str, selector: str) -> list[PodInfo]:
    """get pods by selector"""

    pod_list = v1.list_namespaced_pod(
        namespace=namespace, label_selector=f"app={selector}"
    )
    pod_infos = []
    for pod in pod_list.items:
        pod_info = PodInfo()
        pod_info.name = pod.metadata.name
        pod_info.ip = pod.status.pod_ip
        for container in pod.spec.containers:
            pod_container_info = PodContainerInfo()
            pod_container_info.name = container.name
            pod_container_info.image = container.image
            pod_info.containers.append(pod_container_info)
        pod_infos.append(pod_info)

    return pod_infos


def extract_datetime(log_line: str) -> datetime | None:
    """extracts datetime"""

    match = DATE_PATTERN.search(log_line)
    if match is None:
        return None

    value = match.group()

    # Define the format string that matches the input time string
    time_format = "%Y-%m-%d %H:%M:%S.%f" if "." in value else "%Y-%m-%d %H:%M:%S"

    try:
        # Add current year in front of the string
        value_with_year = f"{datetime.now().year}-{value}"
        return datetime.strptime(value_with_year, time_format)
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
        log_line.timestamp = extract_datetime(log_line.line)
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
        if (  # pylint: disable=too-many-boolean-expressions
            next_category is not None
            and category.end.log_line is not None
            and category.end.log_line.timestamp is not None
            and next_category.start.log_line is not None
            and next_category.start.log_line.timestamp is not None
            and category.end.log_line.timestamp < next_category.start.log_line.timestamp
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
            while category.start.log_line.timestamp is None:
                index += 1
                if index >= len(log_list):
                    return index

                category.start.log_line = log_list[index]

        if category.end.log_line is None and category.end.matches(log_list[index]):
            category.end.log_line = log_list[index]
            # if no date, try next log line
            while category.end.log_line.timestamp is None:
                index += 1
                if index >= len(log_list):
                    return index

                category.end = log_list[index]

        if category.root_child is not None:
            index = populate_benchmark_category(index, log_list, category.root_child)

        category = category.next

    return index


def parse_gpu_logs(scenario: BenchmarkScenario, logs: list[LogLine]) -> None:  # pylint: disable=too-many-locals
    """parse gpu logs"""

    gpu_start = "--- gpu scenario start name:"
    gpu_id = "gpu_uuid='"
    gpu_name = "gpu_name='"
    compute_cap = "compute_cap='"
    persistence_mode = "persistence_mode='"
    gpu_end = "--- gpu scenario end name:"

    # load from start to get gpus scenario
    gpus_scenario_found = False
    for log in logs:
        line = log.line.strip()
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


def convert_objects_to_dict(s: str):
    """
    Converts a string representation of a dict with internal Python objects
    into a real dict, replacing objects with {ClassName: args_dict}.
    """
    # Regex to match ClassName(...) including nested parentheses
    pattern = re.compile(r"(\w+)\((.*?)\)", re.DOTALL)

    def replacer(match):
        class_name = match.group(1)
        args = match.group(2).strip()
        # Use repr() to safely escape any quotes inside the arguments
        return f"{{'{class_name}': {repr(args)}}}"

    # Keep replacing until no more matches (handles nested objects)
    prev_s = None
    while prev_s != s:
        prev_s = s
        s = pattern.sub(replacer, s)

    # Now safely evaluate the string
    return ast.literal_eval(s)


def parse_logs(  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
    scenario: BenchmarkScenario,
    engine: PlatformEngineScenario,
    metrics: BenchmarkVllmMetrics,
    logs: list[LogLine],
) -> None:
    """parse vllm logs"""

    # Strings to be searched on logging ouput in order to extract values

    plugins_init = "plugins/__init__.py"
    available_routes = "Available routes are:"

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
    sleep_gpu_freed = 0.0
    sleep_gpu_in_use = 0.0
    for log in logs:
        line = log.line.strip()

        if metrics.vllm_start_timestamp == 0.0 and plugins_init in line:
            metrics.vllm_start_timestamp = log.timestamp.astimezone(
                timezone.utc
            ).timestamp()

        if (
            available_routes in line
            and metrics.vllm_start_timestamp > 0
            and log.timestamp is not None
        ):
            metrics.vllm_ready_timestamp = log.timestamp.astimezone(
                timezone.utc
            ).timestamp()

        if args is None:
            start_index = line.find(server_non_default_args)
            if start_index >= 0:
                start_index += len(server_non_default_args)
                args = line[start_index:].strip()
                try:
                    engine.args = convert_objects_to_dict(args)
                except Exception:  # pylint: disable=broad-exception-caught
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

        floats = find_floats_in_line(model_gpu_freed, line)
        if len(floats) > 1:
            sleep_gpu_freed = floats[0]
            sleep_gpu_in_use = floats[1]
            continue

        if model_sleep_string in line:
            floats = find_floats_in_line(model_took_string, line)
            if len(floats) > 0:
                metrics.sleep_wake.append(
                    MetricsSleep(
                        log.timestamp.astimezone(timezone.utc).timestamp(),
                        floats[0],
                        sleep_gpu_freed,
                        sleep_gpu_in_use,
                    )
                )
                sleep_gpu_freed = 0.0
                sleep_gpu_in_use = 0.0
                continue

        if model_wake_string in line:
            floats = find_floats_in_line(model_took_string, line)
            if len(floats) > 0:
                metrics.sleep_wake.append(
                    MetricsWake(
                        log.timestamp.astimezone(timezone.utc).timestamp(), floats[0]
                    )
                )
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


def convert_result(
    result_filepath: str,
    output_filepath: str,
    start_time: float,
    stop_time: float,
) -> None:
    """converts result to universal format"""

    try:
        cmd = ["benchmark-report", result_filepath, output_filepath, "-w", "nop", "-f"]
        # Add environment variables for start and stop times in ISO-8601 format
        t_start = (
            datetime.fromtimestamp(start_time, tz=timezone.utc)
            .astimezone()
            .isoformat(timespec="seconds")
        )
        t_stop = (
            datetime.fromtimestamp(stop_time, tz=timezone.utc)
            .astimezone()
            .isoformat(timespec="seconds")
        )
        env = {
            "LLMDBENCH_HARNESS_START": t_start,
            "LLMDBENCH_HARNESS_STOP": t_stop,
            "LLMDBENCH_HARNESS_DELTA": f"PT{stop_time - start_time}S",
        }
        # Create a copy of the existing environment
        custom_env = os.environ.copy()
        # Update with contents of env
        custom_env.update(env)
        with subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            env=custom_env,
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
                logger.info(
                    "benchmark-report succeeded converting: %s", result_filepath
                )

            if err_str != "":
                logger.info("benchmark-report stderr: %s", err_str)
            if out_str != "":
                logger.info("benchmark-report stdout: %s", out_str)
    except Exception:  # pylint: disable=broad-exception-caught
        logger.exception(
            "benchmark-report returned error converting: %s", result_filepath
        )


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
            and category.start.log_line.timestamp is not None
            and category.end.log_line is not None
            and category.end.log_line.timestamp is not None
        ):
            time_difference = (
                category.end.log_line.timestamp - category.start.log_line.timestamp
            )
            elapsed = f"{time_difference.total_seconds():.3f}"

        file.write("\n")
        file.write(f"{blank_string}Log category   : '{category.title}'\n")
        file.write(f"{blank_string}  Process      : '{category.process_desc()}'\n")
        time_format = "%m-%d %H:%M:%S.%f"
        date_str = (
            category.start.log_line.timestamp.strftime(time_format)[:-3]
            if category.start.log_line is not None
            and category.start.log_line.timestamp is not None
            else ""
        )
        file.write(f"{blank_string}  Start date   : '{date_str}'\n")
        date_str = (
            category.end.log_line.timestamp.strftime(time_format)[:-3]
            if category.end.log_line is not None
            and category.end.log_line.timestamp is not None
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
    # --no-enable-log-requests \
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
    if response.status_code not in (HTTPStatus.OK, HTTPStatus.CREATED):
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
    if response.status_code != HTTPStatus.OK:
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


def get_vllm_server_instances(base_url: str, timeout: float) -> list[str]:
    """stop vllm server"""

    url = urljoin(base_url, "v2/vllm/instances")
    response = requests.get(url, timeout=timeout)
    if response.status_code != HTTPStatus.OK:
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

    instance_ids = []
    for instance_status in instances:
        status = instance_status.get("status")
        instance_id = instance_status.get("instance_id")
        instance_ids.append(instance_id)
        logger.info("launcher vllm server instance: %s status: %s", instance_id, status)

    return instance_ids


def wait_for_launcher(base_url: str, timeout: float, wait: float) -> None:
    """wait for launcher to be ready"""
    url = urljoin(base_url, "health")
    start = time.perf_counter()
    while True:
        response_text = None
        try:
            response = requests.get(url, timeout=timeout)
            if response.status_code == HTTPStatus.OK:
                response_text = response.text
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
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.info(
                "launcher health check exception response '%s' '%s'. Trying again ...",
                response_text,
                str(e),
            )

        time.sleep(0.5)
        elapsed = time.perf_counter() - start
        if elapsed > wait:
            raise RuntimeError(f"launcher server failed to start after {elapsed} secs.")


def wait_for_vllm(base_url: str, timeout: float, wait: float) -> None:
    """wait for vllm to be ready"""

    url = urljoin(base_url, "health")
    start = time.perf_counter()
    while True:
        try:
            response = requests.get(url, timeout=timeout)
            if response.status_code == HTTPStatus.OK:
                break
            logger.info(
                "vLLM health check http code '%s' . Trying again ...",
                response.status_code,
            )
        except requests.Timeout:
            logger.info(
                "vLLM health check timed out after '%d' secs. Trying again ...", timeout
            )
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.info("vLLM health check exception '%s'. Trying again ...", str(e))

        time.sleep(0.5)
        elapsed = time.perf_counter() - start
        if elapsed > wait:
            raise RuntimeError(f"vLLM server failed to start after {elapsed} secs.")


def populate_benchmark(  # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
    vllm_info: VllmInfo,
    model: str,
    load_format: LoadFormat,
    base_url: str,
    benchmark_result: BenchmarkResult,
    engine: PlatformEngineScenario,
    requests_dir: str,
    parse_gpus: bool,
    write_log_per_process: bool,
    sleep_wake: bool,
    timeout: float,
    wait: float,
):
    """populate benchmark result"""

    engine.version = get_vllm_version(base_url, timeout)
    benchmark_result.scenario.model.name = model

    vllm_logs = vllm_info.get_vllm_logs()
    log_list = get_log_list(vllm_logs.decode("utf-8"))
    if parse_gpus:
        parse_gpu_logs(benchmark_result.scenario, log_list)

    pod_start = vllm_info.get_pod_start()
    metrics = BenchmarkVllmMetrics()
    metrics.name = engine.name
    metrics.pod_start = pod_start
    parse_logs(
        benchmark_result.scenario,
        engine,
        metrics,
        log_list,
    )
    # if sleep wake request is necessary and sleep mode on and no sleep/wake requests yet
    if (
        sleep_wake
        and benchmark_result.scenario.sleep_mode
        and len(metrics.sleep_wake) < 2
    ):
        logger.info("%s: request sleep/wake", metrics.name)
        sleep(base_url, 1, timeout, wait)
        wake(base_url, timeout, wait)
        # get logs again with latest sleep/wake statistics
        vllm_logs = vllm_info.get_vllm_logs()
        log_list = get_log_list(vllm_logs.decode("utf-8"))
        metrics = BenchmarkVllmMetrics()
        metrics.name = engine.name
        metrics.pod_start = pod_start
        parse_logs(
            benchmark_result.scenario,
            engine,
            metrics,
            log_list,
        )
    benchmark_result.vllm_metrics[metrics.name] = metrics

    # if failed to extract from logs
    if benchmark_result.scenario.load_format == LoadFormat.UNKNOWN:
        logger.info("%s: using load format from env. variable", metrics.name)
        benchmark_result.scenario.load_format = load_format

    log_list_per_process = {}
    if vllm_info.calculate_categories() or write_log_per_process:
        log_list_per_process = get_log_list_per_process(
            benchmark_result.scenario.model.name, log_list
        )
        # categorize logs
        if vllm_info.calculate_categories():
            metrics.root_category = categorize_logs(log_list_per_process)

    output_dir = os.path.join(requests_dir, metrics.name.replace(" ", "_"))
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # write pod log file
    if vllm_info.write_pod_logs():
        pod_logs = vllm_info.get_pod_logs()
        logs_filepath = os.path.join(output_dir, "pod.log")
        with open(logs_filepath, "wb") as file:
            file.write(pod_logs)
            logger.info(
                "%s: pod log file saved to path: %s", metrics.name, logs_filepath
            )

    # write vllm log file
    logs_filepath = os.path.join(output_dir, "vllm.log")
    with open(logs_filepath, "wb") as file:
        file.write(vllm_logs)
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


def benchmark_nop(  # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals,too-many-branches,too-many-statements
    v1: client.CoreV1Api,
    namespace: str,
    endpoint_url: str,
    launcher_port: str,
    launcher_vllm_port: str,
    launcher: bool,
    benchmark_result: BenchmarkResult,
    load_format: LoadFormat,
    requests_dir: str,
    timeout: float,
    wait: float,
    write_log_per_process: bool,
):
    """NOP benchmark"""

    cluster_ip = urlparse(endpoint_url).hostname
    if cluster_ip is None or cluster_ip == "":
        raise RuntimeError(f"Unable to extract hostname from {endpoint_url}.")

    # it should be IP
    svc = find_service_by_cluster_ip(v1, cluster_ip)
    if not svc:
        raise RuntimeError(f"No service found with ClusterIP {cluster_ip}")

    svc_name = svc.metadata.name
    selector = svc.spec.selector
    if svc.metadata.namespace != namespace:
        raise RuntimeError(
            f"Service {svc.metadata.namespace}{svc_name} "
            f"doesn't belong to namespace {namespace}"
        )

    logger.info("Found Service: %s/%s", namespace, svc_name)
    logger.info("Service Selector: %s", selector)

    if not selector or "app" not in selector:
        raise RuntimeError(f"Service {svc_name} does not have an 'app' selector")

    target_port = None
    for p in svc.spec.ports:
        if p.name == "http":
            target_port = p.target_port
            break
    if target_port is None:
        raise RuntimeError(f"Service {svc_name} does not port name 'http'")

    pod_info = None
    endpoint_launcher_url = None
    endpoint_launcher_vllm_url = None
    try:
        pod_infos = get_pod_infos(v1, namespace, selector["app"])
        if len(pod_infos) != 1:
            raise RuntimeError(
                f"{len(pod_infos)} pods found with app selector {selector['app']}. "
                "It should be just 1"
            )
        pod_info = pod_infos[0]
        for container in pod_info.containers:
            logger.info(
                "vLLM standalone pod name: %s container: %s image: %s",
                pod_info.name,
                container.name,
                container.image,
            )
        # use port IP
        endpoint_url = f"http://{pod_info.ip}:{target_port}"
        endpoint_launcher_url = f"http://{pod_info.ip}:{launcher_port}"
        endpoint_launcher_vllm_url = f"http://{pod_info.ip}:{launcher_vllm_port}"
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.info(
            "Skipping harness because vLLM standalone pod not found: %s", str(e)
        )
        return

    for container in pod_info.containers:
        engine = PlatformEngineScenario()
        engine.name = f"{pod_info.name} {container.name}"
        engine.image = container.image
        benchmark_result.scenario.platform.engines[engine.name] = engine

    engine_names = list(benchmark_result.scenario.platform.engines)
    try:
        model = get_vllm_model(endpoint_url, timeout)
        populate_benchmark(
            VllmStandaloneInfo(
                v1, namespace, pod_info.name, pod_info.containers[0].name, wait
            ),
            model,
            load_format,
            endpoint_url,
            benchmark_result,
            benchmark_result.scenario.platform.engines[engine_names[0]],
            requests_dir,
            True,
            write_log_per_process,
            True,
            timeout,
            wait,
        )
    except Exception:  # pylint: disable=broad-exception-caught
        logger.exception("error on benchmark '%s'", engine_names[0])

    # Benchmark launcher if requested
    if launcher and len(engine_names) > 1:
        try:
            logger.info("Benchmark launcher start...")
            wait_for_launcher(endpoint_launcher_url, timeout, wait)
            instance_ids = get_vllm_server_instances(endpoint_launcher_url, timeout)
            instance_id = instance_ids[0] if len(instance_ids) > 0 else None
            if instance_id is None:
                # grab vLLM arguments from standalone
                args = convert_vllm_args(
                    benchmark_result.scenario.platform.engines[engine_names[0]].args,
                    launcher_vllm_port,
                )
                # start vLLM server
                instance_id = start_vllm_server(
                    endpoint_launcher_url,
                    args,
                    timeout,
                )
                wait_for_vllm(endpoint_launcher_vllm_url, timeout, wait)

            model = get_vllm_model(endpoint_launcher_vllm_url, timeout)
            populate_benchmark(
                VllmLauncherInfo(
                    v1,
                    namespace,
                    pod_info.name,
                    pod_info.containers[1].name,
                    wait,
                    endpoint_launcher_url,
                    instance_id,
                ),
                model,
                load_format,
                endpoint_launcher_vllm_url,
                benchmark_result,
                benchmark_result.scenario.platform.engines[engine_names[1]],
                requests_dir,
                False,
                write_log_per_process,
                True,
                timeout,
                wait,
            )
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("error on benchmark '%s'", engine_names[1])
        finally:
            logger.info("Benchmark launcher end")
