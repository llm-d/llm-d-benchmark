#!/usr/bin/env python3

"""
Startup logs benchmark
"""

from __future__ import annotations
from dataclasses import dataclass, fields
from datetime import datetime
from enum import StrEnum
import io
import json
import os
import re
import time
import logging
from typing import Any
from urllib.parse import urljoin, urlparse
import pandas
import requests

from kubernetes import client, config

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 60.0  # time (seconds) to wait for request
MAX_VLLM_WAIT = 15.0 * 60.0  # time (seconds) to wait for vllm to respond

DEFINED_CATEGORIES = [
    {
        "title": "Detect Platform",
        "start": "No plugins for group",
        "end": "detected platform",
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
class LogCategory:
    """Log category"""

    key: int = 0
    title: str = ""
    start_time: datetime | None = None
    end_time: datetime | None = None
    start: str = ""
    end: str = ""
    start_line: str = ""
    end_line: str = ""
    next: LogCategory | None = None
    parent: LogCategory | None = None
    root_child: LogCategory | None = None

    @staticmethod
    def header() -> list[str]:
        """csv header"""
        return [
            "key",
            "title",
            "parent",
            "elapsed",
        ]

    def row(self) -> list[str]:
        """csv row"""
        elapsed = ""
        if self.start_time is not None and self.end_time is not None:
            time_difference = self.end_time - self.start_time
            elapsed = f"{time_difference.total_seconds():.3f}"
        return [
            f"{self.key}",
            self.title,
            f"{self.parent.key}" if self.parent is not None else "",
            elapsed,
        ]


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


@dataclass
class LogResult:
    """Results of one benchmark run"""

    time: str = ""
    vllm_version: str = ""
    sleep_mode: bool = False
    model: str = ""
    load_format: LoadFormat = LoadFormat.UNKNOWN
    load_time: float = 0.0
    size: float = 0.0
    sleep: float = 0.0
    gpu_freed: float = 0.0
    gpu_in_use: float = 0.0
    wake: float = 0.0

    @staticmethod
    def header() -> list[str]:
        """csv header"""
        header = []
        for f in fields(LogResult):
            header.append(f.name)

        return header

    def row(self) -> list[Any]:
        """csv row"""
        row = []
        for name in LogResult.header():
            row.append(getattr(self, name))

        return row


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


def get_vllm_pod_name(
    v1: client.CoreV1Api, namespace: str, deployment_name: str
) -> str:
    """get vllm pod name"""

    selectors = get_deployment_selectors(namespace, deployment_name)
    if len(selectors) == 0:
        raise RuntimeError(
            f"No deployment selectors for deployment {deployment_name} on namespace {namespace}."
        )

    selector = selectors[0]
    pod_names = get_pod_names(v1, namespace, selector)
    if len(pod_names) == 0:
        raise RuntimeError(
            f"No pods found on namespace {namespace} with selector 'app={selector}'."
        )

    return pod_names[0]


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


def get_pod_names(v1: client.CoreV1Api, namespace: str, selector: str) -> list[str]:
    """get pods by selector"""

    pod_list = v1.list_namespaced_pod(
        namespace=namespace, label_selector=f"app={selector}"
    )
    pod_names = []
    for pod in pod_list.items:
        pod_names.append(pod.metadata.name)

    return pod_names


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


def initialize_log_categories(
    key: list[int], defined_categories: list[Any], parent: LogCategory
) -> LogCategory:
    """initialize categories"""
    root_log_category = None
    prev_log_category = None
    for defined_category in defined_categories:
        log_category = LogCategory()
        log_category.key = key[0]
        key[0] = log_category.key + 1
        if root_log_category is None:
            root_log_category = log_category
        if prev_log_category is not None:
            prev_log_category.next = log_category
        prev_log_category = log_category

        log_category.title = defined_category.get("title")
        log_category.start = defined_category.get("start")
        log_category.end = defined_category.get("end")
        log_category.parent = parent
        if log_category.parent is not None and log_category.parent.root_child is None:
            log_category.parent.root_child = log_category

        defined_children = defined_category.get("children")
        if defined_children is not None:
            _ = initialize_log_categories(key, defined_children, log_category)

    return root_log_category


def categorize_logs(vllm_model: str, logs: str) -> LogCategory:
    """parse logs and categorize it"""

    key = [1]
    root_log_category = initialize_log_categories(key, DEFINED_CATEGORIES, None)
    populate_log_categories(vllm_model, logs, root_log_category)
    # add uncategorized categories
    add_uncategorized_categories(key, root_log_category)
    return root_log_category


def populate_log_categories(vllm_model: str, logs: str, root_log_category: LogCategory):
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
                index,
                log_list[index],
            )
            break

        idx += 1

    while index < len(log_list):
        index = populate_log_category(index, log_list, root_log_category)
        index += 1


def add_uncategorized_categories(key: list[int], log_category: LogCategory):
    """add filler uncategorized categories"""

    category = log_category
    while category is not None:
        if category.root_child is not None:
            add_uncategorized_categories(key, category.root_child)

        # if exists a gap, create uncategorized
        next_category = category.next
        if (
            next_category is not None
            and category.end_time is not None
            and next_category.start_time is not None
            and category.end_time < next_category.start_time
        ):
            log_category = LogCategory()
            log_category.key = key[0]
            key[0] = log_category.key + 1
            log_category.title = "Uncategorized"
            log_category.start_time = category.end_time
            log_category.end_time = next_category.start_time
            log_category.parent = category.parent
            log_category.next = next_category
            category.next = log_category
            # skip the uncategorized created category
            category = category.next

        category = category.next


def populate_log_category(
    index: int, log_list: list[str], log_category: LogCategory
) -> int:
    """populate category from log line"""

    category = log_category
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

        if category.root_child is not None:
            index = populate_log_category(index, log_list, category.root_child)

        category = category.next

    return index


def parse_logs(logs: str) -> LogResult:
    """parse vllm logs"""

    # Strings to be searched on logging ouput in order to extract values

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

    log_result = LogResult()
    log_result.time = datetime.now().astimezone().isoformat()

    # loop from the bottom to catch latest statistics before old ones
    sleep_mode = ""
    for line in reversed(logs.splitlines()):
        if (
            sleep_mode != ""
            and log_result.load_format != LoadFormat.UNKNOWN
            and log_result.load_time != 0
            and log_result.sleep != 0
            and log_result.gpu_freed != 0
            and log_result.gpu_in_use != 0
            and log_result.wake != 0
        ):
            break

        line = line.strip()

        if sleep_mode == "":
            start_index = line.find(model_sleep_mode)
            if start_index >= 0:
                start_index += len(model_sleep_mode)
                end_index = line.find(",", start_index)
                if end_index < 0:
                    end_index = line.find("}", start_index)
                if end_index >= 0:
                    sleep_mode = line[start_index:end_index].strip().lower()
                    log_result.sleep_mode = "true" == sleep_mode

        if log_result.load_format == LoadFormat.UNKNOWN:
            start_index = line.find(model_load_format)
            if start_index >= 0:
                start_index += len(model_load_format)
                end_index = line.find(",", start_index)
                if end_index >= 0:
                    format_name = line[start_index:end_index].strip()
                    for f in LoadFormat:
                        if f.name == format_name:
                            log_result.load_format = f
                            break

        if log_result.load_time == 0:
            floats = find_floats_in_line(model_load_string, line)
            if len(floats) > 1:
                log_result.size = floats[0]
                log_result.load_time = floats[1]
                continue

        if log_result.sleep == 0 and model_sleep_string in line:
            floats = find_floats_in_line(model_took_string, line)
            if len(floats) > 0:
                log_result.sleep = floats[0]
                continue

        if log_result.gpu_freed == 0:
            floats = find_floats_in_line(model_gpu_freed, line)
            if len(floats) > 1:
                log_result.gpu_freed = floats[0]
                log_result.gpu_in_use = floats[1]
                continue

        if log_result.wake == 0 and model_wake_string in line:
            floats = find_floats_in_line(model_took_string, line)
            if len(floats) > 0:
                log_result.wake = floats[0]
                continue

    return log_result


def find_floats_in_line(key: str, line: str) -> list[float]:
    """find fload numbers in log line"""
    index = line.find(key)
    if index >= 0:
        return extract_floats(line[index:])

    return []


def extract_floats(text) -> list[float]:
    """extracts all float numbers from a string"""
    return [float(num) for num in re.findall(r"[-+]?\d*\.\d+|\d+", text)]


def read_log_results_from_csv(file_path: str) -> list[LogResult]:
    """read csv log results"""

    log_results = []
    if not os.path.isfile(file_path):
        logger.info("no csv file found on path: %s", file_path)
        return log_results

    df = pandas.read_csv(file_path, encoding="utf-8")

    log_result_header = LogResult.header()

    for _, row in df.iterrows():
        log_result = LogResult()
        for name in log_result_header:
            value = row.get(name)
            if value is not None:
                setattr(log_result, name, value)
        log_results.append(log_result)

    logger.info("csv file found on path: %s", file_path)
    return log_results


def write_log_results_to_csv(log_results: list[LogResult], file_path: str):
    """writes csv log results"""

    data = []
    for log_result in log_results:
        data.append(log_result.row())

    df = pandas.DataFrame(data, columns=LogResult.header())
    df.to_csv(file_path, index=False, encoding="utf-8")
    logger.info("csv file saved to path: %s", file_path)


def write_log_categories_to_csv(log_category: LogCategory, file: io.BufferedWriter):
    """writes csv log results"""

    category = log_category
    while category is not None:
        file.write(f"{','.join(category.row())}\n")
        if category.root_child is not None:
            write_log_categories_to_csv(category.root_child, file)
        category = category.next


def write_log_categories_to_log(log_category: LogCategory, file: io.BufferedWriter):
    """write logs category tree"""
    category = log_category
    while category is not None:
        elapsed = ""
        if category.start_time is not None and category.end_time is not None:
            time_difference = category.end_time - category.start_time
            elapsed = f"{time_difference.total_seconds():.2f}"

        file.write(f"Log category : {category.key} '{category.title}'\n")
        parent_key = f"{category.parent.key}" if category.parent is not None else ""
        file.write(f"   parent    : {parent_key}\n")
        time_format = "%m-%d %H:%M:%S.%f"
        date_str = (
            category.start_time.strftime(time_format)[:-3]
            if category.start_time is not None
            else ""
        )
        file.write(f"   start date: {date_str}\n")
        date_str = (
            category.end_time.strftime(time_format)[:-3]
            if category.end_time is not None
            else ""
        )
        file.write(f"   end date  : {date_str}\n")
        file.write(f"   elapsed   : {elapsed}\n")
        file.write(f"   start     : {category.start}\n")
        file.write(f"   end       : {category.end}\n")
        file.write(f"   start line: {category.start_line}\n")
        file.write(f"   end line. : {category.end_line}\n")
        if category.root_child is not None:
            write_log_categories_to_log(category.root_child, file)
        category = category.next


def main():
    """main entry point"""

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
    domain = urlparse(endpoint_url).netloc
    arr = domain.split(".")
    if len(arr) == 0:
        raise RuntimeError(f"Unable to extract service name from {domain}.")

    # Load Kubernetes configuration
    config.load_kube_config()

    v1 = client.CoreV1Api()

    pod_name = ""
    try:
        pod_name = get_vllm_pod_name(v1, namespace, arr[0])
        logger.info("vLLM standalone pod name: %s", pod_name)
    except Exception as e:
        logger.info(
            "Skipping harness because vLLM standalone pod not found: %s", str(e)
        )
        return

    vllm_version = get_vllm_version(endpoint_url, REQUEST_TIMEOUT)
    vllm_model = get_vllm_model(endpoint_url, REQUEST_TIMEOUT)

    pod_logs = get_pod_logs(v1, namespace, pod_name)
    log_result = parse_logs(pod_logs.decode("utf-8"))
    if log_result.sleep_mode:
        logger.info("Request sleep/wake")
        sleep(endpoint_url, 1, REQUEST_TIMEOUT)
        wake(endpoint_url, REQUEST_TIMEOUT)
        # get logs again with latest sleep/wake statistics
        pod_logs = get_pod_logs(v1, namespace, pod_name)
        log_result = parse_logs(pod_logs.decode("utf-8"))

    log_result.vllm_version = vllm_version
    log_result.model = vllm_model

    # categorize logs
    root_log_category = categorize_logs(vllm_model, pod_logs.decode("utf-8"))

    os.makedirs(requests_dir, exist_ok=True)

    # write vllm log file
    logs_filepath = os.path.join(requests_dir, "vllm.log")
    with open(logs_filepath, "wb") as file:
        file.write(pod_logs)
        logger.info("vllm log file saved to path: %s", logs_filepath)

    cvs_filepath = os.path.join(requests_dir, "nop.csv")

    # read possible existent csv file
    log_results = read_log_results_from_csv(cvs_filepath)

    # append new result to list
    log_results.append(log_result)

    # write log results csv file
    write_log_results_to_csv(log_results, cvs_filepath)

    # write log categories csv file
    csv_categories_filepath = os.path.join(requests_dir, "nop_categories.csv")
    with open(csv_categories_filepath, "w", encoding="utf-8", newline="") as file:
        file.write(f"{','.join(LogCategory.header())}\n")
        write_log_categories_to_csv(root_log_category, file)
        logger.info("csv categories file saved to path: %s", csv_categories_filepath)

    # write log categories log file
    log_categories_filepath = os.path.join(requests_dir, "nop_categories.log")
    with open(log_categories_filepath, "w", encoding="utf-8", newline="") as file:
        write_log_categories_to_log(root_log_category, file)
        logger.info("log categories file saved to path: %s", log_categories_filepath)


if __name__ == "__main__":
    try:
        logger.info("Starting harness run")
        main()
    except Exception:
        logger.exception("Error running harness")
    finally:
        logger.info("End harness run")
