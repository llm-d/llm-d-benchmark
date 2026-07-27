"""Step 09a -- Capture WVA/KEDA + FMA cluster state right after results are collected.

Snapshots HPA/ScaledObject/Deployment YAML, events, controller logs, and the
logs of every pod in the deploy and WVA namespaces to ``results/<exp_id>/``
— alongside the harness's benchmark output. Skipped only when neither
+``wva.enabled`` nor ``eppKedaSaturation.enabled`` is set.

Runs AFTER step_09_collect_results so the local results dir already exists
and won't trip step_09's "skip if results dir non-empty" gate.
"""

import time
from pathlib import Path

from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.executor.step import Phase, Step, StepResult

# Kubelet/API-server transient errors when the
# pod-log symlink path is being rotated mid-call.
_KUBELET_LOG_SENTINELS = (
    "failed to try resolving symlinks",
    "unable to retrieve container logs",
    "Error from server",
)


def _is_kubelet_log_sentinel(stdout: str) -> bool:
    head = stdout[:200].strip()
    return any(head.startswith(s) for s in _KUBELET_LOG_SENTINELS)


def _kube_logs_with_retry(cmd, *args, attempts: int = 3, backoff: float = 2.0):
    """Run `kubectl logs <args>` with retry on the kubelet symlink-rotation race.
    Retrying a few seconds later almost always succeeds
    since rotation completes in well under a second.
    """
    last = None
    for i in range(attempts):
        last = cmd.kube("logs", *args, check=False)
        if not last.success or not last.stdout:
            return last
        if not _is_kubelet_log_sentinel(last.stdout):
            return last
        if i < attempts - 1:
            time.sleep(backoff)
    return last


class CaptureClusterStateStep(Step):
    """Snapshot HPA/ScaledObject/Deployment/events + WVA controller logs to workspace."""

    def __init__(self):
        super().__init__(
            number=9,
            name="capture_cluster_state",
            description="Capture WVA HPA/ScaledObject/Deployment state and controller logs",
            phase=Phase.RUN,
            per_stack=True,
        )

    def should_skip(self, context: ExecutionContext) -> bool:
        if "nok8s" in (context.deployed_methods or []):
            return True
        return context.harness_skip_run

    def execute(
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

        stack_name = stack_path.name
        cmd = context.require_cmd()

        plan_config = self._load_stack_config(stack_path)
        # Capture for any KEDA-driven autoscaling path.
        wva_enabled = self._resolve(plan_config, "wva.enabled", default=False)
        epp_keda_enabled = self._resolve(
            plan_config, "eppKedaSaturation.enabled", default=False
        )
        if not (wva_enabled or epp_keda_enabled):
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=True,
                message=(
                    "wva.enabled and eppKedaSaturation.enabled both false; "
                    "skipping cluster-state capture"
                ),
                stack_name=stack_name,
            )

        deploy_ns = context.namespace or self._resolve(plan_config, "namespace.name")
        wva_ns = self._resolve(plan_config, "wva.namespace", default="") or deploy_ns
        if not deploy_ns:
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=False,
                message="No namespace available for cluster-state capture",
                errors=["namespace is required"],
                stack_name=stack_name,
            )

        exp_ids = context.experiment_ids or []
        exp_id = exp_ids[0] if exp_ids else stack_name
        out_dir = context.run_results_dir() / exp_id
        out_dir.mkdir(parents=True, exist_ok=True)

        captured: list[str] = []
        warnings: list[str] = []

        # 1. Full YAML of WVA/KEDA-relevant resources in the deploy namespace.

        result = cmd.kube(
            "get",
            "pods,hpa,scaledobjects.keda.sh,triggerauthentications.keda.sh,"
            "deployments,replicasets",
            "--namespace",
            deploy_ns,
            "-o",
            "yaml",
            check=False,
        )
        if result.success:
            (out_dir / "resources.yaml").write_text(result.stdout, encoding="utf-8")
            captured.append("resources.yaml")
        else:
            warnings.append(f"get resources failed: {result.stderr[:200]}")

        # 2. HPA describe — last scaling decision is in the events section.
        result = cmd.kube("describe", "hpa", "--namespace", deploy_ns, check=False)
        if result.success:
            (out_dir / "hpa-describe.txt").write_text(result.stdout, encoding="utf-8")
            captured.append("hpa-describe.txt")
        else:
            warnings.append(f"describe hpa failed: {result.stderr[:200]}")

        # 3. HPA wide table — glanceable TARGETS + REPLICAS columns.
        # Under the modern annotation-based path (no VariantAutoscaling CR),
        # `kubectl get hpa -o wide` shows the same OPTIMIZED/METRICSREADY
        # signals via TARGETS (current/target metric value).
        result = cmd.kube(
            "get", "hpa", "--namespace", deploy_ns, "-o", "wide", check=False
        )
        if result.success:
            (out_dir / "hpa.txt").write_text(result.stdout, encoding="utf-8")
            captured.append("hpa.txt")
        else:
            warnings.append(f"get hpa failed: {result.stderr[:200]}")

        # 3b. ScaledObject (KEDA) state -- READY / ACTIVE / TRIGGERS columns
        result = cmd.kube(
            "get",
            "scaledobjects.keda.sh",
            "--namespace",
            deploy_ns,
            "-o",
            "wide",
            check=False,
        )
        if result.success:
            (out_dir / "scaledobject.txt").write_text(result.stdout, encoding="utf-8")
            captured.append("scaledobject.txt")
        else:
            warnings.append(f"get scaledobject failed: {result.stderr[:200]}")

        result = cmd.kube(
            "describe",
            "scaledobjects.keda.sh",
            "--namespace",
            deploy_ns,
            check=False,
        )
        if result.success:
            (out_dir / "scaledobject-describe.txt").write_text(
                result.stdout, encoding="utf-8"
            )
            captured.append("scaledobject-describe.txt")
        else:
            warnings.append(f"describe scaledobject failed: {result.stderr[:200]}")

        # 4. Events — sorted by time so a HPA scale-up event is easy to spot.
        result = cmd.kube(
            "get",
            "events",
            "--namespace",
            deploy_ns,
            "--sort-by=.lastTimestamp",
            check=False,
        )
        if result.success:
            (out_dir / "events.log").write_text(result.stdout, encoding="utf-8")
            captured.append("events.log")
        else:
            warnings.append(f"get events failed: {result.stderr[:200]}")

        # 4b. Events as JSON — the table above uses relative "LAST SEEN" ages,
        # which can't be parsed for timing. JSON carries absolute timestamps so
        # the display job can measure T_scale_up from each HPA SuccessfulRescale
        # scale-up event to the new replica's Ready.
        result = cmd.kube(
            "get", "events", "--namespace", deploy_ns, "-o", "json", check=False
        )
        if result.success:
            (out_dir / "events.json").write_text(result.stdout, encoding="utf-8")
            captured.append("events.json")
        else:
            warnings.append(f"get events json failed: {result.stderr[:200]}")

        # 5. WVA controller logs — every reconcile loop logs the OPTIMIZED
        # replica count it computed. Capped to keep the upload reasonable;
        # bump --tail if we ever need full forensic depth.
        result = _kube_logs_with_retry(
            cmd,
            "deployment/wva-controller-manager",
            "--namespace",
            wva_ns,
            "--tail=50000",
        )
        if (
            result.success
            and result.stdout
            and not _is_kubelet_log_sentinel(result.stdout)
        ):
            (out_dir / "wva-controller.log").write_text(result.stdout, encoding="utf-8")
            captured.append("wva-controller.log")
        elif result.success and result.stdout:
            (out_dir / "wva-controller.kubelet-error").write_text(
                result.stdout, encoding="utf-8"
            )
            warnings.append(
                "logs wva-controller-manager: kubelet sentinel after retries"
            )
        else:
            warnings.append(
                f"logs wva-controller-manager failed: {result.stderr[:200]}"
            )

        # 6. Pod snapshot — replica count + node placement at end-of-run.
        result = cmd.kube(
            "get", "pods", "--namespace", deploy_ns, "-o", "wide", check=False
        )
        if result.success:
            (out_dir / "pods.txt").write_text(result.stdout, encoding="utf-8")
            captured.append("pods.txt")
        else:
            warnings.append(f"get pods failed: {result.stderr[:200]}")

        # 7. Thanos diagnostic queries — proves whether vLLM saturation
        # series in Thanos actually carry the `llm_d_ai_variant` label our
        # PodMonitor relabel is supposed to lift. WVA's saturation engine
        # joins on this label; missing-or-empty means the engine emits
        # "Skipping pod that doesn't match any scale target" even when the
        # pod has the right metadata. Compare baseline-vs-FMA captures of
        # this file to isolate which side of the chain breaks.
        thanos_proxy = (
            "/api/v1/namespaces/openshift-monitoring/services/"
            "thanos-querier:web/proxy/api/v1/query"
        )
        thanos_queries = [
            (
                "vllm-cache-with-variant",
                'vllm:gpu_cache_usage_perc{llm_d_ai_variant=~".+"}',
            ),
            ("vllm-cache-no-variant", 'vllm:gpu_cache_usage_perc{llm_d_ai_variant=""}'),
            ("vllm-cache-all", "vllm:gpu_cache_usage_perc"),
            (
                "epp-pool-kv-cache",
                "inference_pool_average_kv_cache_utilization",
            ),
            ("epp-pool-queue-size", "inference_pool_average_queue_size"),
        ]
        for label, query in thanos_queries:
            result = cmd.kube(
                "get", "--raw", f"{thanos_proxy}?query={query}", check=False
            )
            if result.success:
                (out_dir / f"thanos-{label}.json").write_text(
                    result.stdout, encoding="utf-8"
                )
                captured.append(f"thanos-{label}.json")
            else:
                (out_dir / f"thanos-{label}.error").write_text(
                    result.stderr, encoding="utf-8"
                )
                warnings.append(f"thanos query '{label}' failed: {result.stderr[:200]}")

        # 9. Pod logs for controllers and serving pods. Iterate over every pod
        # in deploy_ns (and the WVA ns if it differs) and dump --tail=5000 per container.
        log_namespaces = {deploy_ns}
        if wva_ns and wva_ns != deploy_ns:
            log_namespaces.add(wva_ns)

        pod_log_count = 0
        for ns in log_namespaces:
            # `-o name` outputs `pod/<name>` lines — robust across shells and
            # avoids jsonpath-quoting pitfalls (kubectl jsonpath's `'\n'` was
            # silently producing literal `\n` instead of a newline, leaving
            # this loop with an empty pod list).
            pods_result = cmd.kube(
                "get", "pods", "--namespace", ns, "-o", "name", check=False
            )
            if not pods_result.success:
                warnings.append(f"list pods in {ns} failed: {pods_result.stderr[:200]}")
                continue
            for line in pods_result.stdout.splitlines():
                pod_name = line.strip().removeprefix("pod/")
                if not pod_name:
                    continue
                log_result = _kube_logs_with_retry(
                    cmd,
                    pod_name,
                    "--namespace",
                    ns,
                    "--all-containers=true",
                    "--tail=5000",
                )
                if log_result.success and log_result.stdout:
                    # If retries also returned the kubelet sentinel, park
                    # the capture under `.kubelet-error` rather than `.log`
                    # so the parser doesn't ingest the error string as
                    # fake logs.
                    is_kubelet_error = _is_kubelet_log_sentinel(log_result.stdout)
                    suffix = ".kubelet-error" if is_kubelet_error else ".log"
                    log_path = out_dir / f"{ns}__{pod_name}{suffix}"
                    log_path.write_text(log_result.stdout, encoding="utf-8")
                    if not is_kubelet_error:
                        pod_log_count += 1

        if pod_log_count > 0:
            captured.append(f"{pod_log_count} pod log(s)")

        for w in warnings:
            context.logger.log_warning(f"cluster-state: {w}")

        if not captured:
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=False,
                message="No cluster state could be captured",
                errors=warnings,
                stack_name=stack_name,
            )

        context.logger.log_info(
            f"cluster-state: captured {len(captured)} file(s) to {out_dir}"
        )
        return StepResult(
            step_number=self.number,
            step_name=self.name,
            success=True,
            message=f"Captured {', '.join(captured)} to cluster-state/{stack_name}/",
            stack_name=stack_name,
        )
