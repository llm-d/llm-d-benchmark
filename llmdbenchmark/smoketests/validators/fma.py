"""Validator for the FMA (fast-model-actuation) standup path.

FMA (``standup_method: fma``) has no Service/gateway/EPP and no decode/prefill
Deployments: a launcher pod hosts vLLM directly and is bound to a requester on
demand. This validator overrides the health check and inference test to probe
the bound launcher pod instead of the generic role-based path in
``BaseSmoketest``.

Registered in the ``VALIDATORS`` map under ``"fast-model-actuation"`` (and
``"fast-model-actuation-keda"``); the benchmark path (``standup_method: fma``)
is routed to the same key by ``get_validator``.
"""

from json import JSONDecodeError, loads
from pathlib import Path
from time import perf_counter, sleep

from llmdbenchmark.executor.command import CommandExecutor
from llmdbenchmark.executor.context import ExecutionContext, is_fma_only_mode
from llmdbenchmark.smoketests.autoscaling import ScaleTarget
from llmdbenchmark.smoketests.base import (
    BaseSmoketest,
    _load_config,
    _model_id_label,
    _nested_get,
)
from llmdbenchmark.smoketests.report import CheckResult, SmoketestReport
from llmdbenchmark.utilities.kube_helpers import _pod_crash_details

# --- FMA serving signals -----------------------------------------------------
# When the dual-pods controller BINDS a launcher to a requester it stamps the
# launcher pod with binding labels it manages itself -- `dual-pods.llm-d.ai/dual`
# (the bound requester pod name) and `dual-pods.llm-d.ai/sleeping` -- and it ADDS
# the InferenceServerConfig.modelServerConfig routing labels. It strips the
# binding labels again on unbind.
#
# NOTE the guide runs a POOL of launchers (LauncherPopulationPolicy
# launcherCount=1 per GPU node), not a single launcher; with requester
# replicas=N there are N bound launchers. Any bound launcher serves the same
# model, so probing pods[0] of the `dual`-selected set is sufficient.
_FMA_LAUNCHER_COMPONENT_SELECTOR = "app.kubernetes.io/component=launcher"
# Requester Deployment name: `fma-requester` (guide) or
# `fma-requester-{model_id_label}` (fma method).
_FMA_REQUESTER_PREFIX = "fma-requester"
# Set by the controller on a launcher when it binds to a requester; removed on
# unbind.
_FMA_BOUND_LABEL = "dual-pods.llm-d.ai/dual"
# Written onto the launcher pod by the state-change-reflector sidecar
_FMA_SIGNATURE_ANNOTATION = "dual-pods.llm-d.ai/vllm-instance-signature"
# FYI-only signal set by the controller on the launcher pod; it trails
# the actual /sleep and /wake_up calls
_FMA_SLEEPING_LABEL = "dual-pods.llm-d.ai/sleeping"


def _fma_serving_selector() -> str:
    """Selector matching a launcher pod bound to a requester (serving)."""
    return f"{_FMA_LAUNCHER_COMPONENT_SELECTOR},{_FMA_BOUND_LABEL}"


def _fma_pods(cmd, namespace: str, selector: str) -> list[dict]:
    """Pod items (from ``kubectl get pods -o json``) matching *selector*."""
    return _fma_list_pods(cmd, namespace, selector) or []


def _fma_list_pods(cmd, namespace: str, selector: str) -> list[dict] | None:
    """Like :func:`_fma_pods`, but None when kubectl fails or returns invalid
    JSON, so callers can tell a failed listing from an empty one."""
    result = cmd.kube(
        "get",
        "pods",
        "-l",
        selector,
        "--namespace",
        namespace,
        "-o",
        "json",
        check=False,
    )
    if not result.success:
        return None
    if not (result.stdout or "").strip():
        return []

    try:
        return loads(result.stdout).get("items", [])
    except JSONDecodeError:
        return None


def _fma_serving_pod(cmd, namespace: str) -> dict | None:
    """The launcher pod currently bound and serving, if any."""
    pods = _fma_pods(cmd, namespace, _fma_serving_selector())
    return pods[0] if pods else None


def _fma_pod_ip(pod: dict | None) -> str:
    """status.podIP of *pod*, or "" if absent."""
    return ((pod or {}).get("status", {}) or {}).get("podIP", "") or ""


def _fma_launcher_crashes(
    pods: list[dict], bound_names: frozenset[str] = frozenset()
) -> tuple[list[str], list[str]]:
    """Crash details for *pods*, split into (bound, idle) launchers.

    A pod is bound when it carries the bound label or is named in
    *bound_names* (pods already observed bound, whatever this listing shows).
    Each detail names the node so a bad-GPU node can be cordoned straight from
    the CI log.
    """
    bound: list[str] = []
    idle: list[str] = []
    for pod in pods:
        details = _pod_crash_details(pod)
        if not details:
            continue
        node = (pod.get("spec", {}) or {}).get("nodeName") or "<unscheduled>"
        metadata = pod.get("metadata", {}) or {}
        labels = metadata.get("labels", {}) or {}
        is_bound = _FMA_BOUND_LABEL in labels or metadata.get("name", "") in bound_names
        target = bound if is_bound else idle
        target.extend(f"{detail} on node {node}" for detail in details)
    return bound, idle


def _fma_skip_report(check_name: str) -> SmoketestReport:
    """A passing report recording that launcher checks were skipped because
    no launcher is expected at smoketest (benchmark fma, requester=0)."""
    report = SmoketestReport()
    report.add(
        CheckResult(
            check_name,
            True,
            message=("No bound launcher at smoketest"),
        )
    )
    return report


def _log_fma_sleeping(context: ExecutionContext, pod: dict) -> None:
    """Log the FYI-only dual-pods sleeping label (trails the real /sleep,
    /wake_up calls, so it is never asserted on)."""
    labels = (pod or {}).get("metadata", {}).get("labels", {}) or {}
    if _FMA_SLEEPING_LABEL in labels:
        context.logger.log_info(
            f"[FYI] {_FMA_SLEEPING_LABEL}={labels[_FMA_SLEEPING_LABEL]} on "
            f"{pod.get('metadata', {}).get('name', '')}"
        )


class FmaValidator(BaseSmoketest):
    """Smoketest for the FMA (fast-model-actuation) standup path."""

    # How long to wait for the launcher to (un)bind after a scale change.
    _FMA_SERVING_TIMEOUT = 300
    # How long to wait for the state-change-reflector sidecar to write its
    # signature annotation.
    _FMA_ANNOTATION_TIMEOUT = 90
    # Shared poll interval for both waits above.
    _FMA_POLL = 5

    def _launcher_created_on_demand(
        self,
        context: ExecutionContext,
        cmd: CommandExecutor,
        plan_config: dict,
    ) -> bool:
        """True when no bound launcher is expected at smoketest time."""
        if cmd.dry_run or "fma" not in (context.deployed_methods or []):
            return False
        requester_replicas = _nested_get(plan_config, "fma", "requester", "replicas")
        return (requester_replicas or 0) == 0

    def _is_stack_target(
        self, target: ScaleTarget, plan_config: dict, context: ExecutionContext
    ) -> bool:
        """Also matches the fma method's requester, which has no guide label,
        by its exact name."""
        if super()._is_stack_target(target, plan_config, context):
            return True
        model_id_label = _model_id_label(plan_config)
        return (
            "fma" in (context.deployed_methods or [])
            and bool(model_id_label)
            and target.kind == "Deployment"
            and target.name == f"{_FMA_REQUESTER_PREFIX}-{model_id_label}"
        )

    def run_health_checks(
        self,
        context: ExecutionContext,
        stack_path: Path,
    ) -> SmoketestReport:
        """Health checks against the bound launcher pod.

        FMA has no Service/gateway; a launcher pod hosts vLLM and is bound to a
        requester on demand. We assert three things the generic path misses:
          1. No BOUND launcher pod is in a container-level crash state
             (OOMKilled/CrashLoopBackOff/image-pull/non-zero exit). Idle pool
             launchers (one per GPU node) only log a warning: a single bad
             node must not fail a stack whose serving launcher is healthy.
          2. The bound launcher carries the state-change-reflector's signature
             annotation.
          3. vLLM answers /v1/models on the launcher pod IP:port.
        """
        report = SmoketestReport()
        cmd = context.require_cmd()
        namespace = context.require_namespace()
        plan_config = _load_config(stack_path)

        model_name = _nested_get(plan_config, "model", "name") or ""
        port = str(_nested_get(plan_config, "vllmCommon", "inferencePort") or "8000")

        if self._launcher_created_on_demand(context, cmd, plan_config):
            return _fma_skip_report("fma_health_skipped_no_launcher")

        launcher_pods = _fma_pods(cmd, namespace, _FMA_LAUNCHER_COMPONENT_SELECTOR)
        if not launcher_pods and not cmd.dry_run:
            report.add(
                CheckResult(
                    "fma_launcher_pods_exist",
                    False,
                    message=(
                        f"No launcher pods found "
                        f"(selector '{_FMA_LAUNCHER_COMPONENT_SELECTOR}')"
                    ),
                )
            )
            return report

        # Requester at zero: launchers are idle, so only the crash scan applies.
        zeroed = self._scaled_to_zero_targets(cmd, context, namespace, plan_config)
        if zeroed:
            _, idle_crashed = _fma_launcher_crashes(launcher_pods)
            if idle_crashed:
                context.logger.log_warning(
                    f"Idle launcher pod(s) in a container crash state (not "
                    f"failing the stack; check the node): {'; '.join(idle_crashed)}"
                )
            report.add(
                CheckResult(
                    "fma_requester_scaled_to_zero",
                    True,
                    message=(
                        f"Requester scaled to 0 by "
                        f"{'; '.join(t.describe() for t in zeroed)}; no launcher "
                        f"is bound by design. The inference test exercises "
                        f"scale-from-zero through the router"
                    ),
                )
            )
            return report

        # Wait for the binding first: the crash scan below needs the bound
        # label to tell the serving launcher from idle pool members.
        serving_pod = self._fma_wait_serving(cmd, context, namespace)

        # 1. No BOUND launcher in a CONTAINER-level crash state. Catches a
        #    container OOMKill (kernel), CrashLoopBackOff, image-pull failure, or
        #    non-zero exit. Re-list so the states and labels are post-binding;
        #    never classify the pre-wait snapshot, whose labels predate binding.
        launcher_pods = _fma_list_pods(cmd, namespace, _FMA_LAUNCHER_COMPONENT_SELECTOR)
        if launcher_pods is None:
            report.add(
                CheckResult(
                    "fma_launcher_pods_listed",
                    False,
                    message=(
                        f"Could not re-list launcher pods after the binding wait "
                        f"(selector '{_FMA_LAUNCHER_COMPONENT_SELECTOR}'); "
                        f"cannot tell bound from idle launchers"
                    ),
                )
            )
            return report
        bound_names: frozenset[str] = frozenset()
        if serving_pod is not None:
            serving_name = serving_pod.get("metadata", {}).get("name", "")
            bound_names = frozenset({serving_name})
            if all(
                p.get("metadata", {}).get("name") != serving_name for p in launcher_pods
            ):
                launcher_pods.append(serving_pod)
        bound_crashed, idle_crashed = _fma_launcher_crashes(launcher_pods, bound_names)
        if idle_crashed:
            context.logger.log_warning(
                f"Idle launcher pod(s) in a container crash state (not failing "
                f"the stack; check the node): {'; '.join(idle_crashed)}"
            )

        # 2. The bound launcher must be serving and carry the signature annotation.
        if serving_pod is None:
            crashed = bound_crashed + idle_crashed
            report.add(
                CheckResult(
                    "fma_launcher_bound",
                    False,
                    message=(
                        f"No launcher pod became bound/serving for model "
                        f"'{model_name}' within {self._FMA_SERVING_TIMEOUT}s "
                        f"(selector '{_fma_serving_selector()}')"
                        + (
                            f"; launcher pod(s) in a container crash state: "
                            f"{'; '.join(crashed)}"
                            if crashed
                            else ""
                        )
                    ),
                )
            )
            return report

        report.add(
            CheckResult(
                "fma_launcher_no_crash",
                not bound_crashed,
                message=(
                    "Bound launcher pod(s) healthy (no container crash states)"
                    if not bound_crashed
                    else (
                        f"Bound launcher pod(s) in a container crash state: "
                        f"{'; '.join(bound_crashed)}"
                    )
                ),
            )
        )

        pod_name = serving_pod.get("metadata", {}).get("name", "")
        _log_fma_sleeping(context, serving_pod)
        annotation_ok = self._fma_wait_signature_annotation(
            cmd, context, namespace, pod_name
        )
        report.add(
            CheckResult(
                "fma_signature_annotation",
                annotation_ok,
                message=(
                    f"Signature annotation '{_FMA_SIGNATURE_ANNOTATION}' present on {pod_name}"
                    if annotation_ok
                    else (
                        f"Signature annotation '{_FMA_SIGNATURE_ANNOTATION}' missing on "
                        f"{pod_name} after {self._FMA_ANNOTATION_TIMEOUT}s"
                    )
                ),
            )
        )

        # 3. vLLM answers /v1/models on the launcher pod IP:port.
        pod_ip = _fma_pod_ip(serving_pod)
        if not pod_ip and not cmd.dry_run:
            report.add(
                CheckResult(
                    "fma_launcher_ip",
                    False,
                    message=f"Bound launcher pod {pod_name} has no status.podIP",
                )
            )
            return report
        err = self._wait_for_model_ready(
            cmd,
            context,
            namespace,
            pod_ip or "<dry-run-ip>",
            port,
            model_name,
            plan_config,
        )
        report.add(
            CheckResult(
                "fma_model_ready",
                err is None,
                message=(
                    f"vLLM serving '{model_name}' at {pod_ip}:{port}"
                    if err is None
                    else err
                ),
            )
        )
        return report

    def run_inference_test(
        self,
        context: ExecutionContext,
        stack_path: Path,
    ) -> SmoketestReport:
        """Baseline inference check against the bound launcher pod.

        Sends one request to the bound launcher pod as a fail-fast serving gate
        before the (expensive) run phase.
        """
        report = SmoketestReport()
        cmd = context.require_cmd()
        namespace = context.require_namespace()
        plan_config = _load_config(stack_path)

        model_name = _nested_get(plan_config, "model", "name") or ""
        port = str(_nested_get(plan_config, "vllmCommon", "inferencePort") or "8000")

        # Requester at zero: go through the router (a launcher pod request never
        # triggers the scale-up). Checked before the plan-based skip, since a
        # plan starting at 0 can still be autoscaled. FMA-only has no router.
        if self._scaled_to_zero_targets(cmd, context, namespace, plan_config):
            if is_fma_only_mode(context):
                return _fma_skip_report("fma_inference_skipped_no_router")
            return super().run_inference_test(context, stack_path)

        if self._launcher_created_on_demand(context, cmd, plan_config):
            return _fma_skip_report("fma_inference_skipped_no_launcher")

        # The launcher must be bound+serving before we send the baseline request.
        pod = self._fma_wait_serving(cmd, context, namespace)
        if pod is None:
            report.add(
                CheckResult(
                    "fma_inference_endpoint",
                    False,
                    message=(
                        f"No bound launcher pod serving '{model_name}' "
                        f"for the inference test"
                    ),
                )
            )
            return report

        ok, msg = self._fma_request_once(
            cmd,
            context,
            namespace,
            _fma_pod_ip(pod),
            port,
            model_name,
            plan_config,
            label="baseline",
        )
        report.add(CheckResult("fma_inference_baseline", ok, message=msg))
        return report

    def _fma_request_once(
        self,
        cmd: CommandExecutor,
        context: ExecutionContext,
        namespace: str,
        pod_ip: str,
        port: str,
        model_name: str,
        plan_config: dict,
        *,
        label: str,
    ) -> tuple[bool, str]:
        """Send one /v1/completions (falling back to /v1/chat/completions) to the
        launcher pod IP:port. Returns (passed, message)."""
        if not pod_ip and not cmd.dry_run:
            return False, f"FMA {label} request: launcher pod has no IP"
        base_url = f"http://{pod_ip or '<dry-run-ip>'}:{port}"
        context.logger.log_info(f"FMA {label} inference against {base_url}...")
        result = self._try_completions(
            cmd, context, namespace, base_url, model_name, plan_config
        )
        if result.get("success"):
            return True, (
                f"FMA {label} inference passed via /v1/completions -- "
                f'Generated: "{result.get("generated_text", "")}"'
            )

        if result.get("should_fallback"):
            chat = self._try_chat_completions(
                cmd, context, namespace, base_url, model_name, plan_config
            )
            if chat.get("success"):
                return True, (
                    f"FMA {label} inference passed via /v1/chat/completions -- "
                    f'Generated: "{chat.get("generated_text", "")}"'
                )

            return False, (
                f"FMA {label} inference failed: /v1/completions: "
                f"{result.get('error')}; /v1/chat/completions: {chat.get('error')}"
            )

        return False, (
            f"FMA {label} inference failed: {result.get('error', 'unknown error')}"
        )

    def _fma_wait_serving(
        self,
        cmd: CommandExecutor,
        context: ExecutionContext,
        namespace: str,
        timeout: int | None = None,
    ) -> dict | None:
        """Poll until a launcher pod is bound+serving (routing labels present +
        a podIP). Returns the pod dict on success or None on timeout."""
        if cmd.dry_run:
            return {
                "metadata": {"name": "<dry-run-launcher>"},
                "status": {"podIP": "<dry-run-ip>"},
            }

        timeout = timeout or self._FMA_SERVING_TIMEOUT
        start = perf_counter()
        while True:
            pod = _fma_serving_pod(cmd, namespace)
            if pod is not None and _fma_pod_ip(pod):
                return pod
            elapsed = perf_counter() - start
            if elapsed >= timeout:
                return None
            context.logger.log_info(
                f"Waiting for a launcher to bind+serve ({int(elapsed)}s/{timeout}s)..."
            )
            sleep(self._FMA_POLL)

    def _fma_wait_signature_annotation(
        self,
        cmd: CommandExecutor,
        context: ExecutionContext,
        namespace: str,
        pod_name: str,
        timeout: int | None = None,
    ) -> bool:
        """Poll for the state-change-reflector's signature annotation on the
        launcher pod. Returns True once present, False on timeout."""
        if cmd.dry_run:
            return True

        timeout = timeout or self._FMA_ANNOTATION_TIMEOUT
        start = perf_counter()
        while True:
            result = cmd.kube(
                "get",
                "pod",
                pod_name,
                "--namespace",
                namespace,
                "-o",
                "json",
                check=False,
            )
            if result.success and (result.stdout or "").strip():
                try:
                    pod = loads(result.stdout)
                except JSONDecodeError:
                    pod = {}
                if _FMA_SIGNATURE_ANNOTATION in self.get_pod_annotations(pod):
                    return True
            if perf_counter() - start >= timeout:
                return False
            sleep(self._FMA_POLL)
