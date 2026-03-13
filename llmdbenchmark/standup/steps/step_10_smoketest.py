"""Step 10 -- Smoketest deployment health and model serving."""

import json
import time
from pathlib import Path

import yaml

from llmdbenchmark.executor.step import Step, StepResult, Phase
from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.executor.command import CommandExecutor
from llmdbenchmark.utilities.endpoint import (
    _rand_suffix,
    _build_overrides,
    find_standalone_endpoint,
    find_gateway_endpoint,
    test_model_serving,
)


class SmoketestStep(Step):
    """Validate deployment health and model serving via smoketests."""

    def __init__(self):
        super().__init__(
            number=10,
            name="smoketest",
            description="Validate deployment health and model serving",
            phase=Phase.STANDUP,
            per_stack=True,
        )

    def execute(  # pylint: disable=too-many-branches,too-many-locals,too-many-statements
        self, context: ExecutionContext, stack_path: Path | None = None
    ) -> StepResult:
        if stack_path is None:
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=False,
                message="No stack path provided for per-stack step",
                errors=["stack_path is required"],
            )

        errors = []
        cmd = context.require_cmd()

        namespace = context.require_namespace()
        stack_name = stack_path.name
        is_standalone = "standalone" in context.deployed_methods

        plan_config = self._load_stack_config(stack_path)
        model_name = self._require_config(plan_config, "model", "name")
        inference_port = self._require_config(plan_config, "vllmCommon", "inferencePort")
        release = self._require_config(plan_config, "release")

        gateway_port = "80"
        service_ip = None
        service_name = None

        if context.dry_run:
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=True,
                message=(f"[DRY RUN] Smoketest commands logged for {stack_name}"),
                stack_name=stack_name,
            )

        if is_standalone:
            service_ip, service_name, gateway_port = find_standalone_endpoint(
                cmd, namespace, inference_port
            )
        else:
            service_ip, service_name, gateway_port = find_gateway_endpoint(
                cmd, namespace, release
            )

        if not service_ip:
            errors.append("Could not find service/gateway IP for smoketest")

        model_short = self._require_config(plan_config, "model", "shortName")
        standalone_role = self._require_config(plan_config, "standalone", "role")
        if is_standalone:
            pod_selector = (
                f"llm-d.ai/model={model_short},llm-d.ai/role={standalone_role}"
            )
        else:
            pod_selector = f"llm-d.ai/model={model_short},llm-d.ai/role=decode"

        context.logger.log_info(f"Checking pod status (selector: {pod_selector})...")

        pod_check = cmd.kube(
            "get",
            "pods",
            "-l",
            pod_selector,
            "--namespace",
            namespace,
            "-o",
            "jsonpath={.items[*].status.phase}",
            check=False,
        )

        if pod_check.success:
            phases = pod_check.stdout.strip().split()
            if not phases:
                errors.append(f"No pods found with selector '{pod_selector}'")
            elif not all(p == "Running" for p in phases):
                errors.append("Not all pods running " f"(found: {', '.join(phases)})")
            else:
                context.logger.log_info(f"All {len(phases)} pod(s) running ✓")
        else:
            errors.append(f"Failed to check pod status: {pod_check.stderr}")

        if service_ip and not errors:
            health_err = self._check_health(
                cmd, context, namespace, service_ip, gateway_port, plan_config,
            )
            if health_err:
                errors.append(health_err)

        # Pods can be Running/Ready per K8s but still loading the model
        # or warming up the P/D topology.  Poll the service endpoint
        # until it actually serves the model before running assertions.
        if service_ip and not errors:
            self._wait_for_model_ready(
                cmd, context, namespace, service_ip, gateway_port,
                model_name, plan_config,
            )

        service_test_passed = False
        if service_ip:
            context.logger.log_info(
                f'Testing service/gateway "{service_ip}" '
                f"(port {gateway_port})..."
            )
            test_result = test_model_serving(
                cmd,
                namespace,
                service_ip,
                gateway_port,
                model_name,
                plan_config,
                max_retries=1,
            )
            if test_result:
                errors.append(f"Service test failed: {test_result}")
            else:
                service_test_passed = True
                context.logger.log_info(
                    f"Service {service_ip}:{gateway_port} responding ✓"
                )
        # In disaggregated P/D setups, decode pods may return 503 on
        # direct access while the NIXL KV-transfer topology warms up,
        # even though the gateway already routes correctly.  If the
        # service test passed, pod-IP failures are demoted to warnings.
        pod_test_passed = False
        pod_ips_result = cmd.kube(
            "get",
            "pods",
            "-l",
            pod_selector,
            "--namespace",
            namespace,
            "-o",
            "jsonpath={.items[*].status.podIP}",
            check=False,
        )

        if pod_ips_result.success and pod_ips_result.stdout.strip():
            pod_ips = pod_ips_result.stdout.strip().split()
            for i, pod_ip in enumerate(pod_ips, 1):
                context.logger.log_info(
                    f"Testing pod {i}/{len(pod_ips)} "
                    f"at {pod_ip}:{inference_port}..."
                )
                test_result = test_model_serving(
                    cmd,
                    namespace,
                    pod_ip,
                    inference_port,
                    model_name,
                    plan_config,
                )
                if test_result:
                    if service_test_passed:
                        context.logger.log_warning(
                            f"Pod IP test failed (non-fatal, service "
                            f"test passed): {test_result}"
                        )
                    else:
                        errors.append(test_result)
                else:
                    pod_test_passed = True
                    context.logger.log_info(f"Pod {pod_ip} responding ✓")
        elif not errors:
            errors.append("No pod IPs found for smoketest")

        # Non-fatal if either the service or pod-IP test passed.
        any_passed = service_test_passed or pod_test_passed
        if context.is_openshift:
            context.logger.log_info("Testing OpenShift route...")
            route_errors: list[str] = []
            self._test_openshift_route(
                cmd,
                context,
                namespace,
                model_name,
                plan_config,
                gateway_port,
                route_errors,
            )
            if route_errors:
                if any_passed:
                    for re in route_errors:
                        context.logger.log_warning(
                            f"Route test failed (non-fatal): {re}"
                        )
                else:
                    errors.extend(route_errors)

        if errors:
            for err in errors:
                context.logger.log_error(f"Smoketest: {err}")
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=False,
                message="Smoketest failed",
                errors=errors,
                stack_name=stack_name,
            )

        return StepResult(
            step_number=self.number,
            step_name=self.name,
            success=True,
            message=f"All smoketests passed for {stack_name}",
            stack_name=stack_name,
        )

    def _check_health(
        self,
        cmd: CommandExecutor,
        context: ExecutionContext,
        namespace: str,
        host: str,
        port: str | int,
        plan_config: dict | None = None,
        timeout: int = 120,
        poll_interval: int = 10,
    ) -> str | None:
        """Check that vLLM is listening by polling /health.

        This distinguishes 'vLLM process is down' from 'model still loading'.
        Returns an error string if /health never responds, None on success.
        """
        protocol = "https" if str(port) == "443" else "http"
        url = f"{protocol}://{host}:{port}/health"
        curl_image = "curlimages/curl"
        override_args = _build_overrides(plan_config)

        context.logger.log_info(
            f"Health check: verifying vLLM is listening at {host}:{port}/health..."
        )
        start = time.time()
        attempt = 0

        while True:
            elapsed = time.time() - start
            if elapsed > timeout:
                return (
                    f"vLLM health check failed: /health did not respond "
                    f"after {timeout}s — process may not be running"
                )

            attempt += 1
            pod_name = f"healthcheck-{_rand_suffix()}"
            curl_cmd = (
                f"'curl -sk --max-time 10 -o /dev/null -w %{{http_code}} {url}'"
            )

            kubectl_args = (
                [
                    "run", pod_name, "--rm", "--attach", "--quiet",
                    "--restart=Never", "--namespace", namespace,
                    f"--image={curl_image}",
                ]
                + override_args
                + ["--command", "--", "sh", "-c", curl_cmd]
            )

            result = cmd.kube(*kubectl_args, check=False)
            status_code = result.stdout.strip() if result.success else ""

            if status_code == "200":
                context.logger.log_info(
                    f"vLLM health check passed ✓ ({int(elapsed)}s elapsed)"
                )
                return None

            remaining = int(timeout - elapsed)
            context.logger.log_info(
                f"vLLM not listening yet (attempt {attempt}, "
                f"status={status_code or 'N/A'}, {remaining}s remaining)..."
            )
            time.sleep(poll_interval)

    def _wait_for_model_ready(
        self,
        cmd: CommandExecutor,
        context: ExecutionContext,
        namespace: str,
        host: str,
        port: str | int,
        expected_model: str,
        plan_config: dict | None = None,
        timeout: int = 300,
        poll_interval: int = 15,
    ):
        """Poll the service endpoint until the model is actually serving.

        Kubernetes may report pods as Ready before the model is fully loaded
        or the P/D topology has finished warming up.  This blocks until
        ``/v1/models`` returns the expected model name, up to *timeout* seconds.
        """
        context.logger.log_info(
            f"Waiting for model to be ready at {host}:{port} "
            f"(timeout {timeout}s)..."
        )
        start = time.time()
        attempt = 0

        while True:
            elapsed = time.time() - start
            if elapsed > timeout:
                context.logger.log_warning(
                    f"Model readiness wait timed out after {timeout}s — "
                    f"proceeding with smoketest assertions"
                )
                return

            attempt += 1
            result = test_model_serving(
                cmd, namespace, host, port, expected_model, plan_config,
                max_retries=1,
            )
            if result is None:
                context.logger.log_info(
                    f"Model ready at {host}:{port} ✓ "
                    f"({int(elapsed)}s elapsed)"
                )
                return

            remaining = int(timeout - elapsed)
            context.logger.log_info(
                f"Model not ready yet (attempt {attempt}, "
                f"{remaining}s remaining)..."
            )
            time.sleep(poll_interval)

    def _test_openshift_route(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self,
        cmd: CommandExecutor,
        _context: ExecutionContext,
        namespace: str,
        model_name: str,
        plan_config: dict,
        _gateway_port: str,
        errors: list,
    ):
        """Test the OpenShift route endpoint."""
        release = self._require_config(plan_config, "release")
        route_name = f"{release}-inference-gateway-route"

        route_result = cmd.kube(
            "get",
            "route",
            route_name,
            "-n",
            namespace,
            "-o",
            "jsonpath={.spec.host}:{.spec.tls.termination}",
            check=False,
        )
        if route_result.success and route_result.stdout.strip():
            parts = route_result.stdout.strip().strip("'").split(":", 1)
            route_host = parts[0]
            tls_termination = parts[1] if len(parts) > 1 else ""

            route_port = "443" if tls_termination else "80"

            _context.logger.log_info(
                f"Testing route {route_host} (port {route_port})..."
            )
            test_result = test_model_serving(
                cmd,
                namespace,
                route_host,
                route_port,
                model_name,
                plan_config,
            )
            if test_result:
                errors.append(f"Route test failed: {test_result}")
            else:
                _context.logger.log_info(f"Route {route_host} responding ✓")
        else:
            _context.logger.log_warning(
                f"Unable to fetch OpenShift route '{route_name}'"
            )
