"""WVA smoketest checks: controller + prometheus-adapter + per-stack VA & HPA.

This is a *mixin* rather than a top-level validator so scenario-specific
validators (e.g. inference-scheduling) can layer it on without having to
duplicate its logic. A concrete validator exists too
(:class:`WvaValidator`) for scenarios whose only WVA concerns are the
baseline controller + per-stack VA/HPA checks.

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
          3. The per-stack VariantAutoscaling resource exists with the
             expected name ({model_id_label}-decode).
          4. The VariantAutoscaling carries the `wva.llmd.ai/controller-instance`
             label matching the controller's `CONTROLLER_INSTANCE` env (when
             set). Without this label the controller's predicate silently
             skips the VA — the most subtle WVA misconfiguration.
          5. The per-stack HPA exists and references the decode Deployment.
          6. The HPA's external-metric selector matches what WVA actually
             emits: `variant_name`, `exported_namespace`, and
             `controller_instance` all aligned with the rendered VA + chart
             values. Catches selector drift before it manifests as
             `TARGETS: <unknown>`.
          7. (optional) HPA has an ``AbleToScale`` condition, meaning
             prometheus-adapter is serving ``wva_desired_replicas`` for it.
          8. The HPA's TARGETS / currentMetrics field has resolved from
             <unknown> to a numeric value — proves the full pipeline
             (controller → Prometheus → adapter → HPA) is end-to-end live.
          9. The HPA's REPLICAS column converges to its MINPODS (idle
             steady-state). With no traffic hitting the deployment, the
             controller computes desiredReplicas=minReplicas and the HPA
             scales the Deployment down to match — confirms the HPA is
             not just receiving the metric but actually acting on it.
         10. End-state snapshot of the VA + HPA (always passes if the
             resources can be queried) so the smoketest log captures
             the final cluster state without needing a follow-up
             ``oc describe`` call to debug.
        """
        config = _load_config(stack_path)
        if not (_nested_get(config, "wva", "enabled") or False):
            return

        # Standup also gates WVA install on OpenShift (see step_03 +
        # step_09); the smoketest must mirror that gate or every check
        # below would fail with "not found" against a cluster that
        # never had WVA installed in the first place.
        if not context.is_openshift:
            report.add(CheckResult(
                "wva_platform_gate",
                True,
                message=(
                    f"WVA enabled in scenario but platform is "
                    f"{context.platform_type}, not OpenShift -- skipping "
                    "WVA smoketest checks (matches standup behavior)."
                ),
            ))
            return

        cmd = context.require_cmd()
        namespace = context.require_namespace()

        if context.dry_run:
            report.add(CheckResult(
                "wva_dry_run", True,
                message="[DRY RUN] WVA smoketest skipped",
            ))
            return

        wva_ns = _nested_get(config, "wva", "namespace") or namespace
        monitoring_ns = (
            _nested_get(config, "openshiftMonitoring", "userWorkloadMonitoringNamespace")
            or "openshift-user-workload-monitoring"
        )
        model_id_label = (
            config.get("model_id_label", "")
            or _nested_get(config, "model", "shortName")
            or ""
        )
        va_name = f"{model_id_label}-decode"
        decode_deployment = f"{model_id_label}-decode"

        # Expected `controller_instance` value -- derived the same way the
        # 19_/27_/28_ templates do, so an empty `wva.namespace` falls back
        # to the deploy namespace.
        expected_controller_instance = (
            _nested_get(config, "wva", "namespace") or namespace
        )

        self._check_wva_controller(
            cmd, wva_ns, report,
            timeout=_WVA_CONTROLLER_TIMEOUT_SECS,
            poll_interval=_WVA_CONTROLLER_POLL_SECS,
            logger=context.logger,
        )
        self._check_prometheus_adapter(cmd, monitoring_ns, report)
        self._check_variant_autoscaling(
            cmd, wva_ns, va_name, expected_controller_instance, report,
        )
        self._check_hpa(
            cmd, wva_ns, va_name, decode_deployment,
            expected_controller_instance, wva_ns, report,
        )
        self._wait_for_hpa_targets(
            cmd, wva_ns, va_name, report,
            timeout=_HPA_TARGETS_TIMEOUT_SECS,
            poll_interval=_HPA_TARGETS_POLL_SECS,
            logger=context.logger,
        )
        self._wait_for_hpa_converged(
            cmd, wva_ns, va_name, report,
            timeout=_HPA_CONVERGED_TIMEOUT_SECS,
            poll_interval=_HPA_CONVERGED_POLL_SECS,
            logger=context.logger,
        )
        self._log_va_hpa_state(cmd, wva_ns, va_name, report)

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

        Polls ``oc get deployment workload-variant-autoscaler-controller-manager``
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
                "get", "deployment", "workload-variant-autoscaler-controller-manager",
                "--namespace", wva_ns,
                "-o", "json",
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
                    report.add(CheckResult(
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
                            f"deploy/workload-variant-autoscaler-controller-manager "
                            f"--previous` for the failure cause."
                        ),
                    ))
                    return

                if available and desired > 0 and ready == desired:
                    report.add(CheckResult(
                        "wva_controller_deployment",
                        True,
                        expected=f"Available, {desired}/{desired} ready",
                        actual=f"Available, {ready}/{desired} ready",
                        message=(
                            f"WVA controller in ns/{wva_ns}: "
                            f"Available, {ready}/{desired} ready "
                            f"after {int(elapsed)}s"
                        ),
                    ))
                    return

                last_state = f"Available={available}, {ready}/{desired} ready"
            else:
                last_state = (
                    f"deployment lookup failed: "
                    f"{result.stderr.strip()[:200]}"
                )

            if elapsed >= timeout:
                report.add(CheckResult(
                    "wva_controller_deployment",
                    False,
                    expected=f"Available, all replicas ready within {timeout}s",
                    actual=last_state,
                    message=(
                        f"WVA controller in ns/{wva_ns} did not become "
                        f"ready within {timeout}s. Last state: {last_state}"
                    ),
                ))
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
            "get", "deployment", "prometheus-adapter",
            "--namespace", monitoring_ns,
            "-o", "json",
            check=False,
        )
        if not result.success:
            report.add(CheckResult(
                "wva_prometheus_adapter", False,
                message=(
                    f"prometheus-adapter Deployment not found in "
                    f"ns/{monitoring_ns}: {result.stderr.strip()[:200]}"
                ),
            ))
            return

        try:
            dep = json.loads(result.stdout) if result.stdout else {}
        except (json.JSONDecodeError, ValueError):
            dep = {}

        available = _deployment_is_available(dep)
        replicas = dep.get("status", {}).get("readyReplicas", 0) or 0
        desired = dep.get("spec", {}).get("replicas", 0) or 0
        report.add(CheckResult(
            "wva_prometheus_adapter",
            available and replicas == desired and desired > 0,
            expected=f"Available, {desired}/{desired} ready",
            actual=f"Available={available}, {replicas}/{desired} ready",
            message=(
                f"prometheus-adapter in ns/{monitoring_ns}: "
                f"Available={available}, ready={replicas}/{desired}"
            ),
        ))

    @staticmethod
    def _check_variant_autoscaling(
        cmd: CommandExecutor,
        wva_ns: str,
        va_name: str,
        expected_controller_instance: str,
        report: SmoketestReport,
    ) -> None:
        """Verify the per-stack VariantAutoscaling exists AND carries the
        ``wva.llmd.ai/controller-instance`` label that matches the
        controller's ``CONTROLLER_INSTANCE`` env.

        The label check is the most subtle of the WVA gates: the v0.6.0
        controller's predicate filters out any in-namespace VA that
        doesn't carry this label when ``CONTROLLER_INSTANCE`` is set.
        Without it, the VA shows up in ``oc get va`` but the controller
        silently never reconciles it ("No active VariantAutoscalings
        found" log loop forever) → no metric → HPA stuck at <unknown>.
        """
        result = cmd.kube(
            "get", "variantautoscaling.llmd.ai", va_name,
            "--namespace", wva_ns,
            "-o", "json",
            check=False,
        )
        if not result.success:
            report.add(CheckResult(
                "wva_variantautoscaling",
                False,
                expected=f"{va_name} present in ns/{wva_ns}",
                actual="missing",
                message=(
                    f"VariantAutoscaling/{va_name} in ns/{wva_ns}: "
                    f"{result.stderr.strip()[:200]}"
                ),
            ))
            return

        try:
            va = json.loads(result.stdout) if result.stdout else {}
        except (json.JSONDecodeError, ValueError):
            va = {}

        report.add(CheckResult(
            "wva_variantautoscaling",
            True,
            expected=f"{va_name} present in ns/{wva_ns}",
            actual="present",
            message=f"VariantAutoscaling/{va_name} in ns/{wva_ns}: present",
        ))

        # Critical alignment check: VA label must match controller's instance.
        labels = va.get("metadata", {}).get("labels", {}) or {}
        actual_label = labels.get("wva.llmd.ai/controller-instance", "")
        label_ok = actual_label == expected_controller_instance
        report.add(CheckResult(
            "wva_va_controller_instance_label",
            label_ok,
            expected=(
                f"wva.llmd.ai/controller-instance="
                f"{expected_controller_instance}"
            ),
            actual=(
                f"wva.llmd.ai/controller-instance={actual_label or '(missing)'}"
            ),
            message=(
                f"VariantAutoscaling/{va_name} controller-instance label: "
                f"{actual_label or '(missing)'} "
                f"(expected {expected_controller_instance}). "
                "Without a matching label, the controller's predicate "
                "skips this VA and never emits wva_desired_replicas."
                if not label_ok
                else
                f"VariantAutoscaling/{va_name} carries the matching "
                f"wva.llmd.ai/controller-instance={expected_controller_instance} "
                "label — controller will reconcile it."
            ),
        ))

    @staticmethod
    def _check_hpa(
        cmd: CommandExecutor,
        wva_ns: str,
        hpa_name: str,
        expected_target: str,
        expected_controller_instance: str,
        expected_exported_namespace: str,
        report: SmoketestReport,
    ) -> None:
        """Verify the per-stack HPA exists, targets the decode Deployment,
        carries a metric selector aligned with what the WVA controller
        actually emits, and (best-effort) has an ``AbleToScale`` condition.
        """
        result = cmd.kube(
            "get", "hpa", hpa_name,
            "--namespace", wva_ns,
            "-o", "json",
            check=False,
        )
        if not result.success:
            report.add(CheckResult(
                "wva_hpa", False,
                message=(
                    f"HPA/{hpa_name} not found in ns/{wva_ns}: "
                    f"{result.stderr.strip()[:200]}"
                ),
            ))
            return

        try:
            hpa = json.loads(result.stdout) if result.stdout else {}
        except (json.JSONDecodeError, ValueError):
            hpa = {}

        scale_target = hpa.get("spec", {}).get("scaleTargetRef", {}).get("name", "")
        report.add(CheckResult(
            "wva_hpa_target",
            scale_target == expected_target,
            expected=expected_target,
            actual=scale_target,
            message=(
                f"HPA/{hpa_name} scaleTargetRef.name={scale_target} "
                f"(expected {expected_target})"
            ),
        ))

        # Selector alignment check. The metric labels the controller
        # actually emits are: variant_name (= VA's name), exported_namespace
        # (= VA's namespace; renamed by Prometheus from `namespace`), and
        # controller_instance (= chart's wva.controllerInstance, when set).
        # The HPA's matchLabels must equal these or the external-metrics
        # API will return zero items and the HPA stays <unknown>.
        match_labels = (
            hpa.get("spec", {}).get("metrics", [{}])[0]
            .get("external", {}).get("metric", {})
            .get("selector", {}).get("matchLabels", {})
        ) or {}
        expected_selector = {
            "variant_name": expected_target,
            "exported_namespace": expected_exported_namespace,
            "controller_instance": expected_controller_instance,
        }
        mismatches = [
            f"{k}={match_labels.get(k, '(missing)')}≠{v}"
            for k, v in expected_selector.items()
            if match_labels.get(k) != v
        ]
        report.add(CheckResult(
            "wva_hpa_selector_alignment",
            not mismatches,
            expected=", ".join(f"{k}={v}" for k, v in expected_selector.items()),
            actual=", ".join(f"{k}={v}" for k, v in match_labels.items()) or "(empty)",
            message=(
                f"HPA/{hpa_name} metric selector aligned with WVA-emitted "
                f"labels — controller→adapter→HPA path will match."
                if not mismatches
                else
                f"HPA/{hpa_name} metric selector mismatch: {', '.join(mismatches)}. "
                "These three labels (variant_name, exported_namespace, "
                "controller_instance) must equal what the WVA controller "
                "emits or no metric row will match."
            ),
        ))

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
            report.add(CheckResult(
                "wva_hpa_able_to_scale",
                is_true,
                expected="True",
                actual=able.get("status", "Unknown"),
                message=(
                    f"HPA/{hpa_name} AbleToScale={able.get('status')} "
                    f"reason={able.get('reason', '')}"
                ),
            ))

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
        full pipeline is live: WVA controller has reconciled the VA,
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
                "get", "hpa", hpa_name,
                "--namespace", wva_ns,
                "-o", "json",
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
                    report.add(CheckResult(
                        "wva_hpa_targets_resolved",
                        True,
                        expected="<numeric>",
                        actual=str(value),
                        message=(
                            f"HPA/{hpa_name} TARGETS resolved: "
                            f"{value}{target_str} after "
                            f"{int(elapsed)}s — full WVA pipeline live"
                        ),
                    ))
                    return

                # Capture the reason from AbleToScale for the eventual
                # failure message, if present.
                for c in (hpa.get("status", {}).get("conditions", []) or []):
                    if c.get("type") == "ScalingActive" and c.get("status") == "False":
                        last_able_to_scale_reason = (
                            c.get("reason", "") + ": " + c.get("message", "")
                        )[:240]
                        break

                last_state = "<unknown>"

            if elapsed >= timeout:
                report.add(CheckResult(
                    "wva_hpa_targets_resolved",
                    False,
                    expected="<numeric>",
                    actual=last_state,
                    message=(
                        f"HPA/{hpa_name} TARGETS did not resolve within "
                        f"{timeout}s. Most recent ScalingActive=False reason: "
                        f"{last_able_to_scale_reason or '(none reported)'}"
                    ),
                ))
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
                "get", "hpa", hpa_name,
                "--namespace", wva_ns,
                "-o", "json",
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
                    f"current={current} desired={desired} "
                    f"min={spec_min} max={spec_max}"
                )

                if (
                    current is not None
                    and int(current) == spec_min
                    and (desired is None or int(desired) == spec_min)
                ):
                    report.add(CheckResult(
                        "wva_hpa_converged",
                        True,
                        expected=f"REPLICAS={spec_min} (=MINPODS)",
                        actual=f"REPLICAS={current}",
                        message=(
                            f"HPA/{hpa_name} converged on idle steady-state "
                            f"({last_state}) after {int(elapsed)}s"
                        ),
                    ))
                    return

            if elapsed >= timeout:
                report.add(CheckResult(
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
                ))
                return

            if logger is not None and int(elapsed) % 30 == 0 and int(elapsed) > 0:
                logger.log_info(
                    f"⏳ Waiting for HPA/{hpa_name} REPLICAS to converge "
                    f"({int(elapsed)}/{timeout}s) -- {last_state}"
                )

            time.sleep(poll_interval)

    @staticmethod
    def _log_va_hpa_state(
        cmd: CommandExecutor,
        wva_ns: str,
        resource_name: str,
        report: SmoketestReport,
    ) -> None:
        """Capture the current `oc get` output of the VA + HPA into the
        smoketest report so the log alone tells the operator what state
        each resource ended up in (TARGETS, MIN, MAX, REPLICAS, etc.)
        without requiring a follow-up ``oc describe``.

        Always passes when both resources can be queried; failure to
        query is informational, not blocking.
        """
        va_result = cmd.kube(
            "get", "variantautoscaling.llmd.ai", resource_name,
            "--namespace", wva_ns,
            check=False,
        )
        hpa_result = cmd.kube(
            "get", "hpa", resource_name,
            "--namespace", wva_ns,
            check=False,
        )

        va_text = va_result.stdout.strip() if va_result.success else (
            f"(failed: {va_result.stderr.strip()[:200]})"
        )
        hpa_text = hpa_result.stdout.strip() if hpa_result.success else (
            f"(failed: {hpa_result.stderr.strip()[:200]})"
        )

        report.add(CheckResult(
            "wva_va_hpa_state",
            va_result.success and hpa_result.success,
            message=(
                f"End-state of WVA resources in ns/{wva_ns}:\n"
                f"  VariantAutoscaling:\n    "
                + va_text.replace("\n", "\n    ")
                + "\n  HorizontalPodAutoscaler:\n    "
                + hpa_text.replace("\n", "\n    ")
            ),
        ))


def _hpa_first_external_metric_value(hpa: dict):
    """Return the numeric value of the first external metric on the HPA, or None.

    The HPA's TARGETS column is rendered from
    ``.status.currentMetrics[*].external.current.{value,averageValue}``.
    Either field may be set depending on the metric's targetType.
    """
    for m in (hpa.get("status", {}).get("currentMetrics", []) or []):
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
    for m in (hpa.get("spec", {}).get("metrics", []) or []):
        target = (m.get("external") or {}).get("target") or {}
        for key in ("value", "averageValue"):
            v = target.get(key)
            if v is not None and str(v) != "":
                return str(v)
    return ""


def _wva_controller_restart_count(
    cmd: CommandExecutor, wva_ns: str
) -> int | None:
    """Sum the manager-container restart counts across all controller pods.

    Used to detect crash-loops mid-wait without parsing logs. Returns
    None if pods can't be queried (don't treat that as a regression —
    skip the crash-loop check that round).
    """
    result = cmd.kube(
        "get", "pods",
        "--namespace", wva_ns,
        "-l", "control-plane=controller-manager",
        "-o", "json",
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
    (controller, prometheus-adapter, VariantAutoscaling, HPA).
    """

    def run_config_validation(
        self, context: ExecutionContext, stack_path: Path,
    ) -> SmoketestReport:
        report = SmoketestReport()
        self.run_wva_checks(context, stack_path, report)
        return report
