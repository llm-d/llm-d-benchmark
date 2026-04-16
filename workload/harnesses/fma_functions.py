"""
Benchmark FMA functions
"""

from __future__ import annotations
from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
import logging
import time
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse
import requests
from kubernetes import client, watch
from kubernetes.client.exceptions import ApiException

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
    VllmLaunchedLogs,
    LoadFormat,
)

logger = logging.getLogger(__name__)

DUAL_LABEL = "dual-pods.llm-d.ai/dual"
FMA_TIMEOUT = 15.0 * 60.0  # time (seconds) to wait


@dataclass
class FMARequesterInfo:
    """requester info"""

    name: str = ""
    creation_timestamp: float = 0.0
    ready_timestamp: float = 0.0
    dual_label_timestamp: float = 0.0
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
class FMALauncherInfo:
    """Launcher info"""

    name: str = ""
    requester_info: FMARequesterInfo = field(default_factory=FMARequesterInfo)
    vllm_instance_id: str | None = None
    launcher_endpoint: str = ""
    vllm_endpoint: str = ""
    ttft: float = 0.0

    def dump(self) -> dict[str, Any]:
        """Convert FMALauncherInfo to dict.

        Returns:
            dict: Defined fields of FMALauncherInfo.
        """
        dump_dict = {}
        for f in fields(self):
            if f.name == "vllm_instance_id":
                continue
            value = getattr(self, f.name)
            dump_dict[f.name] = (
                value.dump()
                if hasattr(value, "dump") and callable(value.dump)
                else value
            )

        return dump_dict


@dataclass
class FMAMetricsIteration:
    """FMA Metrics Iteration"""

    iteration: int = 0
    launcher_infos: list[FMALauncherInfo] = field(default_factory=list[FMALauncherInfo])

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


def get_fma_launcher_infos(  # pylint: disable=too-many-locals
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
                launcher_info.name = engine.name
                launcher_info.requester_info = requester_info
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


def wait_for_requester_pods(  # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals,too-many-branches,too-many-statements
    v1: client.CoreV1Api,
    namespace: str,
    label_selector: str,
    rs_uid: str,
    replicas: int,
    replicaset_name: str,
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
        if not is_owned_by_rs(p, rs_uid):
            continue

        requester_info = FMARequesterInfo()
        requester_info.name = p.metadata.name
        requester_info.creation_timestamp = p.metadata.creation_timestamp.astimezone(
            timezone.utc
        ).timestamp()
        requester_info.ready_timestamp = get_ready_timestamp(p)
        requester_info.dual_label_timestamp = get_dual_label_timestamp(p)
        requester_info.pod = p
        all_requester_pods[p.metadata.name] = requester_info

        if (
            requester_info.ready_timestamp > 0.0
            and requester_info.dual_label_timestamp > 0.0
        ):
            ready_requester_pods.add(p.metadata.name)

    logger.info(
        "Initial ReplicaSet Pods: %d, Ready: %d Replicas %d",
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

                if not is_owned_by_rs(pod, rs_uid):
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
                    "Watch ReplicaSet Pods: %d, Ready: %d Replicas %d",
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
                replicaset_name,
                elapsed,
            )
            return None


def wait_for_replicaset_scale(
    apps_v1: client.AppsV1Api, namespace: str, replicaset_name: str, timeout: float
) -> bool:
    """wait for replicaset to scale"""

    start = time.perf_counter()
    while True:
        try:
            rs = apps_v1.read_namespaced_replica_set(replicaset_name, namespace)
        except ApiException:
            logger.exception(
                "Error reading ReplicatSet '%s:%s'", namespace, replicaset_name
            )
            return False

        desired = rs.spec.replicas or 0
        actual = rs.status.replicas or 0

        logger.info(
            "ReplicatSet '%s:%s' replicas actual %d desired %d",
            namespace,
            replicaset_name,
            actual,
            desired,
        )
        if actual == desired:
            logger.info(
                "ReplicatSet '%s:%s' replicas actual %d reached",
                namespace,
                replicaset_name,
                actual,
            )
            return True

        elapsed = time.perf_counter() - start
        if elapsed > timeout:
            logger.info(
                (
                    "Timed out waiting for ReplicatSet '%s:%s' "
                    "to have the desired replicas %d after %d secs."
                ),
                namespace,
                replicaset_name,
                desired,
                elapsed,
            )
            return False
        time.sleep(2)

    return False


def scale_replicaset(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    v1: client.CoreV1Api,
    apps_v1: client.AppsV1Api,
    replicaset_name: str,
    namespace: str,
    replicas: int,
    timeout: float,
) -> list[FMARequesterInfo] | None:
    """scale ReplicaSet and wait for pods to be ready"""

    if replicas < 0:
        logger.info("Replicas must be >= 0 and not %d", replicas)
        return None

    # Scale ReplicaSet
    try:
        apps_v1.patch_namespaced_replica_set(
            name=replicaset_name,
            namespace=namespace,
            body={"spec": {"replicas": replicas}},
        )
        logger.info(
            "Scaled ReplicaSet '%s:%s' to '%d'", namespace, replicaset_name, replicas
        )
    except ApiException:
        logger.exception(
            "Error scaling ReplicatSet '%s:%s' to '%d'",
            namespace,
            replicaset_name,
            replicas,
        )
        return None

    if replicas == 0:
        # wait fot it to set replicas to 0 and then return
        return (
            []
            if wait_for_replicaset_scale(apps_v1, namespace, replicaset_name, timeout)
            else None
        )

    label_selector = None
    rs_uid = None
    try:
        rs = apps_v1.read_namespaced_replica_set(replicaset_name, namespace)
        selector = rs.spec.selector.match_labels
        if not selector:
            logger.info(
                "ReplicaSet '%s:%s' has no match_labels selector.",
                namespace,
                replicaset_name,
            )
            return None
        label_selector = ",".join(f"{k}={v}" for k, v in selector.items())
        rs_uid = rs.metadata.uid
    except ApiException:
        logger.exception(
            "Error reading ReplicatSet '%s:%s'", namespace, replicaset_name
        )
        return None

    return wait_for_requester_pods(
        v1, namespace, label_selector, rs_uid, replicas, replicaset_name, timeout
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
        pod_logs = VllmLaunchedLogs(
            launcher_info.launcher_endpoint, instance_id, timeout, False
        ).get_logs()
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


def benchmark_fma(  # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals,too-many-branches
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
        raise RuntimeError(f"Unable to extract replicaset name from {domain}.")

    replicaset_name = arr[0]
    replicaset = None
    try:
        replicaset = apps_v1.read_namespaced_replica_set(
            name=replicaset_name, namespace=namespace
        )
    except ApiException as e:
        raise RuntimeError(f"Unable to read replicaset '{replicaset_name}'.") from e

    # make sure to start with 0 replicas
    desired = replicaset.spec.replicas or 0
    if desired > 0:
        # should start with 0 replicas, scale to it
        if (
            scale_replicaset(v1, apps_v1, replicaset_name, namespace, 0, FMA_TIMEOUT)
            is None
        ):
            raise RuntimeError(f"Unable to scale replicaset {replicaset_name} to 0.")

    fma_metrics = FMAMetrics()
    benchmark_result.extra_metrics.append(fma_metrics)
    for iteration in range(1, iterations + 1):
        try:
            logger.info("Benchmark FMA iteration '%d' start...", iteration)
            # scale replicaset to 1
            requester_infos = scale_replicaset(
                v1, apps_v1, replicaset_name, namespace, 1, FMA_TIMEOUT
            )
            if requester_infos is None:
                raise RuntimeError(
                    f"Unable to scale replicaset {replicaset_name} to 1."
                )

            launcher_infos = get_fma_launcher_infos(
                api,
                requester_infos,
                namespace,
                fma_launcher_port,
                benchmark_result,
            )
            for launcher_info in launcher_infos:
                try:
                    wait_for_launcher(launcher_info.launcher_endpoint, timeout, wait)
                    instance_ids = get_vllm_server_instances(
                        launcher_info.launcher_endpoint, timeout
                    )
                    inspect_vllm_instances(instance_ids, launcher_info, timeout)
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

            # scale replicaset to 0
            if (
                scale_replicaset(
                    v1, apps_v1, replicaset_name, namespace, 0, FMA_TIMEOUT
                )
                is None
            ):
                raise RuntimeError(
                    f"Unable to scale replicaset {replicaset_name} to 0."
                )

            for launcher_info in launcher_infos:
                try:
                    if launcher_info.vllm_instance_id is None:
                        continue

                    populate_benchmark(
                        VllmLaunchedLogs(
                            launcher_info.launcher_endpoint,
                            launcher_info.vllm_instance_id,
                            timeout,
                            False,
                        ),
                        model,
                        load_format,
                        launcher_info.vllm_endpoint,
                        benchmark_result,
                        benchmark_result.scenario.platform.engines[launcher_info.name],
                        requests_dir,
                        False,
                        write_log_per_process,
                        False,
                        timeout,
                        wait,
                    )
                except Exception as e:
                    raise RuntimeError(
                        f"error on benchmark FMA '{launcher_info.name}' launcher"
                    ) from e

            fma_metrics_iteration = FMAMetricsIteration()
            fma_metrics_iteration.iteration = iteration
            fma_metrics_iteration.launcher_infos = launcher_infos
            fma_metrics.iterations.append(fma_metrics_iteration)
        finally:
            logger.info("Benchmark FMA iteration '%d' end.", iteration)
