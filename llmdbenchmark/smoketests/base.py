"""Base smoketest with health checks, inference tests, and pod inspection."""

import base64
import json
import time
from pathlib import Path

from llmdbenchmark.executor.command import CommandExecutor
from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.smoketests.report import CheckResult, SmoketestReport
from llmdbenchmark.utilities.endpoint import (
    _rand_suffix,
    _build_overrides,
    _ephemeral_label_args,
    find_standalone_endpoint,
    find_gateway_endpoint,
    test_model_serving,
)


_RETRYABLE_INDICATORS = ("502", "503", "504", "ServiceUnavailable", "not ready")

# Roles whose pod count is HPA-managed when WVA is enabled. The replica
# count check relaxes from strict equality (== <role>.replicas) to
# range-membership (within wva.hpa.[min,max]Replicas) for these roles only.
#
# Currently only `decode` because the per-stack HPA template
# (28_wva-hpa.yaml.j2) only targets the decode Deployment. If the WVA
# chart grows native prefill autoscaling and we render a second HPA,
# add "prefill" here and the relaxation kicks in automatically.
_WVA_HPA_MANAGED_ROLES: frozenset[str] = frozenset({"decode"})


def _is_retryable(text: str) -> bool:
    return any(ind in text for ind in _RETRYABLE_INDICATORS) if text else False


def _is_non_transient_error(resp: dict) -> bool:
    if "error" not in resp:
        return False
    error = resp["error"]
    msg = error.get("message", str(error)) if isinstance(error, dict) else str(error)
    return not _is_retryable(msg)


class BaseSmoketest:
    """Common validation logic shared by all scenarios.

    Provides health checks, inference testing, pod inspection, and a
    library of assertion helpers that per-scenario validators build on.
    """

    @staticmethod
    def _gateway_path_prefix_for_stack(
        plan_config: dict,
        is_standalone: bool,
        stack_name: str = "",
    ) -> str:
        """Thin wrapper over ``utilities.endpoint.compute_gateway_path_prefix``.

        Kept as a method so subclasses can still override if some future
        validator needs scenario-specific routing logic.
        """
        from llmdbenchmark.utilities.endpoint import compute_gateway_path_prefix
        return compute_gateway_path_prefix(
            plan_config, stack_name, is_standalone=is_standalone,
        )

    @staticmethod
    def _gateway_routes_health(plan_config: dict) -> bool:
        """Return True when the gateway routes /health to upstream pods.

        A shared HTTPRoute with ``rewriteTo: /`` routes everything, so
        `/{stack-prefix}/health` reaches vLLM's /health at the root. But
        ``rewriteTo: /v1`` (or any non-root path) narrows routing to the
        /v1/* namespace — /health isn't under /v1, so the gateway would
        have no rule matching it and a smoketest probe would 404.

        The smoketest uses this to decide whether to skip the /health
        probe gracefully in shared-HTTPRoute scenarios where the operator
        deliberately chose narrower routing.
        """
        http_route = plan_config.get("httpRoute") or {}
        if http_route.get("mode") != "shared":
            return True  # not a shared route — direct /health works
        rewrite_to = (http_route.get("rewriteTo") or "/").strip()
        # "/" or "" means "rewrite to root" → /health routes cleanly.
        return rewrite_to in ("", "/")

    @staticmethod
    def discover_endpoint(
        cmd: CommandExecutor,
        context: ExecutionContext,
        plan_config: dict,
    ) -> tuple[str | None, str, bool]:
        """Discover the service/gateway endpoint.

        Returns (service_ip, gateway_port, is_standalone).
        """
        is_standalone = "standalone" in context.deployed_methods
        is_fma = "fma" in context.deployed_methods
        namespace = context.require_namespace()

        inference_port = _nested_get(plan_config, "vllmCommon", "inferencePort") or "8000"
        release = _nested_get(plan_config, "release") or ""

        if is_standalone:
            service_ip, _, gateway_port = find_standalone_endpoint(
                cmd, namespace, inference_port
            )
        elif is_fma:
            return None, "0", False
        else:
            service_ip, _, gateway_port = find_gateway_endpoint(
                cmd, namespace, release
            )

        if not service_ip and context.dry_run:
            service_ip = "<dry-run-endpoint>"
            gateway_port = "80"

        return service_ip, gateway_port, is_standalone

    def run_health_checks(
        self,
        context: ExecutionContext,
        stack_path: Path,
    ) -> SmoketestReport:
        """Run the full health check suite: pods, /health, /v1/models,
        service endpoint, pod IPs, and OpenShift route.
        """
        report = SmoketestReport()
        cmd = context.require_cmd()
        namespace = context.require_namespace()
        plan_config = _load_config(stack_path)

        model_name = _nested_get(plan_config, "model", "name") or ""
        model_id_label = plan_config.get("model_id_label", "") or _nested_get(plan_config, "model", "shortName") or ""
        standalone_role = _nested_get(plan_config, "standalone", "role") or "standalone"

        # skip base healh checks for FMA
        if "fma" in context.deployed_methods:
            return report

        service_ip, gateway_port, is_standalone = self.discover_endpoint(
            cmd, context, plan_config,
        )

        # 1. Check pods running for each configured role
        if is_standalone:
            roles_to_check = [("standalone", standalone_role)]
        else:
            # Check whichever roles are configured (decode, prefill, or both)
            roles_to_check = []
            decode_enabled = _nested_get(plan_config, "decode", "enabled")
            decode_replicas = _nested_get(plan_config, "decode", "replicas") or 0
            # decode is enabled by default if not explicitly disabled
            if decode_enabled is not False and int(decode_replicas) > 0:
                roles_to_check.append(("decode", "decode"))
            else:
                context.logger.log_info("No decode pods configured -- skipping decode health check")
            prefill_enabled = _nested_get(plan_config, "prefill", "enabled")
            prefill_replicas = _nested_get(plan_config, "prefill", "replicas") or 0
            if prefill_enabled and int(prefill_replicas) > 0:
                roles_to_check.append(("prefill", "prefill"))
            else:
                context.logger.log_info("No prefill pods configured -- skipping prefill health check")

        if not roles_to_check:
            report.add(CheckResult(
                "pods_configured", False,
                message="No decode, prefill, or standalone pods configured",
            ))

        for pod_type, role_label in roles_to_check:
            role_selector = f"llm-d.ai/model={model_id_label},llm-d.ai/role={role_label}"
            context.logger.log_info(
                f"Checking {pod_type} pod status (selector: {role_selector})..."
            )
            pod_check = cmd.kube(
                "get", "pods", "-l", role_selector,
                "--namespace", namespace,
                "-o", "jsonpath={.items[*].status.phase}",
                check=False,
            )
            if not pod_check.dry_run:
                if pod_check.success:
                    phases = pod_check.stdout.strip().split()
                    if not phases:
                        report.add(CheckResult(
                            f"{pod_type}_pods_exist", False,
                            message=f"No {pod_type} pods found with selector '{role_selector}'",
                        ))
                    elif not all(p == "Running" for p in phases):
                        report.add(CheckResult(
                            f"{pod_type}_pods_running", False,
                            expected="all Running",
                            actual=", ".join(phases),
                            message=f"Not all {pod_type} pods running (found: {', '.join(phases)})",
                        ))
                    else:
                        context.logger.log_info(
                            f"All {len(phases)} {pod_type} pod(s) running ✓"
                        )
                        report.add(CheckResult(
                            f"{pod_type}_pods_running", True,
                            message=f"{len(phases)} {pod_type} pod(s) running",
                        ))
                        # Check pod Ready condition -- catches crash-looping
                        # sidecar containers (e.g., routing-proxy native
                        # sidecar with restartPolicy: Always). The Ready
                        # condition is True only when ALL containers pass
                        # their readiness probes.
                        ready_check = cmd.kube(
                            "get", "pods", "-l", role_selector,
                            "--namespace", namespace,
                            "--no-headers",
                            "-o", "custom-columns=NAME:.metadata.name,READY:.status.containerStatuses[*].ready",
                            check=False,
                        )
                        if ready_check.success and ready_check.stdout.strip():
                            not_ready = []
                            for line in ready_check.stdout.strip().splitlines():
                                parts = line.strip().split()
                                if len(parts) >= 2:
                                    pod_name = parts[0]
                                    ready_values = parts[1]
                                    if "false" in ready_values.lower():
                                        not_ready.append(pod_name)
                                elif parts:
                                    not_ready.append(parts[0])
                            if not_ready:
                                report.add(CheckResult(
                                    f"{pod_type}_containers_ready", False,
                                    message=f"Not all {pod_type} pods ready (containers may be crash-looping): {', '.join(not_ready)}",
                                ))
                            else:
                                context.logger.log_info(
                                    f"All {pod_type} containers ready ✓"
                                )
                else:
                    report.add(CheckResult(
                        f"{pod_type}_pods_check", False,
                        message=f"Failed to check {pod_type} pod status: {pod_check.stderr}",
                    ))

        if not service_ip:
            report.add(CheckResult(
                "endpoint_discovery", False,
                message="Could not find service/gateway IP",
            ))
            return report

        # When the scenario uses a shared HTTPRoute with path-based routing
        # (e.g. /pool-a/v1 → pool A, /pool-b/v1 → pool B), prepend this
        # stack's path prefix to the gateway URLs so requests actually hit
        # the InferencePool for THIS stack. Returns "" for the usual case
        # (single-model scenarios, standalone) — unchanged behavior.
        url_path_prefix = self._gateway_path_prefix_for_stack(
            plan_config, is_standalone, stack_name=stack_path.name,
        )

        # 2. Health check (/health) — skip gracefully when a shared
        # HTTPRoute deliberately narrows routing to /v1/* (i.e.
        # rewriteTo != "/"). vLLM's /health endpoint lives at the root,
        # so a request to {prefix}/health wouldn't match any route rule
        # and would return 404 regardless of pod health.
        if url_path_prefix and not self._gateway_routes_health(plan_config):
            context.logger.log_info(
                f"Skipping /health probe: gateway rewriteTo="
                f"{(plan_config.get('httpRoute') or {}).get('rewriteTo', '/')!r} "
                "deliberately narrows routing to /v1/* paths. /v1/models "
                "probe + direct-pod-IP health check still run."
            )
            report.add(CheckResult(
                "health_endpoint_skipped", True,
                message=(
                    "/health not routable via gateway (by design); "
                    "relying on /v1/models + direct-pod-IP probe"
                ),
            ))
        else:
            health_err = self._check_health(
                cmd, context, namespace, service_ip, gateway_port, plan_config,
                url_path_prefix=url_path_prefix,
            )
            if health_err:
                report.add(CheckResult("health_endpoint", False, message=health_err))
            else:
                report.add(CheckResult("health_endpoint", True, message="/health responding"))

        # 3. Wait for model ready (/v1/models)
        if report.passed:
            self._wait_for_model_ready(
                cmd, context, namespace, service_ip, gateway_port,
                model_name, plan_config,
                url_path_prefix=url_path_prefix,
            )

        # 4. Test service/gateway
        service_test_passed = False
        context.logger.log_info(
            f'Testing service/gateway "{service_ip}" (port {gateway_port})'
            f'{" [prefix=" + url_path_prefix + "]" if url_path_prefix else ""}...'
        )
        test_result = test_model_serving(
            cmd, namespace, service_ip, gateway_port,
            model_name, plan_config, max_retries=1,
            url_path_prefix=url_path_prefix,
        )
        if test_result:
            report.add(CheckResult(
                "service_endpoint", False, message=f"Service test failed: {test_result}",
            ))
        else:
            service_test_passed = True
            context.logger.log_info(
                f"Service {service_ip}:{gateway_port} responding ✓"
            )
            report.add(CheckResult("service_endpoint", True, message="Service responding"))

        # 5. Test pod IPs directly (use the first role -- decode for ms, standalone for standalone)
        inference_port = _nested_get(plan_config, "vllmCommon", "inferencePort") or "8000"
        primary_role = roles_to_check[0] if roles_to_check else ("decode", "decode")
        primary_selector = f"llm-d.ai/model={model_id_label},llm-d.ai/role={primary_role[1]}"
        pod_ips_result = cmd.kube(
            "get", "pods", "-l", primary_selector,
            "--namespace", namespace,
            "-o", "jsonpath={.items[*].status.podIP}",
            check=False,
        )

        if context.dry_run:
            test_model_serving(
                cmd, namespace, "<dry-run-pod-ip>", inference_port,
                model_name, plan_config, max_retries=1,
            )
        elif pod_ips_result.success and pod_ips_result.stdout.strip():
            pod_ips = pod_ips_result.stdout.strip().split()
            for i, pod_ip in enumerate(pod_ips, 1):
                context.logger.log_info(
                    f"Testing pod {i}/{len(pod_ips)} at {pod_ip}:{inference_port}..."
                )
                test_result = test_model_serving(
                    cmd, namespace, pod_ip, inference_port,
                    model_name, plan_config,
                )
                if test_result:
                    if service_test_passed:
                        context.logger.log_warning(
                            f"Pod IP test failed (non-fatal, service "
                            f"test passed): {test_result}"
                        )
                    else:
                        report.add(CheckResult(
                            f"pod_ip_{pod_ip}", False,
                            message=f"Curl to {pod_ip}:{inference_port} failed: {test_result}",
                        ))
                else:
                    context.logger.log_info(f"Pod {pod_ip} responding ✓")

        # 6. OpenShift route (only for modelservice -- standalone has no gateway route)
        if context.is_openshift and not is_standalone:
            context.logger.log_info("Testing OpenShift route...")
            self._test_openshift_route(
                cmd, context, namespace, model_name, plan_config,
                gateway_port, report, service_test_passed,
            )

        return report

    def run_inference_test(
        self,
        context: ExecutionContext,
        stack_path: Path,
    ) -> SmoketestReport:
        """Run a sample inference request and report pass/fail."""
        report = SmoketestReport()
        cmd = context.require_cmd()
        namespace = context.require_namespace()
        plan_config = _load_config(stack_path)

        model_name = _nested_get(plan_config, "model", "name") or ""

        # skip base inference test for FMA
        if "fma" in context.deployed_methods:
            return report

        service_ip, gateway_port, _is_standalone = self.discover_endpoint(
            cmd, context, plan_config,
        )

        if not service_ip:
            report.add(CheckResult(
                "inference_endpoint", False,
                message="Could not find service/gateway IP for inference test",
            ))
            return report

        protocol = "https" if str(gateway_port) == "443" else "http"
        # Shared-HTTPRoute scenarios route by path prefix (e.g. /pool-a/*
        # → this stack's InferencePool). Bake the prefix into base_url so
        # every downstream {base_url}/v1/completions becomes
        # {base_url}/pool-a/v1/completions — the gateway then rewrites
        # /pool-a/* → /* before the request reaches vLLM. Empty string for
        # every other scenario, preserving existing behavior.
        prefix = self._gateway_path_prefix_for_stack(
            plan_config, _is_standalone, stack_name=stack_path.name,
        )
        base_url = f"{protocol}://{service_ip}:{gateway_port}{prefix}"

        context.logger.log_info(f"Running sample inference against {base_url}...")

        # Try /v1/completions first
        context.logger.log_info("Trying /v1/completions endpoint...")
        result = self._try_completions(
            cmd, context, namespace, base_url, model_name, plan_config,
        )

        if result["success"]:
            self._print_demo_command(
                context, cmd, namespace, plan_config,
                base_url, "/v1/completions",
                result["payload"], result["generated_text"],
            )
            report.add(CheckResult(
                "inference_completions", True,
                message=f'Inference passed via /v1/completions -- Generated: "{result["generated_text"]}"',
            ))
            return report

        # Fallback to /v1/chat/completions
        if result.get("should_fallback"):
            context.logger.log_info(
                f"/v1/completions returned non-transient error: "
                f"{result['error'][:100]}. Falling back to /v1/chat/completions..."
            )
            chat_result = self._try_chat_completions(
                cmd, context, namespace, base_url, model_name, plan_config,
            )
            if chat_result["success"]:
                self._print_demo_command(
                    context, cmd, namespace, plan_config,
                    base_url, "/v1/chat/completions",
                    chat_result["payload"], chat_result["generated_text"],
                )
                report.add(CheckResult(
                    "inference_chat", True,
                    message=f'Inference passed via /v1/chat/completions -- Generated: "{chat_result["generated_text"]}"',
                ))
                return report

            report.add(CheckResult(
                "inference_test", False,
                message=(
                    f"/v1/completions failed: {result['error']}; "
                    f"/v1/chat/completions also failed: {chat_result['error']}"
                ),
            ))
        else:
            report.add(CheckResult(
                "inference_test", False,
                message=result.get("error", "Inference test failed"),
            ))

        return report

    def run_config_validation(
        self,
        context: ExecutionContext,
        stack_path: Path,
    ) -> SmoketestReport:
        """Validate deployed pod config matches scenario expectations.

        The base class returns an empty (all-pass) report.  Per-scenario
        validators override this to add scenario-specific checks.
        """
        report = SmoketestReport()
        report.add(CheckResult(
            "config_validation", True,
            message="No scenario-specific validator configured -- skipping config validation",
        ))
        return report

    @staticmethod
    def get_pod_specs(
        cmd: CommandExecutor,
        namespace: str,
        selector: str,
    ) -> list[dict]:
        """Fetch pod specs for all pods matching *selector*.

        Returns a list of pod dicts (items from ``kubectl get pods -o json``).
        """
        result = cmd.kube(
            "get", "pods", "-l", selector,
            "--namespace", namespace,
            "-o", "json",
            check=False,
        )
        if not result.success or not result.stdout.strip():
            return []
        try:
            data = json.loads(result.stdout)
            return data.get("items", [])
        except json.JSONDecodeError:
            return []

    @staticmethod
    def get_pod_args(pod_spec: dict, container: str = "vllm") -> str:
        """Extract the command/args string for a named container."""
        for c in pod_spec.get("spec", {}).get("containers", []):
            if c.get("name") == container:
                args = c.get("args", [])
                return " ".join(args) if args else ""
        return ""

    @staticmethod
    def get_pod_env(pod_spec: dict, container: str = "vllm") -> dict[str, str]:
        """Extract env vars as a dict for a named container."""
        for c in pod_spec.get("spec", {}).get("containers", []):
            if c.get("name") == container:
                return {
                    e["name"]: e.get("value", "")
                    for e in c.get("env", [])
                    if "name" in e
                }
        return {}

    @staticmethod
    def get_pod_resources(pod_spec: dict, container: str = "vllm") -> dict:
        """Extract resources (limits + requests) for a named container."""
        for c in pod_spec.get("spec", {}).get("containers", []):
            if c.get("name") == container:
                return c.get("resources", {})
        return {}

    @staticmethod
    def get_pod_containers(pod_spec: dict) -> list[str]:
        """Return names of all containers in the pod."""
        return [
            c.get("name", "")
            for c in pod_spec.get("spec", {}).get("containers", [])
        ]

    @staticmethod
    def get_pod_init_containers(pod_spec: dict) -> list[str]:
        """Return names of all init containers in the pod."""
        return [
            c.get("name", "")
            for c in pod_spec.get("spec", {}).get("initContainers", [])
        ]

    @staticmethod
    def get_pod_volumes(pod_spec: dict) -> list[str]:
        """Return names of all volumes in the pod."""
        return [
            v.get("name", "")
            for v in pod_spec.get("spec", {}).get("volumes", [])
        ]

    @staticmethod
    def get_pod_annotations(pod_spec: dict) -> dict[str, str]:
        """Return annotations from the pod metadata."""
        return pod_spec.get("metadata", {}).get("annotations", {})

    @staticmethod
    def get_container_ports(pod_spec: dict, container: str = "vllm") -> list[dict]:
        """Return container port entries for a named container."""
        for c in pod_spec.get("spec", {}).get("containers", []):
            if c.get("name") == container:
                return c.get("ports", [])
        return []

    @staticmethod
    def assert_arg_present(pod_args: str, flag: str) -> CheckResult:
        """Check that *flag* appears in pod args (ignores value)."""
        if flag not in pod_args:
            return CheckResult(
                f"arg_{flag.lstrip('-')}", False,
                expected=f"{flag} present",
                actual="not found",
                message=f"{flag} not found in container vllm args",
            )
        return CheckResult(
            f"arg_{flag.lstrip('-')}", True,
            message=f"{flag} present in container vllm args",
        )

    @staticmethod
    def assert_arg_contains(
        pod_args: str, flag: str, value: str | None = None,
    ) -> CheckResult:
        """Check that *flag* (and optionally *value*) appears in pod args."""
        if flag not in pod_args:
            return CheckResult(
                f"arg_{flag.lstrip('-')}", False,
                expected=f"{flag} present",
                actual="not found",
                message=f"{flag} not found in container vllm args",
            )
        if value is not None and value not in pod_args:
            return CheckResult(
                f"arg_{flag.lstrip('-')}", False,
                expected=f"{flag} with {value}",
                actual=f"{flag} present but value mismatch",
                message=f"{flag} found in container vllm args but expected value '{value}' not present",
            )
        msg = f"{flag} present" + (f" with {value}" if value else "") + " in container vllm args"
        return CheckResult(f"arg_{flag.lstrip('-')}", True, message=msg)

    @staticmethod
    def assert_arg_absent(pod_args: str, flag: str) -> CheckResult:
        """Check that *flag* does not appear in pod args."""
        if flag in pod_args:
            return CheckResult(
                f"no_{flag.lstrip('-')}", False,
                expected=f"{flag} absent",
                actual="present",
                message=f"{flag} should not be in container vllm args",
            )
        return CheckResult(
            f"no_{flag.lstrip('-')}", True,
            message=f"{flag} correctly absent from container vllm args",
        )

    @staticmethod
    def assert_env_equals(
        pod_env: dict[str, str], var_name: str, expected: str,
        container: str = "vllm",
        pod_name: str = "",
    ) -> CheckResult:
        """Check that env var *var_name* equals *expected* in the given container."""
        loc = f"pod/{pod_name} container/{container}" if pod_name else f"container/{container}"
        actual = pod_env.get(var_name)
        if actual is None:
            return CheckResult(
                f"env_{var_name}", False,
                expected=expected, actual="not set",
                message=f"{var_name} not set in {loc} env (expected {expected})",
            )
        if str(actual) != str(expected):
            return CheckResult(
                f"env_{var_name}", False,
                expected=expected, actual=str(actual),
                message=f"{var_name}={actual} in {loc} env (expected {expected})",
            )
        return CheckResult(
            f"env_{var_name}", True,
            message=f"{var_name}={actual} in {loc} env",
        )

    @staticmethod
    def assert_container_exists(containers: list[str], name: str) -> CheckResult:
        """Check that a container named *name* exists in the pod."""
        if name in containers:
            return CheckResult(
                f"container_{name}", True,
                message=f"Container '{name}' present in [{', '.join(containers)}]",
            )
        return CheckResult(
            f"container_{name}", False,
            expected=name, actual=str(containers),
            message=f"Container '{name}' not found in [{', '.join(containers)}]",
        )

    @staticmethod
    def assert_container_absent(containers: list[str], name: str) -> CheckResult:
        """Check that no container named *name* exists in the pod."""
        if name not in containers:
            return CheckResult(
                f"no_container_{name}", True,
                message=f"Container '{name}' correctly absent from [{', '.join(containers)}]",
            )
        return CheckResult(
            f"no_container_{name}", False,
            expected=f"no {name}",
            actual=f"{name} present",
            message=f"Container '{name}' should not be in [{', '.join(containers)}]",
        )

    @staticmethod
    def assert_replica_count(pods: list[dict], expected: int) -> CheckResult:
        """Check that the number of pods matches *expected*."""
        actual = len(pods)
        if actual == expected:
            return CheckResult(
                "replica_count", True,
                message=f"{actual} replica(s) (expected {expected})",
            )
        return CheckResult(
            "replica_count", False,
            expected=str(expected), actual=str(actual),
            message=f"{actual} replica(s) (expected {expected})",
        )

    @staticmethod
    def assert_resource_matches(
        actual_resources: dict,
        expected_value: str,
        resource_path: str,
    ) -> CheckResult:
        """Check a resource field like limits.memory or requests.cpu."""
        parts = resource_path.split(".")
        val = actual_resources
        for p in parts:
            val = val.get(p, {}) if isinstance(val, dict) else None
            if val is None:
                break

        if val is None:
            return CheckResult(
                f"resource_{resource_path}", False,
                expected=expected_value, actual="not set",
                message=f"container vllm resources.{resource_path} not set (expected {expected_value})",
            )
        if str(val) != str(expected_value):
            return CheckResult(
                f"resource_{resource_path}", False,
                expected=expected_value, actual=str(val),
                message=f"container vllm resources.{resource_path}={val} (expected {expected_value})",
            )
        return CheckResult(
            f"resource_{resource_path}", True,
            message=f"container vllm resources.{resource_path}={val}",
        )

    def validate_role_pods(
        self,
        cmd: CommandExecutor,
        namespace: str,
        config: dict,
        role: str,
        model_short: str,
        report: SmoketestReport,
        logger=None,
    ) -> list[dict]:
        """Validate all aspects of pods for a given role (decode/prefill/standalone).

        Checks replica count, resources, parallelism, env vars, init containers,
        security context, volumes, probes, and vLLM args against the rendered config.

        Returns the list of matching pods.
        """
        role_config = _nested_get(config, role) or {}
        prefix = role  # used in check names

        # --- Replica count ---
        pods = self.get_pod_specs(
            cmd, namespace,
            f"llm-d.ai/model={model_short},llm-d.ai/role={role}",
        )
        expected_replicas = role_config.get("replicas")
        if expected_replicas is not None:
            expected_replicas = int(expected_replicas)
            # When multinode (LWS) is enabled, each replica spawns
            # ``workers`` pods (1 leader + N-1 workers).
            multinode_enabled = _nested_get(config, "multinode", "enabled")
            if multinode_enabled:
                workers = int(
                    role_config.get("parallelism", {}).get("workers", 1)
                )
                expected_pods = expected_replicas * workers
            else:
                expected_pods = expected_replicas
            pod_details = ", ".join(
                f"{p.get('metadata', {}).get('name', '?')}@{p.get('spec', {}).get('nodeName', '?')}"
                for p in pods
            ) or "none"

            # If WVA + HPA owns the replica count for this role, the
            # actual pod count is HPA-driven and may legitimately differ
            # from the scenario's static `<role>.replicas` (e.g. with
            # `decode.replicas: 2` and `wva.hpa.minReplicas: 1`, an idle
            # cluster ends up at 1 pod, which is correct, not a regression).
            #
            # In that case we relax the check to "actual count is within
            # the HPA's [minReplicas, maxReplicas] window" and surface
            # both the scenario value and the HPA bounds in the message
            # so a reader can still tell what's happening.
            #
            # Multinode (LWS) deployments scale via LeaderWorkerSet, not
            # HPA, so the relaxation must not apply to them — keep strict
            # equality there.
            hpa_managed = (
                _nested_get(config, "wva", "enabled")
                and _nested_get(config, "wva", "hpa", "enabled")
                and role in _WVA_HPA_MANAGED_ROLES
                and not multinode_enabled
            )
            if hpa_managed:
                hpa_min = int(_nested_get(config, "wva", "hpa", "minReplicas") or 1)
                hpa_max = int(_nested_get(config, "wva", "hpa", "maxReplicas") or expected_pods)
                in_range = hpa_min <= len(pods) <= hpa_max
                report.add(CheckResult(
                    f"{prefix}_replicas",
                    in_range,
                    expected=f"{hpa_min}..{hpa_max} (HPA window)",
                    actual=str(len(pods)),
                    message=(
                        f"{role} pods in ns/{namespace}: "
                        f"{len(pods)} (HPA min={hpa_min} max={hpa_max}, "
                        f"scenario.{role}.replicas={expected_pods}) "
                        f"[{pod_details}]"
                    ),
                ))
            else:
                report.add(CheckResult(
                    f"{prefix}_replicas",
                    len(pods) == expected_pods,
                    expected=str(expected_pods),
                    actual=str(len(pods)),
                    message=(
                        f"{role} pods in ns/{namespace}: "
                        f"{len(pods)} (expected {expected_pods}) [{pod_details}]"
                    ),
                ))

        if not pods:
            return pods

        pod = pods[0]
        pod_name = pod.get("metadata", {}).get("name", "unknown")
        pod_node = pod.get("spec", {}).get("nodeName", "unknown")
        pod_ns = pod.get("metadata", {}).get("namespace", namespace)
        group_name = role

        # Emit a header check so the step renderer can group output
        report.add(CheckResult(
            name=f"{prefix}_header",
            passed=True,
            message=f"Inspecting {role} pod: {pod_name} (node: {pod_node}, ns: {pod_ns})",
            group=group_name,
            is_header=True,
        ))

        def _tag(check: CheckResult) -> CheckResult:
            """Tag a CheckResult with its group for indented rendering."""
            check.group = group_name
            return check

        args = self.get_pod_args(pod)
        env = self.get_pod_env(pod)
        containers = self.get_pod_containers(pod)
        init_containers = self.get_pod_init_containers(pod)
        resources = self.get_pod_resources(pod)
        volumes = self.get_pod_volumes(pod)
        ports = self.get_container_ports(pod)

        # --- Resources (limits + requests) ---
        for section in ("limits", "requests"):
            for field in ("memory", "cpu", "ephemeral-storage"):
                expected = _nested_get(role_config, "resources", section, field)
                if expected is not None:
                    report.add(_tag(self.assert_resource_matches(
                        resources, str(expected), f"{section}.{field}",
                    )))

        # --- Parallelism ---
        parallelism = role_config.get("parallelism", {})
        tp = parallelism.get("tensor")
        if tp is not None and "VLLM_TENSOR_PARALLELISM" in env:
            report.add(_tag(self.assert_env_equals(env, "VLLM_TENSOR_PARALLELISM", str(tp), pod_name=pod_name)))

        dp = parallelism.get("data")
        if dp is not None and "DP_SIZE" in env:
            report.add(_tag(self.assert_env_equals(env, "DP_SIZE", str(dp), pod_name=pod_name)))

        dp_local = parallelism.get("dataLocal")
        if dp_local is not None and "DP_SIZE_LOCAL" in env:
            report.add(_tag(self.assert_env_equals(env, "DP_SIZE_LOCAL", str(dp_local), pod_name=pod_name)))

        # --- Extra env vars ---
        extra_env = role_config.get("extraEnvVars", [])
        for ev in extra_env:
            ev_name = ev.get("name")
            ev_value = ev.get("value")
            if ev_name and ev_value is not None:
                report.add(_tag(self.assert_env_equals(env, ev_name, str(ev_value), pod_name=pod_name)))

        # --- Init containers ---
        expected_init = role_config.get("initContainers", [])
        for ic in expected_init:
            ic_name = ic.get("name")
            if ic_name:
                found = ic_name in init_containers
                report.add(_tag(CheckResult(
                    f"{prefix}_init_{ic_name}",
                    found,
                    expected=f"'{ic_name}' in initContainers",
                    actual=f"initContainers: [{', '.join(init_containers)}]",
                    message=f"initContainer '{ic_name}' {'present' if found else 'not found'} in [{', '.join(init_containers)}]",
                )))

        # --- Security context capabilities ---
        extra_config = role_config.get("extraContainerConfig", {})
        expected_caps = _nested_get(extra_config, "securityContext", "capabilities", "add")
        if expected_caps:
            actual_caps = self._get_container_security_caps(pod)
            for cap in expected_caps:
                has_cap = cap in actual_caps
                report.add(_tag(CheckResult(
                    f"{prefix}_cap_{cap}",
                    has_cap,
                    expected=cap,
                    actual=f"capabilities.add: [{', '.join(actual_caps)}]",
                    message=f"securityContext capability {cap} {'present' if has_cap else 'not found'} in [{', '.join(actual_caps)}]",
                )))

        # --- Routing proxy (may be a regular container or init container) ---
        # The helm chart only injects the routing proxy on decode pods,
        # not prefill pods (prefill receives traffic via KV transfer).
        routing_enabled = _nested_get(config, "routing", "proxy", "enabled")
        if routing_enabled is True and role in ("decode", "standalone"):
            in_containers = "routing-proxy" in containers
            in_init = "routing-proxy" in init_containers
            found = in_containers or in_init
            location = "containers" if in_containers else ("initContainers" if in_init else "")
            report.add(_tag(CheckResult(
                f"{prefix}_routing_proxy", found,
                expected="routing-proxy in containers or initContainers",
                actual=f"{'found in ' + location if found else 'not found'}",
                message=f"routing-proxy {'present in ' + location if found else 'not found in containers or initContainers'}",
            )))
        elif routing_enabled is False and role in ("decode", "standalone"):
            in_containers = "routing-proxy" in containers
            in_init = "routing-proxy" in init_containers
            found = in_containers or in_init
            location = "containers" if in_containers else ("initContainers" if in_init else "")
            report.add(_tag(CheckResult(
                f"{prefix}_no_routing_proxy", not found,
                expected="routing-proxy absent",
                actual=f"{'found in ' + location if found else 'absent'}",
                message=f"routing-proxy {'should not be present but found in ' + location if found else 'correctly absent'}",
            )))

        # --- Volumes from vllmCommon ---
        expected_volumes = _nested_get(config, "vllmCommon", "volumes") or []
        for vol in expected_volumes:
            vol_name = vol.get("name")
            if vol_name:
                found = vol_name in volumes
                report.add(_tag(CheckResult(
                    f"{prefix}_volume_{vol_name}",
                    found,
                    expected=f"'{vol_name}' in spec.volumes",
                    actual=f"spec.volumes: [{', '.join(volumes)}]",
                    message=f"volume '{vol_name}' {'present' if found else 'not found'} in spec.volumes [{', '.join(volumes)}]",
                )))

        # --- Volume mounts from vllmCommon ---
        expected_mounts = _nested_get(config, "vllmCommon", "volumeMounts") or []
        actual_mounts = self._get_container_volume_mounts(pod)
        for mount in expected_mounts:
            mount_name = mount.get("name")
            mount_path = mount.get("mountPath")
            if mount_name:
                has_mount = mount_name in actual_mounts
                actual_path = actual_mounts.get(mount_name, "N/A")
                mount_names = list(actual_mounts.keys())
                report.add(_tag(CheckResult(
                    f"{prefix}_mount_{mount_name}",
                    has_mount,
                    expected=f"'{mount_name}' at {mount_path}",
                    actual=f"{'at ' + actual_path if has_mount else 'not found'} in [{', '.join(mount_names)}]",
                    message=f"volumeMount '{mount_name}' {'at ' + actual_path if has_mount else 'not found in [' + ', '.join(mount_names) + ']'}",
                )))

        # --- Probes ---
        probe_config = role_config.get("probes", {})
        self._validate_probes(pod, prefix, probe_config, report, group=group_name)

        # --- vLLM flags ---
        # When customCommand is set, the auto-generated flags (from
        # vllmCommon.flags, model.blockSize, etc.) are not applied --
        # the custom command is responsible for its own flags.
        has_custom_command = bool(_nested_get(role_config, "vllm", "customCommand"))

        if not has_custom_command:
            flags = _nested_get(config, "vllmCommon", "flags") or {}
            if flags.get("enforceEager") is True:
                report.add(_tag(self.assert_arg_contains(args, "--enforce-eager")))
            if flags.get("noPrefixCaching") is True:
                report.add(_tag(self.assert_arg_contains(args, "--no-enable-prefix-caching")))
            if flags.get("disableLogRequests") is True:
                report.add(_tag(self.assert_arg_contains(args, "--no-enable-log-requests")))
            if flags.get("disableUvicornAccessLog") is True:
                report.add(_tag(self.assert_arg_contains(args, "--disable-uvicorn-access-log")))

            # --- Model args (block size, max model len) ---
            # The auto-generated command uses env var references ($VLLM_BLOCK_SIZE,
            # $VLLM_MAX_MODEL_LEN) instead of hardcoded values. We verify:
            # 1. The flag is present in the args (with env var or literal value)
            # 2. The corresponding env var on the pod has the correct value
            model_config = config.get("model", {})
            block_size = model_config.get("blockSize")
            if block_size is not None:
                # Check flag present (either $VLLM_BLOCK_SIZE or literal value)
                report.add(_tag(self.assert_arg_present(args, "--block-size")))
                # Check env var has the right value
                report.add(_tag(self.assert_env_equals(
                    env, "VLLM_BLOCK_SIZE", str(block_size), pod_name=pod_name,
                )))

            max_model_len = model_config.get("maxModelLen")
            if max_model_len is not None:
                report.add(_tag(self.assert_arg_present(args, "--max-model-len")))
                report.add(_tag(self.assert_env_equals(
                    env, "VLLM_MAX_MODEL_LEN", str(max_model_len), pod_name=pod_name,
                )))

            # --- Additional flags from role config ---
            additional_flags = _nested_get(role_config, "vllm", "additionalFlags") or []
            for flag in additional_flags:
                if isinstance(flag, str) and flag.startswith("--"):
                    report.add(_tag(self.assert_arg_contains(args, flag)))

        # --- KV transfer (checked for both auto-generated and custom commands) ---
        kv_transfer = _nested_get(config, "vllmCommon", "kvTransfer") or {}
        if kv_transfer.get("enabled"):
            kv_connector = kv_transfer.get("connector")
            if kv_connector:
                report.add(_tag(self.assert_arg_contains(
                    args, "--kv-transfer-config", str(kv_connector),
                )))

        # --- KV events (checked for both auto-generated and custom commands) ---
        kv_events = _nested_get(config, "vllmCommon", "kvEvents") or {}
        if kv_events.get("enabled"):
            kv_port = kv_events.get("port")
            if kv_port is not None:
                has_port = any(p.get("containerPort") == int(kv_port) for p in ports)
                report.add(_tag(CheckResult(
                    f"{prefix}_kv_events_port",
                    has_port,
                    expected=str(kv_port),
                    message=f"KV events containerPort {kv_port} {'present' if has_port else 'not found'} in container ports",
                )))

        # --- Role env vars (injected by helm chart, not from config) ---
        if role == "decode":
            report.add(_tag(self.assert_env_equals(env, "VLLM_IS_DECODE", "1", pod_name=pod_name)))
        elif role == "prefill":
            report.add(_tag(self.assert_env_equals(env, "VLLM_IS_PREFILL", "1", pod_name=pod_name)))

        return pods

    @staticmethod
    def _get_container_security_caps(
        pod_spec: dict, container: str = "vllm",
    ) -> list[str]:
        """Extract security context capabilities for a container."""
        for c in pod_spec.get("spec", {}).get("containers", []):
            if c.get("name") == container:
                return (
                    c.get("securityContext", {})
                    .get("capabilities", {})
                    .get("add", [])
                )
        return []

    @staticmethod
    def _get_container_volume_mounts(
        pod_spec: dict, container: str = "vllm",
    ) -> dict[str, str]:
        """Return volume mount names mapped to mount paths."""
        for c in pod_spec.get("spec", {}).get("containers", []):
            if c.get("name") == container:
                return {
                    m.get("name", ""): m.get("mountPath", "")
                    for m in c.get("volumeMounts", [])
                }
        return {}

    def _validate_probes(
        self,
        pod_spec: dict,
        prefix: str,
        probe_config: dict,
        report: SmoketestReport,
        container: str = "vllm",
        group: str = "",
    ):
        """Validate probe configuration against config."""
        for c in pod_spec.get("spec", {}).get("containers", []):
            if c.get("name") != container:
                continue

            for probe_type in ("startup", "liveness", "readiness"):
                expected = probe_config.get(probe_type)
                if not expected:
                    continue

                actual_probe = c.get(f"{probe_type}Probe", {})
                if not actual_probe:
                    report.add(CheckResult(
                        f"{prefix}_{probe_type}_probe",
                        False,
                        message=f"{probe_type}Probe not configured on container",
                        group=group,
                    ))
                    continue

                # Check path
                expected_path = expected.get("path")
                if expected_path:
                    actual_path = actual_probe.get("httpGet", {}).get("path")
                    report.add(CheckResult(
                        f"{prefix}_{probe_type}_path",
                        actual_path == expected_path,
                        expected=expected_path,
                        actual=str(actual_path),
                        message=f"{probe_type}Probe path: {actual_path} (expected {expected_path})",
                        group=group,
                    ))

                # Check key numeric fields
                for field in ("failureThreshold", "periodSeconds", "initialDelaySeconds", "timeoutSeconds"):
                    expected_val = expected.get(field)
                    if expected_val is not None:
                        actual_val = actual_probe.get(field)
                        report.add(CheckResult(
                            f"{prefix}_{probe_type}_{field}",
                            str(actual_val) == str(expected_val),
                            expected=str(expected_val),
                            actual=str(actual_val),
                            message=f"{probe_type}Probe.{field}: {actual_val} (expected {expected_val})",
                            group=group,
                        ))
            break

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
        url_path_prefix: str = "",
    ) -> str | None:
        from llmdbenchmark.utilities.endpoint import _normalize_url_prefix
        protocol = "https" if str(port) == "443" else "http"
        prefix = _normalize_url_prefix(url_path_prefix)
        url = f"{protocol}://{host}:{port}{prefix}/health"
        curl_image = "quay.io/curl/curl"
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
                    f"after {timeout}s -- process may not be running"
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
                + _ephemeral_label_args()
                + override_args
                + ["--command", "--", "sh", "-c", curl_cmd]
            )

            result = cmd.kube(*kubectl_args, check=False)

            if result.dry_run:
                return None

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
        url_path_prefix: str = "",
    ):
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
                    f"Model readiness wait timed out after {timeout}s -- "
                    f"proceeding with smoketest assertions"
                )
                return

            attempt += 1
            result = test_model_serving(
                cmd, namespace, host, port, expected_model, plan_config,
                max_retries=1,
                url_path_prefix=url_path_prefix,
            )

            if cmd.dry_run:
                return

            if result is None:
                context.logger.log_info(
                    f"Model ready at {host}:{port} ✓ ({int(elapsed)}s elapsed)"
                )
                return

            remaining = int(timeout - elapsed)
            context.logger.log_info(
                f"Model not ready yet (attempt {attempt}, "
                f"{remaining}s remaining)..."
            )
            time.sleep(poll_interval)

    def _test_openshift_route(
        self,
        cmd: CommandExecutor,
        context: ExecutionContext,
        namespace: str,
        model_name: str,
        plan_config: dict,
        gateway_port: str,
        report: SmoketestReport,
        service_test_passed: bool,
    ):
        release = _nested_get(plan_config, "release") or ""
        route_name = f"{release}-inference-gateway-route"

        route_result = cmd.kube(
            "get", "route", route_name, "-n", namespace,
            "-o", "jsonpath={.spec.host}:{.spec.tls.termination}",
            check=False,
        )
        if route_result.success and route_result.stdout.strip():
            parts = route_result.stdout.strip().strip("'").split(":", 1)
            route_host = parts[0]
            tls_termination = parts[1] if len(parts) > 1 else ""
            route_port = "443" if tls_termination else "80"

            context.logger.log_info(
                f"Testing route {route_host} (port {route_port})..."
            )
            test_result = test_model_serving(
                cmd, namespace, route_host, route_port,
                model_name, plan_config,
            )
            if test_result:
                if service_test_passed:
                    context.logger.log_warning(
                        f"Route test failed (non-fatal): {test_result}"
                    )
                else:
                    report.add(CheckResult(
                        "openshift_route", False,
                        message=f"Route test failed: {test_result}",
                    ))
            else:
                context.logger.log_info(f"Route {route_host} responding ✓")
                report.add(CheckResult(
                    "openshift_route", True,
                    message="Route responding",
                ))
        else:
            context.logger.log_warning(
                f"Unable to fetch OpenShift route '{route_name}'"
            )

    def _try_completions(
        self,
        cmd: CommandExecutor,
        context: ExecutionContext,
        namespace: str,
        base_url: str,
        model_name: str,
        plan_config: dict | None,
        max_retries: int = 3,
        retry_interval: int = 15,
    ) -> dict:
        url = f"{base_url}/v1/completions"
        payload = {
            "model": model_name,
            "prompt": "The capital of the United States is",
            "max_tokens": 5,
            "temperature": 0,
        }

        for attempt in range(1, max_retries + 1):
            stdout, err = self._curl_post(cmd, namespace, url, payload, plan_config)

            if cmd.dry_run:
                return {
                    "success": True, "payload": payload,
                    "generated_text": "<dry-run>",
                }

            if err:
                if _is_retryable(err) and attempt < max_retries:
                    context.logger.log_info(
                        f"Attempt {attempt}/{max_retries}: {err[:80]}, "
                        f"retrying in {retry_interval}s..."
                    )
                    time.sleep(retry_interval)
                    continue
                return {"success": False, "error": err}

            try:
                resp = json.loads(stdout)
            except json.JSONDecodeError:
                if _is_retryable(stdout) and attempt < max_retries:
                    time.sleep(retry_interval)
                    continue
                return {
                    "success": False,
                    "error": f"Non-JSON response from {url}: {stdout[:200]}",
                }

            if _is_non_transient_error(resp):
                error_msg = resp["error"]
                if isinstance(error_msg, dict):
                    error_msg = error_msg.get("message", str(error_msg))
                return {
                    "success": False, "error": str(error_msg),
                    "should_fallback": True,
                }

            if "error" in resp:
                error_msg = resp["error"]
                if isinstance(error_msg, dict):
                    error_msg = error_msg.get("message", str(error_msg))
                if attempt < max_retries:
                    time.sleep(retry_interval)
                    continue
                return {"success": False, "error": str(error_msg)}

            if "choices" not in resp or not resp["choices"]:
                if attempt < max_retries:
                    time.sleep(retry_interval)
                    continue
                return {"success": False, "error": f"Missing choices in response from {url}"}

            first = resp["choices"][0]
            if not first.get("text") and not first.get("message"):
                if attempt < max_retries:
                    time.sleep(retry_interval)
                    continue
                return {"success": False, "error": f"No generated text from {url}"}

            text = first.get("text", "").strip()
            return {"success": True, "generated_text": text, "payload": payload}

        return {"success": False, "error": f"Exhausted {max_retries} retries for {url}"}

    def _try_chat_completions(
        self,
        cmd: CommandExecutor,
        context: ExecutionContext,
        namespace: str,
        base_url: str,
        model_name: str,
        plan_config: dict | None,
        max_retries: int = 3,
        retry_interval: int = 15,
    ) -> dict:
        url = f"{base_url}/v1/chat/completions"
        payload = {
            "model": model_name,
            "messages": [{"role": "user", "content": "What is the capital of the United States?"}],
            "max_tokens": 5,
            "temperature": 0,
        }

        for attempt in range(1, max_retries + 1):
            stdout, err = self._curl_post(cmd, namespace, url, payload, plan_config)

            if err:
                if _is_retryable(err) and attempt < max_retries:
                    context.logger.log_info(
                        f"Chat attempt {attempt}/{max_retries}: {err[:80]}, "
                        f"retrying in {retry_interval}s..."
                    )
                    time.sleep(retry_interval)
                    continue
                return {"success": False, "error": err}

            try:
                resp = json.loads(stdout)
            except json.JSONDecodeError:
                if _is_retryable(stdout) and attempt < max_retries:
                    time.sleep(retry_interval)
                    continue
                return {
                    "success": False,
                    "error": f"Non-JSON response from {url}: {stdout[:200]}",
                }

            if "error" in resp:
                error_msg = resp["error"]
                if isinstance(error_msg, dict):
                    error_msg = error_msg.get("message", str(error_msg))
                if _is_retryable(str(error_msg)) and attempt < max_retries:
                    time.sleep(retry_interval)
                    continue
                return {"success": False, "error": str(error_msg)}

            if "choices" not in resp or not resp["choices"]:
                if attempt < max_retries:
                    time.sleep(retry_interval)
                    continue
                return {"success": False, "error": f"Missing choices from {url}"}

            message = resp["choices"][0].get("message", {})
            text = message.get("content", "").strip()
            if not text:
                if attempt < max_retries:
                    time.sleep(retry_interval)
                    continue
                return {"success": False, "error": f"No content in response from {url}"}

            return {"success": True, "generated_text": text, "payload": payload}

        return {"success": False, "error": f"Exhausted {max_retries} retries for {url}"}

    @staticmethod
    def _curl_post(
        cmd: CommandExecutor,
        namespace: str,
        url: str,
        payload: dict,
        plan_config: dict | None,
        timeout_seconds: int = 120,
    ) -> tuple[str, str | None]:
        override_args = _build_overrides(plan_config)
        curl_image = "quay.io/curl/curl"
        pod_name = f"inference-test-{_rand_suffix()}"
        payload_json = json.dumps(payload)
        payload_b64 = base64.b64encode(payload_json.encode()).decode()

        curl_cmd = (
            f"'echo {payload_b64} | base64 -d | "
            f"curl -sk --max-time {timeout_seconds} "
            f"-X POST {url} "
            f"-H \"Content-Type: application/json\" "
            f"-d @- 2>&1'"
        )

        kubectl_args = (
            [
                "run", pod_name, "--rm", "--attach", "--quiet",
                "--restart=Never", "--namespace", namespace,
                f"--image={curl_image}",
            ]
            + _ephemeral_label_args()
            + override_args
            + ["--command", "--", "sh", "-c", curl_cmd]
        )

        result = cmd.kube(*kubectl_args, check=False)

        if result.dry_run:
            return "", None

        if not result.success:
            detail = result.stderr[:300] or result.stdout[:300]
            return "", f"Curl to {url} failed: {detail}"

        return result.stdout.strip(), None

    def _print_demo_command(
        self,
        context: ExecutionContext,
        cmd: CommandExecutor,
        namespace: str,
        plan_config: dict,
        base_url: str,
        endpoint: str,
        payload: dict,
        generated_text: str,
    ):
        payload_compact = json.dumps(payload, separators=(",", ":"))

        context.logger.log_info(f"✅ Inference test passed via {endpoint}")
        if generated_text:
            context.logger.log_info(f'   Generated: "{generated_text[:80]}"')
        context.logger.log_info("")

        external_url = self._detect_external_url(
            cmd, namespace, plan_config, endpoint,
        )

        demo_url = external_url or f"{base_url}{endpoint}"
        payload_pretty = json.dumps(payload, indent=2)
        context.logger.log_info("   To reproduce or demo, run:")
        context.logger.log_info("")
        context.logger.log_info(f"   curl -sk -X POST \\")
        context.logger.log_info(f"     {demo_url} \\")
        context.logger.log_info(f"     -H 'Content-Type: application/json' \\")
        context.logger.log_info(f"     -d '{{")
        for line in payload_pretty.splitlines()[1:]:
            context.logger.log_info(f"       {line}")
        context.logger.log_info(f"     '")

    def _detect_external_url(
        self,
        cmd: CommandExecutor,
        namespace: str,
        plan_config: dict,
        endpoint: str,
    ) -> str | None:
        try:
            release = _nested_get(plan_config, "release") or ""
            model_id_label = plan_config.get("model_id_label", "") or _nested_get(plan_config, "model", "shortName") or ""
        except KeyError:
            return None

        if not release:
            return None

        route_name = f"{release}-inference-gateway-route"
        result = cmd.kube(
            "get", "route", route_name,
            "-n", namespace,
            "-o", "jsonpath={.spec.host}:{.spec.tls.termination}",
            check=False,
        )

        if not result.success or not result.stdout.strip():
            return None

        parts = result.stdout.strip().strip("'").split(":", 1)
        route_host = parts[0]
        tls_termination = parts[1] if len(parts) > 1 else ""
        protocol = "https" if tls_termination else "http"

        return f"{protocol}://{route_host}/{model_id_label}{endpoint}"


def _nested_get(d: dict, *keys: str):
    """Safely traverse nested dicts."""
    for key in keys:
        if not isinstance(d, dict):
            return None
        d = d.get(key)
        if d is None:
            return None
    return d


def _load_config(stack_path: Path) -> dict:
    """Load the rendered config.yaml from a stack directory."""
    import yaml
    config_file = stack_path / "config.yaml"
    if config_file.exists():
        with open(config_file) as f:
            return yaml.safe_load(f) or {}
    return {}
