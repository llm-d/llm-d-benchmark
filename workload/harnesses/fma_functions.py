"""
Benchmark FMA functions
"""

from __future__ import annotations
from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
from enum import StrEnum
import logging
import os
import time
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse
import requests
from kubernetes import client, watch
from kubernetes.client.exceptions import ApiException

from dpc_log_parser import parse_dpc_log_file
from nop_functions import (
    BenchmarkResult,
    BenchmarkScenario,
    BenchmarkVllmMetrics,
    PlatformEngineScenario,
    get_log_list,
    get_server_status_sleep,
    get_vllm_model,
    parse_logs,
    wait_for_launcher,
    get_vllm_server_instances,
    wait_for_vllm,
    populate_benchmark,
    VllmLauncherInfo,
    LoadFormat,
)

logger = logging.getLogger(__name__)

DUAL_LABEL = "dual-pods.llm-d.ai/dual"
FMA_TIMEOUT = 10.0 * 60.0  # time (seconds) to wait

# Name of the requester container whose start time is the #599 actuation
# baseline. Mirrors FMA pkg/api InferenceServerContainerName.
INFERENCE_SERVER_CONTAINER_NAME = "inference-server"


@dataclass
class FMARequesterInfo:
    """requester info"""

    name: str = ""
    creation_timestamp: float = 0.0
    ready_timestamp: float = 0.0
    dual_label_timestamp: float = 0.0
    container_start_timestamp: float = 0.0
    pod: Any | None = None

    def dump(self) -> dict[str, Any]:
        """Convert FMARequesterInfo to dict.

        Returns:
            dict: Defined fields of FMARequesterInfo.
        """
        dump_dict = {}
        for f in fields(self):
            if f.name != "pod":
                value = getattr(self, f.name)
                dump_dict[f.name] = value

        return dump_dict


@dataclass
class FMALauncherInfo:  # pylint: disable=too-many-instance-attributes
    """Launcher info"""

    v1: client.CoreV1Api | None = None
    namespace: str = ""
    pod_name: str = ""
    container_name: str = ""
    name: str = ""
    requester_info: FMARequesterInfo = field(default_factory=FMARequesterInfo)
    vllm_instance_id: str | None = None
    launcher_endpoint: str = ""
    vllm_endpoint: str = ""
    ttft: float = 0.0
    actuation_condition: FMAActuationCondition | None = None
    launcher_creation_timestamp: float = 0.0
    launcher_node: str = ""
    t_wake: float | None = None
    t_instance_create: float | None = None
    t_cold_launcher: float | None = None
    # Which baseline produced this iteration's actuation timing, one of:
    #   "dpc"                 -- DPC "HTTP call done" log (highest fidelity)
    #   "kube_container_start"-- Kube fallback, requester inference-server
    #                            container state.running.started_at (matches #599)
    #   "kube_pod_create"     -- Kube fallback, container-start unavailable,
    #                            reverted to requester pod creation_timestamp
    timing_source: str = "kube_pod_create"

    @property
    def dpc_timing_available(self) -> bool:
        """Derived convenience: True only when timing came from the DPC log."""
        return self.timing_source == "dpc"

    def dump(self) -> dict[str, Any]:
        """Convert FMALauncherInfo to dict.

        Returns:
            dict: Defined fields of FMALauncherInfo.
        """
        dump_dict = {}
        for f in fields(self):
            if f.name == "v1":
                continue
            value = getattr(self, f.name)
            dump_dict[f.name] = (
                value.dump()
                if hasattr(value, "dump") and callable(value.dump)
                else value
            )

        # dpc_timing_available is a derived property (not a dataclass field), so
        # include it explicitly for backward-compatible downstream readers.
        dump_dict["dpc_timing_available"] = self.dpc_timing_available

        return dump_dict


class FMAActuationCondition(StrEnum):
    """Type of actuation"""

    T_COLD_LAUNCHER = "T_cold_launcher"  # DPC creates new launcher + new vllm
    T_WARM = "T_warm"  # existing launcher creates new vllm
    T_HOT = "T_hot"  # waking sleeping vllm

    def dump(self) -> str:
        """Convert FMAActuationCondition to str.

        Returns:
            str: FMAActuationCondition value.
        """
        return self.value


@dataclass
class FMAMetricsIteration:
    """FMA Metrics Iteration"""

    iteration: int
    launcher_infos: list[FMALauncherInfo]
    hot_hit_rate: float = 0.0
    warm_hit_rate: float = 0.0
    cold_launcher_hit_rate: float = 0.0

    def dump(self) -> dict[str, Any]:
        """Convert FMAMetricsIteration to dict.

        Returns:
            dict: Defined fields of FMAMetricsIteration.
        """
        dump_dict = {}
        for f in fields(self):
            value = getattr(self, f.name)
            if f.name == "launcher_infos":
                dump_list = []
                for v in value:
                    dump_list.append(
                        v.dump() if hasattr(v, "dump") and callable(v.dump) else v
                    )
                dump_dict[f.name] = dump_list
                continue

            dump_dict[f.name] = (
                value.dump()
                if hasattr(value, "dump") and callable(value.dump)
                else value
            )

        return dump_dict


@dataclass
class FMAMetrics:
    """FMA Metrics"""

    name: str = "fma"
    iterations: list[FMAMetricsIteration] = field(
        default_factory=list[FMAMetricsIteration]
    )

    def dump(self) -> dict[str, Any]:
        """Convert FMAMetrics to dict.

        Returns:
            dict: Defined fields of FMAMetrics.
        """
        dump_dict = {}
        for f in fields(self):
            value = getattr(self, f.name)
            if f.name == "iterations":
                dump_list = []
                for v in value:
                    dump_list.append(
                        v.dump() if hasattr(v, "dump") and callable(v.dump) else v
                    )
                dump_dict[f.name] = dump_list
                continue

            dump_dict[f.name] = (
                value.dump()
                if hasattr(value, "dump") and callable(value.dump)
                else value
            )

        return dump_dict


def get_fma_launcher_infos(  # pylint: disable=too-many-locals,too-many-arguments,too-many-positional-arguments
    v1: client.CoreV1Api,
    api,
    requester_infos: list[FMARequesterInfo],
    namespace: str,
    fma_launcher_port: str,
    benchmark_result: BenchmarkResult,
) -> list[FMALauncherInfo]:
    """returns connected launchers info and populates BenchmarResult engine"""

    launcher_infos = []

    for requester_info in requester_infos:
        requester_pod = requester_info.pod
        requester_pod_name = requester_pod.metadata.name

        launcher_pod_name = requester_pod.metadata.labels.get(DUAL_LABEL)
        if launcher_pod_name is None or launcher_pod_name == "":
            logger.info(
                "No launcher pod name found for requester pod '%s'.", requester_pod_name
            )
            continue

        inference_server_config_name = requester_pod.metadata.annotations.get(
            "dual-pods.llm-d.ai/inference-server-config"
        )
        if inference_server_config_name is None or inference_server_config_name == "":
            logger.info(
                "No inference server config name found for requester pod '%s'.",
                requester_pod_name,
            )
            continue

        vllm_port = None
        try:
            inference_server = api.get_namespaced_custom_object(
                group="fma.llm-d.ai",
                version="v1alpha1",
                namespace=namespace,
                plural="inferenceserverconfigs",
                name=inference_server_config_name,
            )
            vllm_port = (
                inference_server.get("spec", {})
                .get("modelServerConfig", {})
                .get("port")
            )
            if vllm_port is None:
                logger.info(
                    "No modelServerConfig port found in inferenceserverconfigs %s",
                    inference_server_config_name,
                )
                continue
        except ApiException:
            logger.exception(
                "error accessing inference server config '%s'",
                inference_server_config_name,
            )
            continue

        launcher_pod_ip = None
        try:
            launcher_pod = client.CoreV1Api().read_namespaced_pod(
                name=launcher_pod_name, namespace=namespace
            )
            launcher_pod_ip = launcher_pod.status.pod_ip
            if launcher_pod_ip is None:
                logger.info("Launcher pod '%s' ip not found.", launcher_pod_name)
                continue

            if len(launcher_pod.spec.containers) > 0:
                container = launcher_pod.spec.containers[0]
                engine = PlatformEngineScenario()
                engine.name = launcher_pod.metadata.name
                engine.image = container.image
                benchmark_result.scenario.platform.engines[engine.name] = engine
                launcher_info = FMALauncherInfo()
                launcher_info.v1 = v1
                launcher_info.namespace = launcher_pod.metadata.namespace
                launcher_info.pod_name = launcher_pod.metadata.name
                launcher_info.container_name = container.name
                launcher_info.name = engine.name
                launcher_info.requester_info = requester_info
                launcher_info.launcher_creation_timestamp = (
                    launcher_pod.metadata.creation_timestamp.astimezone(
                        timezone.utc
                    ).timestamp()
                )
                launcher_info.launcher_node = launcher_pod.spec.node_name or ""
                launcher_info.launcher_endpoint = (
                    f"http://{launcher_pod_ip}:{fma_launcher_port}"
                )
                launcher_info.vllm_endpoint = f"http://{launcher_pod_ip}:{vllm_port}"
                launcher_infos.append(launcher_info)
        except client.ApiException:
            logger.exception("error accessing launcher pod '%s'", launcher_pod_name)
            continue

    return launcher_infos


def is_owned_by_rs(pod, rs_uid):
    """verify that pod is owned by replicaset"""
    for o in pod.metadata.owner_references or []:
        if o.uid == rs_uid:
            return True
    return False


def get_ready_timestamp(pod: Any) -> float:
    """returns pod ready timestemp"""
    if pod.status.phase == "Running":
        for cond in pod.status.conditions or []:
            if cond.type == "Ready" and cond.status == "True":
                return cond.last_transition_time.astimezone(timezone.utc).timestamp()
    return 0.0


def get_dual_label_timestamp(pod: Any) -> float:
    """Return dual label timestamp."""
    if (
        pod.metadata.labels.get(DUAL_LABEL)
        and pod.status.phase == "Running"
        and pod.metadata.deletion_timestamp is None
    ):
        return datetime.now().astimezone(timezone.utc).timestamp()

    return 0.0


def get_container_start_timestamp(pod: Any) -> float:
    """Return the requester inference-server container start time as an epoch.

    Mirrors the controller's #599 baseline, which reads
    ``getContainerStatus(requestingPod, "inference-server").State.Running.StartedAt``.

    Returns the ``state.running.started_at`` epoch (float) for the container
    named ``inference-server``, or 0.0 when the container is missing, not
    running, or its start time is unavailable.
    """
    container_statuses = pod.status.container_statuses
    if not container_statuses:
        return 0.0
    for cs in container_statuses:
        if cs.name != INFERENCE_SERVER_CONTAINER_NAME:
            continue
        state = getattr(cs, "state", None)
        running = getattr(state, "running", None) if state is not None else None
        started_at = getattr(running, "started_at", None) if running else None
        if started_at is None:
            return 0.0
        return started_at.astimezone(timezone.utc).timestamp()
    return 0.0


def select_kube_fallback_baseline(
    requester_info: "FMARequesterInfo",
) -> tuple[float, str]:
    """Choose the Kube-timestamp fallback baseline for an actuation.

    Prefers the requester ``inference-server`` container start time (matching
    the controller's #599 baseline); otherwise reverts to the requester pod
    ``creation_timestamp`` -- a degraded baseline.

    This function is pure (no logging): the tentative ``timing_source`` it
    returns may be overridden later by DPC-log refinement, so any warning about
    a ``kube_pod_create`` reversion is deferred until the final source is known
    (see :func:`warn_on_pod_create_baseline`).

    Returns a ``(baseline_epoch, timing_source)`` tuple where ``timing_source``
    is ``"kube_container_start"`` or ``"kube_pod_create"``.
    """
    if requester_info.container_start_timestamp > 0.0:
        return requester_info.container_start_timestamp, "kube_container_start"

    return requester_info.creation_timestamp, "kube_pod_create"


def warn_on_pod_create_baseline(
    requester_name: str,
    logger_: logging.Logger = logger,
) -> None:
    """Warn that an actuation's FINAL baseline reverted to pod creation time.

    Emitted only for iterations whose final ``timing_source`` is
    ``"kube_pod_create"`` (i.e. neither a container-start Kube fallback nor a
    DPC-log override), so a genuine reversion is never silent while a reversion
    that DPC refinement later moots produces no spurious warning.
    """
    logger_.warning(
        "Requester '%s': inference-server container start time unavailable; "
        "reverted Kube-fallback baseline to pod creation_timestamp "
        "(timing_source=kube_pod_create). This iteration's actuation number is "
        "measured from an earlier baseline than the controller (#599) and may "
        "be overstated.",
        requester_name,
    )


def wait_for_requester_pods(  # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals,too-many-branches,too-many-statements
    v1: client.CoreV1Api,
    namespace: str,
    label_selector: str,
    rs_uid: str | None,
    replicas: int,
    deployment_name: str,
    timeout: float,
) -> list[FMARequesterInfo] | None:
    """
    Watch pods matching a label selector in a namespace.
    Handles dropped streams, retries, and dual-labeled pods.
    """

    # --- Initial list ---
    pods = v1.list_namespaced_pod(
        namespace=namespace, label_selector=label_selector
    ).items

    all_requester_pods = {}
    ready_requester_pods = set()

    for p in pods:
        if rs_uid is not None and not is_owned_by_rs(p, rs_uid):
            continue

        requester_info = FMARequesterInfo()
        requester_info.name = p.metadata.name
        requester_info.creation_timestamp = p.metadata.creation_timestamp.astimezone(
            timezone.utc
        ).timestamp()
        requester_info.ready_timestamp = get_ready_timestamp(p)
        requester_info.dual_label_timestamp = get_dual_label_timestamp(p)
        requester_info.container_start_timestamp = get_container_start_timestamp(p)
        requester_info.pod = p
        all_requester_pods[p.metadata.name] = requester_info

        if (
            requester_info.ready_timestamp > 0.0
            and requester_info.dual_label_timestamp > 0.0
        ):
            ready_requester_pods.add(p.metadata.name)

    logger.info(
        "Initial Requester Pods: %d, Ready: %d Replicas %d",
        len(all_requester_pods),
        len(ready_requester_pods),
        replicas,
    )

    if len(ready_requester_pods) >= replicas:
        logger.info("All requester pods ready initially")
        return [all_requester_pods[name] for name in ready_requester_pods]

    start = time.perf_counter()
    while True:
        # --- Watch pod events ---
        w = watch.Watch()
        try:
            logger.info("Starting watcher for requester pods...")
            for event in w.stream(
                v1.list_namespaced_pod,
                namespace=namespace,
                label_selector=label_selector,
                timeout_seconds=30,
            ):
                pod = event["object"]
                name = pod.metadata.name
                event_type = event["type"]

                if rs_uid is not None and not is_owned_by_rs(pod, rs_uid):
                    continue

                if event_type == "DELETED":
                    all_requester_pods.pop(name, None)
                    ready_requester_pods.discard(name)
                else:
                    requester_info = all_requester_pods.get(name, FMARequesterInfo())
                    requester_info.name = name
                    requester_info.creation_timestamp = (
                        pod.metadata.creation_timestamp.astimezone(
                            timezone.utc
                        ).timestamp()
                    )
                    requester_info.ready_timestamp = get_ready_timestamp(pod)
                    requester_info.container_start_timestamp = (
                        get_container_start_timestamp(pod)
                    )
                    # only calculate if it wasn't already calculated
                    if requester_info.dual_label_timestamp == 0.0:
                        requester_info.dual_label_timestamp = get_dual_label_timestamp(
                            pod
                        )
                    requester_info.pod = pod
                    all_requester_pods[name] = requester_info
                    if (
                        requester_info.ready_timestamp > 0.0
                        and requester_info.dual_label_timestamp > 0.0
                    ):
                        ready_requester_pods.add(name)
                    else:
                        ready_requester_pods.discard(name)

                logger.info(
                    "Watch Requester Pods: %d, Ready: %d Replicas %d",
                    len(all_requester_pods),
                    len(ready_requester_pods),
                    replicas,
                )

                if len(ready_requester_pods) >= replicas:
                    logger.info("All requester pods ready")
                    w.stop()
                    return [all_requester_pods[name] for name in ready_requester_pods]
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.warning(
                "Watcher stream ended unexpectedly: {%s}. Retrying in 1s...", str(e)
            )
            time.sleep(1)
            continue

        elapsed = time.perf_counter() - start
        if elapsed > timeout:
            w.stop()
            logger.info(
                "Timed out waiting for requester %s pods to become ready after %.1f secs.",
                deployment_name,
                elapsed,
            )
            return None


def wait_for_deployment_scale(
    apps_v1: client.AppsV1Api, namespace: str, deployment_name: str, timeout: float
) -> bool:
    """wait for the requester Deployment to reach its desired replica count"""

    start = time.perf_counter()
    while True:
        try:
            dep = apps_v1.read_namespaced_deployment(deployment_name, namespace)
        except ApiException:
            logger.exception(
                "Error reading Deployment '%s:%s'", namespace, deployment_name
            )
            return False

        desired = dep.spec.replicas or 0
        actual = dep.status.replicas or 0

        logger.info(
            "Deployment '%s:%s' replicas actual %d desired %d",
            namespace,
            deployment_name,
            actual,
            desired,
        )
        if actual == desired:
            logger.info(
                "Deployment '%s:%s' replicas actual %d reached",
                namespace,
                deployment_name,
                actual,
            )
            return True

        elapsed = time.perf_counter() - start
        if elapsed > timeout:
            logger.info(
                (
                    "Timed out waiting for Deployment '%s:%s' "
                    "to have the desired replicas %d after %d secs."
                ),
                namespace,
                deployment_name,
                desired,
                elapsed,
            )
            return False
        time.sleep(2)

    return False


def scale_deployment(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    v1: client.CoreV1Api,
    apps_v1: client.AppsV1Api,
    deployment_name: str,
    namespace: str,
    replicas: int,
    timeout: float,
) -> list[FMARequesterInfo] | None:
    """scale the requester Deployment and wait for its pods to be ready."""

    if replicas < 0:
        logger.info("Replicas must be >= 0 and not %d", replicas)
        return None

    # Scale the Deployment
    try:
        apps_v1.patch_namespaced_deployment(
            name=deployment_name,
            namespace=namespace,
            body={"spec": {"replicas": replicas}},
        )
        logger.info(
            "Scaled Deployment '%s:%s' to '%d'", namespace, deployment_name, replicas
        )
    except ApiException:
        logger.exception(
            "Error scaling Deployment '%s:%s' to '%d'",
            namespace,
            deployment_name,
            replicas,
        )
        return None

    if replicas == 0:
        # wait for it to set replicas to 0 and then return
        return (
            []
            if wait_for_deployment_scale(apps_v1, namespace, deployment_name, timeout)
            else None
        )

    label_selector = None
    try:
        dep = apps_v1.read_namespaced_deployment(deployment_name, namespace)
        selector = dep.spec.selector.match_labels
        if not selector:
            logger.info(
                "Deployment '%s:%s' has no match_labels selector.",
                namespace,
                deployment_name,
            )
            return None
        label_selector = ",".join(f"{k}={v}" for k, v in selector.items())
    except ApiException:
        logger.exception("Error reading Deployment '%s:%s'", namespace, deployment_name)
        return None

    return wait_for_requester_pods(
        v1, namespace, label_selector, None, replicas, deployment_name, timeout
    )


def calculate_vllm_ttft(base_url: str, model: str, timeout: float) -> float:
    """calculate vLLM ttft"""

    url = urljoin(base_url, "/v1/completions")
    payload = {
        "model": model,
        "prompt": "Once upon a time,",
        "max_tokens": 50,
        "stream": True,  # enable streaming to detect first token
    }

    headers = {"Content-Type": "application/json"}

    # Send the request and measure TTFT
    try:
        with requests.post(
            url, json=payload, headers=headers, timeout=timeout, stream=True
        ) as response:
            start = time.perf_counter()
            first_token_time = None

            # Iterate over streamed response
            for line in response.iter_lines():
                if line:
                    # Decode the line
                    decoded_line = line.decode("utf-8")
                    if decoded_line.startswith("data:"):
                        token_data = decoded_line[5:].strip()
                        if token_data != "[DONE]":
                            first_token_time = time.perf_counter()
                            break

            if first_token_time:
                ttft = first_token_time - start
                logger.info("TTFT (Time To First vLLM Token): %.4f seconds", ttft)
                return ttft
    except Exception:  # pylint: disable=broad-exception-caught
        logger.exception("Error ocurred when calculating vLLM ttft.")

    logger.info("No vLLM token received.")
    return 0.0


def inspect_vllm_instances(
    instance_ids: list[str], launcher_info: FMALauncherInfo, timeout: float
):
    """gets information for each instance inside the launcher"""

    logger.info("Launcher '%s' info start:", launcher_info.name)
    for instance_id in instance_ids:
        pod_logs = VllmLauncherInfo(
            launcher_info.v1,
            launcher_info.namespace,
            launcher_info.pod_name,
            launcher_info.container_name,
            timeout,
            launcher_info.launcher_endpoint,
            instance_id,
            False,
        ).get_vllm_logs()
        scenario = BenchmarkScenario()
        engine = PlatformEngineScenario()
        metrics = BenchmarkVllmMetrics()
        parse_logs(scenario, engine, metrics, get_log_list(pod_logs.decode("utf-8")))
        port = int(engine.args.get("port", 0))
        logger.info("Instance id '%s' info start:", instance_id)
        logger.info("Arguments: %s", str(engine.args))
        logger.info("Port: %d", port)
        if port != 0:
            parsed = urlparse(launcher_info.vllm_endpoint)
            new_netloc = f"{parsed.hostname}:{port}"
            new_url = urlunparse(parsed._replace(netloc=new_netloc))
            logger.info("URL Endpoint: %s", new_url)
            sleeping = get_server_status_sleep(new_url, timeout)
            logger.info("Sleeping: %s", sleeping)
        logger.info("Instance id '%s' info end.", instance_id)

    logger.info("Launcher '%s' info end.", launcher_info.name)


def write_controller_log(
    v1: client.CoreV1Api, namespace: str, label_selector: str, requests_dir: str
) -> None:
    """write controller logs"""

    try:
        pods = v1.list_namespaced_pod(
            namespace=namespace, label_selector=label_selector
        ).items
        for pod in pods:
            pod_name = pod.metadata.name
            for container in pod.spec.containers:
                container_name = container.name
                response = v1.read_namespaced_pod_log(
                    name=pod_name,
                    container=container_name,
                    namespace=pod.metadata.namespace,
                    pretty=False,
                    _preload_content=False,
                )
                logs_filepath = os.path.join(
                    requests_dir, f"{pod_name}--{container_name}.log"
                )
                with open(logs_filepath, "wb") as file:
                    file.write(response.data)
                    logger.info(
                        "controller pod '%s:%s' container '%s' log file saved to path: %s",
                        pod.metadata.namespace,
                        pod_name,
                        container_name,
                        logs_filepath,
                    )
    except Exception:  # pylint: disable=broad-exception-caught
        logger.exception(
            "Error ocurred when writing logs for controller with label selector '%s'.",
            label_selector,
        )


def benchmark_fma(  # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals,too-many-branches,too-many-statements
    v1: client.CoreV1Api,
    api: client.CustomObjectsApi,
    apps_v1: client.AppsV1Api,
    namespace: str,
    endpoint_url: str,
    fma_launcher_port: str,
    benchmark_result: BenchmarkResult,
    load_format: LoadFormat,
    requests_dir: str,
    iterations: int,
    timeout: float,
    wait: float,
    write_log_per_process: bool,
):
    """FMA benchmark"""

    domain = urlparse(endpoint_url).netloc
    arr = domain.split(".")
    if len(arr) == 0:
        raise RuntimeError(f"Unable to extract deployment name from {domain}.")

    deployment_name = arr[0]
    deployment = None
    try:
        deployment = apps_v1.read_namespaced_deployment(
            name=deployment_name, namespace=namespace
        )
    except ApiException as e:
        raise RuntimeError(f"Unable to read deployment '{deployment_name}'.") from e

    # make sure to start with 0 replicas
    desired = deployment.spec.replicas or 0
    if desired > 0:
        # should start with 0 replicas, scale to it
        if (
            scale_deployment(v1, apps_v1, deployment_name, namespace, 0, FMA_TIMEOUT)
            is None
        ):
            raise RuntimeError(f"Unable to scale deployment {deployment_name} to 0.")

    try:  # pylint: disable=too-many-nested-blocks
        fma_metrics = FMAMetrics()
        benchmark_result.extra_metrics.append(fma_metrics)
        for iteration in range(1, iterations + 1):  # pylint: disable=too-many-nested-blocks
            try:
                logger.info("Benchmark FMA iteration '%d' start...", iteration)
                # scale the requester Deployment to 1
                requester_infos = scale_deployment(
                    v1, apps_v1, deployment_name, namespace, 1, FMA_TIMEOUT
                )
                if requester_infos is None:
                    raise RuntimeError(
                        f"Unable to scale deployment {deployment_name} to 1."
                    )

                launcher_infos = get_fma_launcher_infos(
                    v1,
                    api,
                    requester_infos,
                    namespace,
                    fma_launcher_port,
                    benchmark_result,
                )
                for launcher_info in launcher_infos:
                    try:
                        wait_for_launcher(
                            launcher_info.launcher_endpoint, timeout, wait
                        )
                        instance_ids = get_vllm_server_instances(
                            launcher_info.launcher_endpoint, timeout
                        )
                        inspect_vllm_instances(instance_ids, launcher_info, wait)
                        launcher_info.vllm_instance_id = (
                            instance_ids[-1] if len(instance_ids) > 0 else None
                        )
                        if launcher_info.vllm_instance_id is None:
                            continue

                        wait_for_vllm(launcher_info.vllm_endpoint, timeout, wait)
                        model = get_vllm_model(launcher_info.vllm_endpoint, timeout)
                        launcher_info.ttft = calculate_vllm_ttft(
                            launcher_info.vllm_endpoint,
                            model,
                            timeout,
                        )
                    except Exception as e:
                        raise RuntimeError(
                            f"error on benchmark FMA '{launcher_info.name}' launcher"
                        ) from e

                # scale the requester Deployment back to 0
                if (
                    scale_deployment(
                        v1, apps_v1, deployment_name, namespace, 0, FMA_TIMEOUT
                    )
                    is None
                ):
                    raise RuntimeError(
                        f"Unable to scale deployment {deployment_name} to 0."
                    )

                for launcher_info in launcher_infos:
                    try:
                        if launcher_info.vllm_instance_id is None:
                            continue

                        populate_benchmark(
                            VllmLauncherInfo(
                                launcher_info.v1,
                                launcher_info.namespace,
                                launcher_info.pod_name,
                                launcher_info.container_name,
                                wait,
                                launcher_info.launcher_endpoint,
                                launcher_info.vllm_instance_id,
                                False,
                            ),
                            model,
                            load_format,
                            launcher_info.vllm_endpoint,
                            benchmark_result,
                            benchmark_result.scenario.platform.engines[
                                launcher_info.name
                            ],
                            requests_dir,
                            False,
                            write_log_per_process,
                            False,
                            timeout,
                            wait,
                        )
                        launcher_info.actuation_condition = None
                        if (
                            len(
                                benchmark_result.vllm_metrics[
                                    launcher_info.name
                                ].sleep_wake
                            )
                            >= 2
                        ):
                            sleep = (
                                benchmark_result.vllm_metrics[launcher_info.name]
                                .sleep_wake[-1]
                                .metrics_type()
                                == "sleep"
                            )
                            wake = (
                                benchmark_result.vllm_metrics[launcher_info.name]
                                .sleep_wake[-2]
                                .metrics_type()
                                == "wake"
                            )
                            if sleep and wake:
                                launcher_info.actuation_condition = (
                                    FMAActuationCondition.T_HOT
                                )

                        if launcher_info.actuation_condition is None:
                            if (
                                launcher_info.launcher_creation_timestamp > 0.0
                                and launcher_info.requester_info.creation_timestamp
                                > 0.0
                                and launcher_info.launcher_creation_timestamp
                                < launcher_info.requester_info.creation_timestamp
                            ):
                                launcher_info.actuation_condition = (
                                    FMAActuationCondition.T_WARM
                                )
                            else:
                                launcher_info.actuation_condition = (
                                    FMAActuationCondition.T_COLD_LAUNCHER
                                )

                        # Compute per-path timing (upper bound via Kube timestamps).
                        # Anchor hot/warm actuation on the requester
                        # inference-server container start (matches controller
                        # #599); revert to pod creation_timestamp only when the
                        # container start is unavailable, and record which
                        # baseline was used via timing_source.
                        ready_ts = launcher_info.requester_info.ready_timestamp
                        actuation_baseline, launcher_info.timing_source = (
                            select_kube_fallback_baseline(launcher_info.requester_info)
                        )
                        if (
                            launcher_info.actuation_condition
                            == FMAActuationCondition.T_HOT
                            and ready_ts > 0.0
                        ):
                            launcher_info.t_wake = ready_ts - actuation_baseline
                        elif (
                            launcher_info.actuation_condition
                            == FMAActuationCondition.T_WARM
                            and ready_ts > 0.0
                        ):
                            launcher_info.t_instance_create = (
                                ready_ts - actuation_baseline
                            )
                        elif (
                            launcher_info.actuation_condition
                            == FMAActuationCondition.T_COLD_LAUNCHER
                            and ready_ts > 0.0
                            and launcher_info.launcher_creation_timestamp > 0.0
                        ):
                            launcher_info.t_cold_launcher = (
                                ready_ts - launcher_info.launcher_creation_timestamp
                            )

                    except Exception as e:
                        raise RuntimeError(
                            f"error on benchmark FMA '{launcher_info.name}' launcher"
                        ) from e

                # Compute hit rates for this iteration
                total = len(launcher_infos)
                hot_count = sum(
                    li.actuation_condition == FMAActuationCondition.T_HOT
                    for li in launcher_infos
                )
                warm_count = sum(
                    li.actuation_condition == FMAActuationCondition.T_WARM
                    for li in launcher_infos
                )
                cold_count = sum(
                    li.actuation_condition == FMAActuationCondition.T_COLD_LAUNCHER
                    for li in launcher_infos
                )
                fma_metrics_iteration = FMAMetricsIteration(
                    iteration,
                    launcher_infos,
                    hot_hit_rate=hot_count / total if total > 0 else 0.0,
                    warm_hit_rate=warm_count / total if total > 0 else 0.0,
                    cold_launcher_hit_rate=cold_count / total if total > 0 else 0.0,
                )
                fma_metrics.iterations.append(fma_metrics_iteration)
            finally:
                logger.info("Benchmark FMA iteration '%d' end.", iteration)
    finally:
        write_controller_log(
            v1,
            namespace,
            "app.kubernetes.io/component=dual-pods-controller",
            requests_dir,
        )
        write_controller_log(
            v1,
            namespace,
            "app.kubernetes.io/component=launcher-populator",
            requests_dir,
        )

        # Refine per-path timing using DPC log parsing (tighter than Kube timestamps)
        dpc_records = parse_dpc_log_file(requests_dir)
        if dpc_records:
            logger.info(
                "DPC log parsed: %d requester timing records found.",
                len(dpc_records),
            )
            for fma_iter in fma_metrics.iterations:
                for launcher_info in fma_iter.launcher_infos:
                    requester_name = launcher_info.requester_info.name
                    rec = dpc_records.get(requester_name)
                    if rec is None:
                        logger.debug(
                            "No DPC timing record for requester '%s'.",
                            requester_name,
                        )
                        continue

                    if launcher_info.actuation_condition == FMAActuationCondition.T_HOT:
                        refined = rec.t_hot()
                        if refined is not None:
                            logger.info(
                                "Requester '%s': T_hot refined %.3fs -> %.3fs",
                                requester_name,
                                launcher_info.t_wake or 0.0,
                                refined,
                            )
                            launcher_info.t_wake = refined
                            launcher_info.timing_source = "dpc"
                    elif (
                        launcher_info.actuation_condition
                        == FMAActuationCondition.T_WARM
                    ):
                        refined = rec.t_instance_create()
                        if refined is not None:
                            logger.info(
                                "Requester '%s': T_instance_create refined %.3fs -> %.3fs",
                                requester_name,
                                launcher_info.t_instance_create or 0.0,
                                refined,
                            )
                            launcher_info.t_instance_create = refined
                            launcher_info.timing_source = "dpc"
                    elif (
                        launcher_info.actuation_condition
                        == FMAActuationCondition.T_COLD_LAUNCHER
                    ):
                        refined = rec.t_cold_launcher()
                        if refined is not None:
                            logger.info(
                                "Requester '%s': T_cold_launcher refined %.3fs -> %.3fs",
                                requester_name,
                                launcher_info.t_cold_launcher or 0.0,
                                refined,
                            )
                            launcher_info.t_cold_launcher = refined
                            launcher_info.timing_source = "dpc"
        else:
            logger.info(
                "No DPC timing records found; using Kube-timestamp upper bounds."
            )

        # Now that DPC refinement has run, warn only for iterations whose FINAL
        # timing_source is kube_pod_create -- so a reversion that DPC refinement
        # overrode to "dpc" does not emit a spurious "may be overstated" warning.
        for fma_iter in fma_metrics.iterations:
            for launcher_info in fma_iter.launcher_infos:
                if launcher_info.timing_source == "kube_pod_create":
                    warn_on_pod_create_baseline(launcher_info.requester_info.name)
