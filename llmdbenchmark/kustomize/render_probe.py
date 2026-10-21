"""Check that a guide's render Service can tokenize through its serving pods."""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from llmdbenchmark.executor.command import CommandExecutor
    from llmdbenchmark.executor.context import ExecutionContext


# Run inside a ready backend pod so the request uses the same cluster DNS and
# Service path as EPP. Discover the deployed model from vLLM rather than the
# scenario YAML: kustomize mode ignores that YAML's model configuration.
_RENDER_PROBE_SCRIPT = """\
import json
import sys
from urllib import request

base_url = sys.argv[1].rstrip("/")
client = request.build_opener(request.ProxyHandler({}))
with client.open(f"{base_url}/v1/models", timeout=10) as response:
    models = json.load(response).get("data", [])
if not models or not models[0].get("id"):
    raise RuntimeError("render Service did not return a model ID")

model = models[0]["id"]
payload = json.dumps({"model": model, "prompt": "render check", "max_tokens": 1}).encode()
render_request = request.Request(
    f"{base_url}/v1/completions/render",
    data=payload,
    headers={"Content-Type": "application/json"},
)
with client.open(render_request, timeout=10) as response:
    rendered = json.load(response)
if not isinstance(rendered, list) or not rendered or not rendered[0].get("token_ids"):
    raise RuntimeError("render Service did not return token IDs")
print(f"model={model} token_count={len(rendered[0]['token_ids'])}")
"""


def probe_render_service(
    cmd: CommandExecutor,
    context: ExecutionContext,
    guide_name: str,
    namespace: str,
) -> str | None:
    """Return an error if ``<guide>-render`` cannot produce token IDs."""
    if context.dry_run:
        context.logger.log_info("[render] dry run: skipping render Service probe")
        return None

    service_name = f"{guide_name.split('/')[-1]}-render"
    service_result = cmd.kube(
        "get", "service", service_name, "-n", namespace, "-o", "json", check=False
    )
    if not service_result.success:
        return (
            f"Render Service {service_name} is unavailable: "
            f"{service_result.stderr.strip()}"
        )
    try:
        service = json.loads(service_result.stdout)
        spec = service["spec"]
        selector = spec["selector"]
        ports = spec["ports"]
        port = next(
            (entry["port"] for entry in ports if entry.get("name") == "render-http"),
            ports[0]["port"],
        )
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        return f"Render Service {service_name} has invalid configuration: {exc}"
    if not selector:
        return f"Render Service {service_name} has no pod selector"

    label_selector = ",".join(
        f"{key}={value}" for key, value in sorted(selector.items())
    )
    pods_result = cmd.kube(
        "get", "pods", "-n", namespace, "-l", label_selector, "-o", "json", check=False
    )
    if not pods_result.success:
        return (
            f"Could not list render Service {service_name} backends: "
            f"{pods_result.stderr.strip()}"
        )
    try:
        pods = json.loads(pods_result.stdout)["items"]
    except (KeyError, TypeError, ValueError) as exc:
        return f"Could not read render Service {service_name} backends: {exc}"
    ready_pods = [
        pod
        for pod in pods
        if any(
            condition.get("type") == "Ready" and condition.get("status") == "True"
            for condition in pod.get("status", {}).get("conditions", [])
        )
    ]
    if not ready_pods:
        return f"Render Service {service_name} has no ready backend pods"

    pod = ready_pods[0]
    pod_name = pod["metadata"]["name"]
    containers = pod["spec"]["containers"]
    container = next(
        (item["name"] for item in containers if item["name"] == "modelserver"),
        containers[0]["name"],
    )
    base_url = f"http://{service_name}:{port}"

    for attempt in range(5):
        result = cmd.kube_exec(
            pod_name,
            "python",
            "-c",
            _RENDER_PROBE_SCRIPT,
            base_url,
            namespace=namespace,
            container=container,
            check=False,
            timeout=30,
        )
        if result.success:
            context.logger.log_info(
                f"[render] {service_name} returned token IDs ({result.stdout.strip()})"
            )
            return None
        if attempt < 4:
            time.sleep(2)

    detail = (result.stderr or result.stdout).strip()
    return f"Render Service {service_name} did not return token IDs: {detail[-500:]}"
