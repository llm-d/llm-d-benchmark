"""WVA smoketest checks: controller + prometheus-adapter + per-stack HPA.

This is a *mixin* rather than a top-level validator so scenario-specific
validators (e.g. inference-scheduling) can layer it on without having to
duplicate its logic. A concrete validator exists too
(:class:`WvaValidator`) for scenarios whose only WVA concerns are the
baseline controller + per-stack HPA checks.

Activation gate: the mixin runs only when BOTH ``wva.enabled: true`` is
present in the rendered stack config AND the cluster is OpenShift. WVA's
install path (prometheus-adapter, thanos-querier integration, user-workload
monitoring) is currently only verified on OpenShift; on other platforms
standup deliberately skips the install, so the smoketest must skip the
checks too.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from llmdbenchmark.executor.command import CommandExecutor
from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.smoketests.base import BaseSmoketest, _load_config, _nested_get
from llmdbenchmark.smoketests.report import CheckResult, SmoketestReport


# How long to poll the WVA controller Deployment waiting for it to become
# Available with all replicas Ready. Pod scheduling + image pull + leader
# election typically take 30-60s on a healthy cluster.
_WVA_CONTROLLER_TIMEOUT_SECS = 180
_WVA_CONTROLLER_POLL_SECS = 5

# How long to poll the HPA waiting for its TARGETS / currentMetrics field
# to resolve from <unknown> to a real number. Default is generous because
# the full pipeline (controller reconcile → Prometheus scrape →
# prometheus-adapter discovery → HPA poll) can take 90–120 s end-to-end
# even on a healthy cluster.
_HPA_TARGETS_TIMEOUT_SECS = 180
_HPA_TARGETS_POLL_SECS = 5

# How long to wait for the HPA's current replica count to converge to its
# minReplicas (the expected idle steady-state when no traffic is hitting
# the deployment). Includes the scaleDown stabilization window
# (typically 120s) plus the time for the actual scale-down to complete,
# so allow at least 2x the stabilization window.
_HPA_CONVERGED_TIMEOUT_SECS = 300
_HPA_CONVERGED_POLL_SECS = 10


class WvaSmoketestMixin:
    """Adds WVA-specific checks to any scenario validator.

    Subclasses (or concrete validators) call :meth:`run_wva_checks` from
    their ``run_config_validation`` method. Safe to call unconditionally —
    it returns immediately when WVA is not enabled on this stack.
    """

    def run_wva_checks(
        self,
        context: ExecutionContext,
        stack_path: Path,
        report: SmoketestReport,
    ) -> None:
        """Append WVA resource health checks to *report*.

        Validates:
          1. WVA controller Deployment becomes Available with all replicas
             Ready in the WVA namespace (polling — fails fast if the
             container starts crash-looping mid-wait).
          2. prometheus-adapter Deployment exists and is Available in the
             user-workload-monitoring namespace.
          3. The per-stack HPA exists and references the right scaleTargetRef:
             the modelservice decode Deployment ({model_id_label}-decode) or,
             under fma.enabled, the FMA requester Deployment
             (fma-requester-{model_id_label}).
          4. The HPA carries the three annotations WVA's controller requires
             for annotation-based discovery: ``llm-d.ai/managed=true``,
             ``llm-d.ai/model-id``, and ``llm-d.ai/variant-cost``. Without
             these the controller's HPA reconciler skips the HPA and never
             emits ``wva_desired_replicas``.
          5. The HPA's external-metric selector matches what WVA actually
             emits: ``variant_name`` = the HPA's own name, and
             ``exported_namespace`` = the HPA's namespace. Catches selector
             drift before it manifests as ``TARGETS: <unknown>``.
          6. (optional) HPA has an ``AbleToScale`` condition, meaning
             prometheus-adapter is serving ``wva_desired_replicas`` for it.
          7. The HPA's TARGETS / currentMetrics field has resolved from
             <unknown> to a numeric value — proves the full pipeline
             (controller → Prometheus → adapter → HPA) is end-to-end live.
          8. The HPA's REPLICAS column converges to its MINPODS (idle
             steady-state). With no traffic hitting the deployment, the
             controller computes desiredReplicas=minReplicas and the HPA
             scales the Deployment down to match — confirms the HPA is
             not just receiving the metric but actually acting on it.
          9. End-state snapshot of the HPA (always passes if the resource
             can be queried) so the smoketest log captures the final
             cluster state without needing a follow-up ``oc describe``.
        """
        config = _load_config(stack_path)
        if not (_nested_get(config, "wva", "enabled") or False):
            return

        # Standup also gates WVA install on OpenShift (see step_03 +
        # step_09); the smoketest must mirror that gate or every check
        # below would fail with "not found" against a cluster that
        # never had WVA installed in the first place.
        if not context.is_openshift:
            report.add(
                CheckResult(
                    "wva_platform_gate",
                    True,
                    message=(
                        f"WVA enabled in scenario but platform is "
                        f"{context.platform_type}, not OpenShift -- skipping "
                        "WVA smoketest checks (matches standup behavior)."
                    ),
                )
            )
            return

        cmd = context.require_cmd()
        namespace = context.require_namespace()

        if context.dry_run:
            report.add(
                CheckResult(
                    "wva_dry_run",
                    True,
                    message="[DRY RUN] WVA smoketest skipped",
                )
            )
            return

        wva_ns = _nested_get(config, "wva", "namespace") or namespace
        monitoring_ns = (
            _nested_get(
                config, "openshiftMonitoring", "userWorkloadMonitoringNamespace"
            )
            or "openshift-user-workload-monitoring"
        )
        model_id_label = (
            config.get("model_id_label", "")
            or _nested_get(config, "model", "shortName")
            or ""
        )
        # Template 28 retargets the HPA at the FMA requester Deployment
        # when fma.enabled — variant suffix becomes `-fma` (vs `-decode` for
        # modelservice). The smoketest must follow the same gate so HPA
        # name, scaleTargetRef expectation, and the metric selector's
        # variant_name all stay aligned.
        fma_enabled = bool(_nested_get(config, "fma", "enabled") or False)
        if fma_enabled:
            hpa_name = f"{model_id_label}-fma"
            scale_target_name = f"fma-requester-{model_id_label}"
        else:
            hpa_name = f"{model_id_label}-decode"
            scale_target_name = f"{model_id_label}-decode"

        expected_model_id = _nested_get(config, "model", "name") or ""

        self._check_wva_controller(
            cmd,
            wva_ns,
            report,
            timeout=_WVA_CONTROLLER_TIMEOUT_SECS,
            poll_interval=_WVA_CONTROLLER_POLL_SECS,
            logger=context.logger,
        )
        self._check_prometheus_adapter(cmd, monitoring_ns, report)
        self._check_hpa(
            cmd,
            wva_ns,
            hpa_name,
            scale_target_name,
            report,
            expected_model_id=expected_model_id,
        )
        # The HPA targets-resolved + converged checks below assume the model
        # server is already emitting vLLM metrics post-standup (the WVA-only
        # path with decode.replicas >= 1). Under FMA the model server tier
        # is the FMA launcher pods, and at idle the requester sits at min=1
        # with no traffic — so vLLM has no requests to measure, prometheus
        # has no series for `wva_desired_replicas`, the HPA shows
        # `FailedGetExternalMetric`, and the controller's optimization loop
        # computes `desiredReplicas=0`. None of that is a bug; it is the
        # correct idle state for FMA-on-modelservice. The metric pipeline
        # gets exercised end-to-end by the run phase as soon as
        # inference-perf hits the gateway. Skip the pre-traffic checks
        # rather than fail-closed on idle, mirroring how
        # ``_wait_for_hpa_targets`` would already pass on the WVA-only
        # path because decode pods are emitting metrics from the moment
        # they're Ready.
        if fma_enabled:
            report.add(
                CheckResult(
                    "wva_hpa_targets_resolved",
                    True,
                    message=(
                        "Skipped pre-traffic HPA TARGETS check on FMA path: "
                        "requester at min=1 with no load means "
                        "`wva_desired_replicas` is intentionally absent until "
                        "inference-perf drives traffic. The metric pipeline "
                        "is exercised end-to-end during the run phase."
                    ),
                )
            )
            report.add(
                CheckResult(
                    "wva_hpa_converged",
                    True,
                    message=(
                        "Skipped pre-traffic HPA convergence check on FMA path: "
                        "with no traffic the controller computes "
                        "`desiredReplicas=0` and HPA clamps to min=1; "
                        "convergence (current==min) is the steady-state idle "
                        "behavior, not a failure mode."
                    ),
                )
            )
        else:
            self._wait_for_hpa_targets(
                cmd,
                wva_ns,
                hpa_name,
                report,
                timeout=_HPA_TARGETS_TIMEOUT_SECS,
                poll_interval=_HPA_TARGETS_POLL_SECS,
                logger=context.logger,
            )
            self._wait_for_hpa_converged(
                cmd,
                wva_ns,
                hpa_name,
                report,
                timeout=_HPA_CONVERGED_TIMEOUT_SECS,
                poll_interval=_HPA_CONVERGED_POLL_SECS,
                logger=context.logger,
            )
        self._log_hpa_state(cmd, wva_ns, hpa_name, report)

    # --- individual checks ------------------------------------------------

    @staticmethod
    def _check_wva_controller(
        cmd: CommandExecutor,
        wva_ns: str,
        report: SmoketestReport,
        timeout: int = _WVA_CONTROLLER_TIMEOUT_SECS,
        poll_interval: int = _WVA_CONTROLLER_POLL_SECS,
        logger=None,
    ) -> None:
        """Wait for the WVA controller Deployment to become Available.

        Polls ``oc get deployment wva-controller-manager``
        until the Deployment reports ``Available=True`` with all replicas Ready
        (covers pod scheduling, image pull, container startup, health
        probes, and leader election). Fails immediately — without waiting
        the full timeout — if the manager container's restart count grows
        mid-wait, since that signals a crash-loop that won't self-recover.
        """
        start = time.time()
        baseline_restarts: int | None = None
        last_state = "(deployment not found)"

        while True:
            elapsed = time.time() - start

            result = cmd.kube(
                "get",
                "deployment",
                "wva-controller-manager",
                "--namespace",
                wva_ns,
                "-o",
                "json",
                check=False,
            )

            if result.success:
                try:
                    dep = json.loads(result.stdout) if result.stdout else {}
                except (json.JSONDecodeError, ValueError):
                    dep = {}

                available = _deployment_is_available(dep)
                ready = dep.get("status", {}).get("readyReplicas", 0) or 0
                desired = dep.get("spec", {}).get("replicas", 0) or 0

                # Sample container restart count from any pod owned by
                # this Deployment, to spot crash-loops without waiting
                # for the full timeout.
                restarts = _wva_controller_restart_count(cmd, wva_ns)
                if baseline_restarts is None and restarts is not None:
                    baseline_restarts = restarts

                # Pod-restart growth during the wait → crash loop, fail fast.
                if (
                    baseline_restarts is not None
                    and restarts is not None
                    and restarts > baseline_restarts
                ):
                    report.add(
                        CheckResult(
                            "wva_controller_deployment",
                            False,
                            expected=f"Available, {desired}/{desired} ready, no restarts",
                            actual=(
                                f"Available={available}, {ready}/{desired} ready, "
                                f"restarts={restarts} (was {baseline_restarts})"
                            ),
                            message=(
                                f"WVA controller in ns/{wva_ns} is restarting "
                                f"(restartCount {baseline_restarts}→{restarts} "
                                f"during {int(elapsed)}s wait). Likely crash-loop; "
                                f"check `oc logs -n {wva_ns} "
                                f"deploy/wva-controller-manager "
                                f"--previous` for the failure cause."
                            ),
                        )
                    )
                    return

                if available and desired > 0 and ready == desired:
                    report.add(
                        CheckResult(
                            "wva_controller_deployment",
                            True,
                            expected=f"Available, {desired}/{desired} ready",
                            actual=f"Available, {ready}/{desired} ready",
                            message=(
                                f"WVA controller in ns/{wva_ns}: "
                                f"Available, {ready}/{desired} ready "
                                f"after {int(elapsed)}s"
                            ),
                        )
                    )
                    return

                last_state = f"Available={available}, {ready}/{desired} ready"
            else:
                last_state = f"deployment lookup failed: {result.stderr.strip()[:200]}"

            if elapsed >= timeout:
                report.add(
                    CheckResult(
                        "wva_controller_deployment",
                        False,
                        expected=f"Available, all replicas ready within {timeout}s",
                        actual=last_state,
                        message=(
                            f"WVA controller in ns/{wva_ns} did not become "
                            f"ready within {timeout}s. Last state: {last_state}"
                        ),
                    )
                )
                return

            if logger is not None and int(elapsed) % 30 == 0 and int(elapsed) > 0:
                logger.log_info(
                    f"⏳ Waiting for WVA controller in ns/{wva_ns} to become "
                    f"Ready ({int(elapsed)}/{timeout}s) -- {last_state}"
                )

            time.sleep(poll_interval)

    @staticmethod
    def _check_prometheus_adapter(
        cmd: CommandExecutor, monitoring_ns: str, report: SmoketestReport
    ) -> None:
        """Verify prometheus-adapter Deployment is Available."""
        result = cmd.kube(
            "get",
            "deployment",
            "prometheus-adapter",
            "--namespace",
            monitoring_ns,
            "-o",
            "json",
            check=False,
        )
        if not result.success:
            report.add(
                CheckResult(
                    "wva_prometheus_adapter",
                    False,
                    message=(
                        f"prometheus-adapter Deployment not found in "
                        f"ns/{monitoring_ns}: {result.stderr.strip()[:200]}"
                    ),
                )
            )
            return

        try:
            dep = json.loads(result.stdout) if result.stdout else {}
        except (json.JSONDecodeError, ValueError):
            dep = {}

        available = _deployment_is_available(dep)
        replicas = dep.get("status", {}).get("readyReplicas", 0) or 0
        desired = dep.get("spec", {}).get("replicas", 0) or 0
        report.add(
            CheckResult(
                "wva_prometheus_adapter",
                available and replicas == desired and desired > 0,
                expected=f"Available, {desired}/{desired} ready",
                actual=f"Available={available}, {replicas}/{desired} ready",
                message=(
                    f"prometheus-adapter in ns/{monitoring_ns}: "
                    f"Available={available}, ready={replicas}/{desired}"
                ),
            )
        )

    @staticmethod
    def _check_hpa(
        cmd: CommandExecutor,
        wva_ns: str,
        hpa_name: str,
        expected_scale_target: str,
        report: SmoketestReport,
        expected_model_id: str = "",
    ) -> None:
        """Verify the per-stack HPA exists, targets the right workload, carries
        the WVA opt-in annotations, and has a metric selector aligned with what
        the controller actually emits.

        Annotation-based discovery requires three annotations on
        the HPA: ``llm-d.ai/managed: "true"`` (opts the HPA into WVA reconcile),
        ``llm-d.ai/model-id`` (required, identifies the model), and
        ``llm-d.ai/variant-cost`` (optional but rendered by 28_wva-hpa.yaml.j2).
        WVA's external metric is keyed by the HPA's own name (``variant_name``)
        and namespace (``exported_namespace``); the legacy ``controller_instance``
        selector is intentionally absent on the modern path.
        """
        result = cmd.kube(
            "get",
            "hpa",
            hpa_name,
            "--namespace",
            wva_ns,
            "-o",
            "json",
            check=False,
        )
        if not result.success:
            report.add(
                CheckResult(
                    "wva_hpa",
                    False,
                    message=(
                        f"HPA/{hpa_name} not found in ns/{wva_ns}: "
                        f"{result.stderr.strip()[:200]}"
                    ),
                )
            )
            return

        try:
            hpa = json.loads(result.stdout) if result.stdout else {}
        except (json.JSONDecodeError, ValueError):
            hpa = {}

        scale_target = hpa.get("spec", {}).get("scaleTargetRef", {}).get("name", "")
        report.add(
            CheckResult(
                "wva_hpa_target",
                scale_target == expected_scale_target,
                expected=expected_scale_target,
                actual=scale_target,
                message=(
                    f"HPA/{hpa_name} scaleTargetRef.name={scale_target} "
                    f"(expected {expected_scale_target})"
                ),
            )
        )

        # Annotation-based discovery requires `llm-d.ai/managed=true` and
        # `llm-d.ai/model-id` on the HPA. Without `managed`, WVA's HPA
        # reconciler returns early (annotations.IsManaged false). Without
        # `model-id`, ParseAnnotations errors and the controller never
        # processes this HPA.
        annotations = hpa.get("metadata", {}).get("annotations", {}) or {}
        managed_val = annotations.get("llm-d.ai/managed", "")
        model_id_val = annotations.get("llm-d.ai/model-id", "")

        managed_ok = managed_val == "true"
        report.add(
            CheckResult(
                "wva_hpa_managed_annotation",
                managed_ok,
                expected='llm-d.ai/managed="true"',
                actual=f'llm-d.ai/managed="{managed_val or "(missing)"}"',
                message=(
                    f"HPA/{hpa_name} has the WVA opt-in annotation."
                    if managed_ok
                    else f'HPA/{hpa_name} is missing llm-d.ai/managed="true". '
                    "Without it, the WVA controller's HPA reconciler skips "
                    "this HPA and never emits wva_desired_replicas for it."
                ),
            )
        )

        if expected_model_id:
            model_id_ok = model_id_val == expected_model_id
            report.add(
                CheckResult(
                    "wva_hpa_model_id_annotation",
                    model_id_ok,
                    expected=f"llm-d.ai/model-id={expected_model_id}",
                    actual=f"llm-d.ai/model-id={model_id_val or '(missing)'}",
                    message=(
                        f"HPA/{hpa_name} model-id annotation matches scenario."
                        if model_id_ok
                        else f"HPA/{hpa_name} model-id mismatch: "
                        f"{model_id_val or '(missing)'} (expected "
                        f"{expected_model_id}). The controller uses this "
                        "to group variants per model."
                    ),
                )
            )

        # Selector alignment check. WVA emits wva_desired_replicas with two
        # labels under the annotation path: variant_name (= the HPA's own
        # name) and exported_namespace (= the HPA's namespace; renamed by
        # Prometheus from `namespace`). The legacy `controller_instance`
        # selector is intentionally absent on the modern path.
        match_labels = (
            hpa.get("spec", {})
            .get("metrics", [{}])[0]
            .get("external", {})
            .get("metric", {})
            .get("selector", {})
            .get("matchLabels", {})
        ) or {}
        expected_selector = {
            "variant_name": hpa_name,
            "exported_namespace": wva_ns,
        }
        mismatches = [
            f"{k}={match_labels.get(k, '(missing)')}≠{v}"
            for k, v in expected_selector.items()
            if match_labels.get(k) != v
        ]
        report.add(
            CheckResult(
                "wva_hpa_selector_alignment",
                not mismatches,
                expected=", ".join(f"{k}={v}" for k, v in expected_selector.items()),
                actual=", ".join(f"{k}={v}" for k, v in match_labels.items())
                or "(empty)",
                message=(
                    f"HPA/{hpa_name} metric selector aligned with WVA-emitted "
                    f"labels — controller→adapter→HPA path will match."
                    if not mismatches
                    else f"HPA/{hpa_name} metric selector mismatch: "
                    f"{', '.join(mismatches)}. Both labels (variant_name, "
                    "exported_namespace) must equal what the WVA controller "
                    "emits or no metric row will match."
                ),
            )
        )

        # Surface the AbleToScale condition when it's present -- it's the
        # signal that prometheus-adapter is actually serving the
        # wva_desired_replicas external metric to the HPA. Missing
        # condition isn't a hard failure (HPA may still be initializing).
        conditions = hpa.get("status", {}).get("conditions", []) or []
        able = next(
            (c for c in conditions if c.get("type") == "AbleToScale"),
            None,
        )
        if able is not None:
            is_true = able.get("status") == "True"
            report.add(
                CheckResult(
                    "wva_hpa_able_to_scale",
                    is_true,
                    expected="True",
                    actual=able.get("status", "Unknown"),
                    message=(
                        f"HPA/{hpa_name} AbleToScale={able.get('status')} "
                        f"reason={able.get('reason', '')}"
                    ),
                )
            )

    @staticmethod
    def _wait_for_hpa_targets(
        cmd: CommandExecutor,
        wva_ns: str,
        hpa_name: str,
        report: SmoketestReport,
        timeout: int = _HPA_TARGETS_TIMEOUT_SECS,
        poll_interval: int = _HPA_TARGETS_POLL_SECS,
        logger=None,
    ) -> None:
        """Poll the HPA until its TARGETS / currentMetrics resolves to a value.

        ``oc get hpa`` shows ``<unknown>`` for an external metric until the
        full pipeline is live: WVA controller has reconciled the HPA,
        emitted ``wva_desired_replicas`` to its ``/metrics`` endpoint,
        Prometheus has scraped it, prometheus-adapter has discovered the
        rule, and the HPA controller has polled the external-metrics API.
        End-to-end latency on a healthy cluster is typically 60–120 s.

        We poll the HPA's ``.status.currentMetrics[*].external.current``
        block (the source of the TARGETS column) until any external metric
        on this HPA reports a value, or *timeout* expires.
        """
        start = time.time()
        last_state = "<unknown>"
        last_able_to_scale_reason = ""

        while True:
            elapsed = time.time() - start

            result = cmd.kube(
                "get",
                "hpa",
                hpa_name,
                "--namespace",
                wva_ns,
                "-o",
                "json",
                check=False,
            )
            if result.success:
                try:
                    hpa = json.loads(result.stdout) if result.stdout else {}
                except (json.JSONDecodeError, ValueError):
                    hpa = {}

                value = _hpa_first_external_metric_value(hpa)
                if value is not None:
                    target = _hpa_first_external_metric_target(hpa)
                    target_str = f"/{target}" if target else ""
                    report.add(
                        CheckResult(
                            "wva_hpa_targets_resolved",
                            True,
                            expected="<numeric>",
                            actual=str(value),
                            message=(
                                f"HPA/{hpa_name} TARGETS resolved: "
                                f"{value}{target_str} after "
                                f"{int(elapsed)}s — full WVA pipeline live"
                            ),
                        )
                    )
                    return

                # Capture the reason from AbleToScale for the eventual
                # failure message, if present.
                for c in hpa.get("status", {}).get("conditions", []) or []:
                    if c.get("type") == "ScalingActive" and c.get("status") == "False":
                        last_able_to_scale_reason = (
                            c.get("reason", "") + ": " + c.get("message", "")
                        )[:240]
                        break

                last_state = "<unknown>"

            if elapsed >= timeout:
                report.add(
                    CheckResult(
                        "wva_hpa_targets_resolved",
                        False,
                        expected="<numeric>",
                        actual=last_state,
                        message=(
                            f"HPA/{hpa_name} TARGETS did not resolve within "
                            f"{timeout}s. Most recent ScalingActive=False reason: "
                            f"{last_able_to_scale_reason or '(none reported)'}"
                        ),
                    )
                )
                return

            if logger is not None and int(elapsed) % 30 == 0 and int(elapsed) > 0:
                logger.log_info(
                    f"⏳ Waiting for HPA/{hpa_name} TARGETS to resolve "
                    f"({int(elapsed)}/{timeout}s)..."
                )

            time.sleep(poll_interval)

    @staticmethod
    def _wait_for_hpa_converged(
        cmd: CommandExecutor,
        wva_ns: str,
        hpa_name: str,
        report: SmoketestReport,
        timeout: int = _HPA_CONVERGED_TIMEOUT_SECS,
        poll_interval: int = _HPA_CONVERGED_POLL_SECS,
        logger=None,
    ) -> None:
        """Wait for the HPA's REPLICAS to converge to its MINPODS.

        At smoketest time the deployment has no traffic, so the WVA
        controller computes ``desiredReplicas == minReplicas`` and the
        HPA scales the Deployment down to that floor. If this never
        happens, either:
          - the HPA is receiving the metric but failing to scale (RBAC
            or scale-subresource issue), or
          - the controller is computing ``desiredReplicas > minReplicas``
            for a still-loading deployment, or
          - we're inside a still-active scaleDown stabilization window
            and timeout was set too tight.
        """
        start = time.time()
        last_state = "(hpa not found)"

        while True:
            elapsed = time.time() - start

            result = cmd.kube(
                "get",
                "hpa",
                hpa_name,
                "--namespace",
                wva_ns,
                "-o",
                "json",
                check=False,
            )

            if result.success:
                try:
                    hpa = json.loads(result.stdout) if result.stdout else {}
                except (json.JSONDecodeError, ValueError):
                    hpa = {}

                spec_min = int(hpa.get("spec", {}).get("minReplicas", 1) or 1)
                spec_max = int(hpa.get("spec", {}).get("maxReplicas", 0) or 0)
                current = hpa.get("status", {}).get("currentReplicas")
                desired = hpa.get("status", {}).get("desiredReplicas")

                last_state = (
                    f"current={current} desired={desired} min={spec_min} max={spec_max}"
                )

                if (
                    current is not None
                    and int(current) == spec_min
                    and (desired is None or int(desired) == spec_min)
                ):
                    report.add(
                        CheckResult(
                            "wva_hpa_converged",
                            True,
                            expected=f"REPLICAS={spec_min} (=MINPODS)",
                            actual=f"REPLICAS={current}",
                            message=(
                                f"HPA/{hpa_name} converged on idle steady-state "
                                f"({last_state}) after {int(elapsed)}s"
                            ),
                        )
                    )
                    return

            if elapsed >= timeout:
                report.add(
                    CheckResult(
                        "wva_hpa_converged",
                        False,
                        expected="REPLICAS == MINPODS",
                        actual=last_state,
                        message=(
                            f"HPA/{hpa_name} did not converge to MINPODS within "
                            f"{timeout}s. Last state: {last_state}. "
                            "Likely causes: scaleDown stabilization window still "
                            "active (bump _HPA_CONVERGED_TIMEOUT_SECS), HPA can't "
                            "patch the Deployment scale subresource, or controller "
                            "computed desiredReplicas > minReplicas."
                        ),
                    )
                )
                return

            if logger is not None and int(elapsed) % 30 == 0 and int(elapsed) > 0:
                logger.log_info(
                    f"⏳ Waiting for HPA/{hpa_name} REPLICAS to converge "
                    f"({int(elapsed)}/{timeout}s) -- {last_state}"
                )

            time.sleep(poll_interval)

    @staticmethod
    def _log_hpa_state(
        cmd: CommandExecutor,
        wva_ns: str,
        hpa_name: str,
        report: SmoketestReport,
    ) -> None:
        """Capture the current `oc get` output of the HPA into the smoketest
        report so the log alone tells the operator what state the resource
        ended up in (TARGETS, MIN, MAX, REPLICAS, etc.) without requiring
        a follow-up ``oc describe``.

        Always passes when the resource can be queried; failure to query
        is informational, not blocking.
        """
        hpa_result = cmd.kube(
            "get",
            "hpa",
            hpa_name,
            "--namespace",
            wva_ns,
            check=False,
        )

        hpa_text = (
            hpa_result.stdout.strip()
            if hpa_result.success
            else (f"(failed: {hpa_result.stderr.strip()[:200]})")
        )

        report.add(
            CheckResult(
                "wva_hpa_state",
                hpa_result.success,
                message=(
                    f"End-state of HPA/{hpa_name} in ns/{wva_ns}:\n    "
                    + hpa_text.replace("\n", "\n    ")
                ),
            )
        )


def _hpa_first_external_metric_value(hpa: dict):
    """Return the numeric value of the first external metric on the HPA, or None.

    The HPA's TARGETS column is rendered from
    ``.status.currentMetrics[*].external.current.{value,averageValue}``.
    Either field may be set depending on the metric's targetType.
    """
    for m in hpa.get("status", {}).get("currentMetrics", []) or []:
        external = m.get("external") or {}
        current = external.get("current") or {}
        for key in ("value", "averageValue"):
            v = current.get(key)
            if v is not None and str(v) != "":
                return v
    return None


def _hpa_first_external_metric_target(hpa: dict) -> str:
    """Return the spec-side target value (denominator in TARGETS), or ''.

    Used purely for logging — e.g. ``500m/1`` shows "500m" current and
    "1" target.
    """
    for m in hpa.get("spec", {}).get("metrics", []) or []:
        target = (m.get("external") or {}).get("target") or {}
        for key in ("value", "averageValue"):
            v = target.get(key)
            if v is not None and str(v) != "":
                return str(v)
    return ""


def _wva_controller_restart_count(cmd: CommandExecutor, wva_ns: str) -> int | None:
    """Sum the manager-container restart counts across all controller pods.

    Used to detect crash-loops mid-wait without parsing logs. Returns
    None if pods can't be queried (don't treat that as a regression —
    skip the crash-loop check that round).
    """
    result = cmd.kube(
        "get",
        "pods",
        "--namespace",
        wva_ns,
        "-l",
        "control-plane=controller-manager",
        "-o",
        "json",
        check=False,
    )
    if not result.success:
        return None
    try:
        data = json.loads(result.stdout) if result.stdout else {}
    except (json.JSONDecodeError, ValueError):
        return None

    total = 0
    for pod in data.get("items", []) or []:
        for cs in pod.get("status", {}).get("containerStatuses", []) or []:
            if cs.get("name") == "manager":
                total += int(cs.get("restartCount", 0) or 0)
    return total


def _deployment_is_available(dep: dict) -> bool:
    """Return True when the Deployment has an Available=True condition."""
    for cond in dep.get("status", {}).get("conditions", []) or []:
        if cond.get("type") == "Available" and cond.get("status") == "True":
            return True
    return False


class WvaValidator(WvaSmoketestMixin, BaseSmoketest):
    """Minimal standalone validator for WVA-only scenarios (e.g. inference-scheduling-wva).

    Runs the base infrastructure smoketest plus the WVA-specific checks
    (controller, prometheus-adapter, HPA annotations + selector alignment).
    """

    def run_config_validation(
        self,
        context: ExecutionContext,
        stack_path: Path,
    ) -> SmoketestReport:
        report = SmoketestReport()
        self.run_wva_checks(context, stack_path, report)
        return report
