"""Step 09 -- Make the model endpoint reachable from outside the cluster.

Gated on the top-level ``externally_accessible`` value (scenario/defaults,
default ``true``) and common to every Kubernetes deploy method. The step
finds the Service that fronts the model for the active method, then:

- OpenShift: reuses a Route already pointing at that Service, or creates one
  with ``oc expose``, and reports its URL.
- Kubernetes: starts a detached ``kubectl port-forward`` to that Service on a
  free local port and reports ``http://localhost:<port>``. The process
  outlives standup; its PID and log are in the workspace.

Best-effort: a failure only warns, since the in-cluster endpoint still works.
"""

from __future__ import annotations

import json
import socket
import subprocess
import threading
import time
from pathlib import Path

from llmdbenchmark.executor.command import CommandExecutor
from llmdbenchmark.executor.context import ExecutionContext, is_fma_only_mode
from llmdbenchmark.executor.step import Phase, Step, StepResult
from llmdbenchmark.utilities.endpoint import (
    find_direct_modelservice_endpoint,
    find_epponly_endpoint,
    find_kustomize_endpoint,
    find_standalone_endpoint,
    resolve_direct_service_namespace,
)

# First local port tried for port-forwards; later stacks take the next free one.
PORT_FORWARD_BASE = 8000
# How long to give a fresh port-forward before declaring it failed.
PORT_FORWARD_SETTLE_SECONDS = 3


class ExposeEndpointStep(Step):
    """Expose the model Service via a Route (OpenShift) or port-forward."""

    def __init__(self):
        super().__init__(
            number=9,
            name="expose_endpoint",
            description="Make the model endpoint externally accessible",
            phase=Phase.STANDUP,
            per_stack=True,
        )
        # (namespace, service) -> URL, so stacks sharing one gateway
        # Service reuse a single Route / port-forward.
        self._exposed: dict[tuple[str, str], str] = {}
        # Stacks run in parallel; serialize so they neither race to expose a
        # shared Service nor pick the same free local port.
        self._lock = threading.Lock()

    def should_skip(self, context: ExecutionContext) -> bool:
        # nok8s containers already listen on the host.
        return "nok8s" in (context.deployed_methods or [])

    def execute(
        self, context: ExecutionContext, stack_path: Path | None = None
    ) -> StepResult:
        stack_name = stack_path.name if stack_path else ""
        plan_config = self._load_stack_config(stack_path) if stack_path else {}

        if not plan_config.get("externally_accessible", True):
            return self._ok(stack_name, "externally_accessible is false -- skipping")

        cmd = context.require_cmd()
        namespace = context.require_namespace()

        if context.dry_run:
            how = "route" if context.is_openshift else "port-forward"
            context.logger.log_info(
                f"[DRY RUN] Would expose the model endpoint of {stack_name} via {how}"
            )
            return self._ok(stack_name, f"[DRY RUN] {how}")

        if is_fma_only_mode(context):
            context.logger.log_warning(
                "externally_accessible: FMA-only deployments have no model "
                "Service to expose -- skipping"
            )
            return self._ok(stack_name, "no Service to expose (FMA-only)")

        namespace, svc_name, port = self._find_service(
            cmd, context, plan_config, namespace
        )
        if not svc_name:
            context.logger.log_warning(
                f"externally_accessible: no model Service found for {stack_name} "
                f"in ns/{namespace} -- skipping"
            )
            return self._ok(stack_name, "no model Service found")

        key = (namespace, svc_name)
        with self._lock:
            url = self._exposed.get(key)
            if url is None:
                if context.is_openshift:
                    url = self._expose_route(cmd, context, namespace, svc_name, port)
                else:
                    url = self._port_forward(cmd, context, namespace, svc_name, port)
                if url:
                    self._exposed[key] = url

        if not url:
            if context.is_openshift:
                context.route_failed_stacks.add(stack_name)
            return self._ok(stack_name, "could not expose endpoint (non-fatal)")

        context.external_endpoints[stack_name] = url
        context.logger.log_info(
            f"Model endpoint for {stack_name} is externally accessible at {url}",
            emoji="🌐",
        )
        return self._ok(stack_name, f"externally accessible at {url}")

    def _ok(self, stack_name: str, message: str) -> StepResult:
        return StepResult(
            step_number=self.number,
            step_name=self.name,
            success=True,
            message=message,
            stack_name=stack_name,
        )

    def _find_service(
        self,
        cmd: CommandExecutor,
        context: ExecutionContext,
        plan_config: dict,
        namespace: str,
    ) -> tuple[str, str | None, str]:
        """Return (namespace, service, port) of the Service fronting the model.

        Mirrors run step 03's endpoint detection, but resolves gateway
        classes to the data-plane Service rather than the Gateway object.
        """
        methods = context.deployed_methods or []

        if "standalone" in methods:
            inference_port = plan_config.get("vllmCommon", {}).get(
                "inferencePort", 8000
            )
            _, svc, port = find_standalone_endpoint(cmd, namespace, inference_port)
            return namespace, svc, port

        if "kustomize" in methods:
            guide_name = (plan_config.get("kustomize", {}) or {}).get("guideName", "")
            if not guide_name:
                return namespace, None, "80"
            _, svc, port = find_kustomize_endpoint(cmd, namespace, guide_name)
            return namespace, svc, port

        gateway_class = (plan_config.get("gateway", {}) or {}).get("className", "")
        model_id_label = plan_config.get("model_id_label", "")

        if gateway_class == "epponly":
            ip, svc, port = find_epponly_endpoint(cmd, namespace, model_id_label)
            return namespace, svc if ip else None, port

        if gateway_class == "none":
            direct_ns = resolve_direct_service_namespace(plan_config, namespace)
            default_port = str(
                (plan_config.get("routing", {}) or {}).get("servicePort", "8000")
            )
            ip, svc, port = find_direct_modelservice_endpoint(
                cmd, direct_ns, model_id_label, default_port
            )
            return direct_ns, svc if ip else None, port

        # Gateway classes: istio names the data-plane Service "<gateway>-istio";
        # agentgateway reuses the Gateway's own name.
        release = plan_config.get("release") or context.release
        gateway_name = f"infra-{release}-inference-gateway"
        for svc in (f"{gateway_name}-istio", gateway_name):
            port = self._service_port(cmd, namespace, svc)
            if port:
                return namespace, svc, port
        return namespace, None, "80"

    @staticmethod
    def _get_service(cmd: CommandExecutor, namespace: str, svc: str) -> dict:
        result = cmd.kube(
            "get",
            "service",
            svc,
            "--namespace",
            namespace,
            "-o",
            "json",
            check=False,
        )
        if not (result.success and result.stdout.strip()):
            return {}
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError, TypeError:
            return {}

    def _service_port(self, cmd: CommandExecutor, namespace: str, svc: str) -> str:
        """HTTP port of *svc*, or "" when the Service does not exist."""
        ports = (self._get_service(cmd, namespace, svc).get("spec", {}) or {}).get(
            "ports"
        ) or []
        for p in ports:
            if str(p.get("port")) == "80" or p.get("name") == "http":
                return str(p.get("port"))
        return str(ports[0].get("port", "")) if ports else ""

    def _expose_route(
        self,
        cmd: CommandExecutor,
        context: ExecutionContext,
        namespace: str,
        svc: str,
        port: str,
    ) -> str | None:
        """Reuse or create a Route to *svc*; return its URL."""
        route = self._find_route(cmd, namespace, svc)
        if route is None:
            route_name = f"{svc}-external"[:63].rstrip("-")
            # `oc expose --port` sets the route's targetPort; it must be the
            # service port's name (or its targetPort), not the service port.
            target = port
            for p in (self._get_service(cmd, namespace, svc).get("spec", {}) or {}).get(
                "ports"
            ) or []:
                if str(p.get("port")) == str(port):
                    target = str(p.get("name") or p.get("targetPort") or port)
                    break
            result = cmd.kube(
                "expose",
                f"service/{svc}",
                f"--name={route_name}",
                f"--port={target}",
                "--namespace",
                namespace,
                check=False,
            )
            if not result.success:
                context.logger.log_warning(
                    f"Could not create route to service/{svc} (non-fatal): "
                    f"{result.stderr.strip()}"
                )
                return None
            context.logger.log_info(
                f"OpenShift route '{route_name}' created to service/{svc}"
            )
            route = self._find_route(cmd, namespace, svc)
            if route is None:
                return None

        spec = route.get("spec", {})
        host = spec.get("host")
        if not host:
            return None
        scheme = "https" if spec.get("tls") is not None else "http"
        return f"{scheme}://{host}"

    @staticmethod
    def _find_route(cmd: CommandExecutor, namespace: str, svc: str) -> dict | None:
        result = cmd.kube(
            "get", "route", "--namespace", namespace, "-o", "json", check=False
        )
        if not (result.success and result.stdout.strip()):
            return None
        try:
            items = json.loads(result.stdout).get("items", [])
        except json.JSONDecodeError, TypeError:
            return None
        for route in items:
            if route.get("spec", {}).get("to", {}).get("name") == svc:
                return route
        return None

    def _port_forward(
        self,
        cmd: CommandExecutor,
        context: ExecutionContext,
        namespace: str,
        svc: str,
        port: str,
    ) -> str | None:
        """Start a detached port-forward to *svc*; return its local URL."""
        local_port = _free_local_port(PORT_FORWARD_BASE)
        argv = cmd.kube_argv(
            "port-forward",
            "--namespace",
            namespace,
            f"svc/{svc}",
            f"{local_port}:{port}",
        )
        log_path = cmd.work_dir / "setup" / f"port-forward-{namespace}-{svc}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "w", encoding="utf-8") as log:
            # New session: survives standup exiting and Ctrl-C in the terminal.
            proc = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        time.sleep(PORT_FORWARD_SETTLE_SECONDS)
        if proc.poll() is not None:
            context.logger.log_warning(
                f"port-forward to svc/{svc} exited (non-fatal): "
                f"{log_path.read_text(encoding='utf-8').strip()}"
            )
            return None

        log_path.with_suffix(".pid").write_text(f"{proc.pid}\n", encoding="utf-8")
        context.logger.log_info(
            f"Started '{' '.join(argv)}' (pid {proc.pid}, log {log_path}). "
            f"Stop it with: kill {proc.pid}"
        )
        scheme = "https" if str(port) == "443" else "http"
        return f"{scheme}://localhost:{local_port}"


def _free_local_port(start: int) -> int:
    """First port >= *start* free on localhost."""
    for candidate in range(start, start + 1000):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", candidate))
            except OSError:
                continue
            return candidate
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
