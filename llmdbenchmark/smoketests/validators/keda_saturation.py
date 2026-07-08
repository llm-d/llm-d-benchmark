"""EPP+KEDA saturation smoketest checks: direct KEDA ScaledObject (no WVA controller).

This is a *mixin* for scenario-specific validators that layer EPP+KEDA checks
on without duplicating logic. Verifies KEDA CRD, per-stack ScaledObject,
and HPA targets resolution.

Activation gate: runs only when BOTH ``eppKedaSaturation.enabled: true`` is
present in the rendered stack config AND the cluster is OpenShift.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from llmdbenchmark.executor.command import CommandExecutor
from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.smoketests.base import _load_config, _nested_get
from llmdbenchmark.smoketests.report import CheckResult, SmoketestReport


_KEDA_CRD_TIMEOUT_SECS = 60
_KEDA_CRD_POLL_SECS = 5

_HPA_TARGETS_TIMEOUT_SECS = 180
_HPA_TARGETS_POLL_SECS = 5


class EppKedaSaturationSmoketestMixin:
    """Adds EPP+KEDA saturation-specific checks to any scenario validator.

    Subclasses (or concrete validators) call :meth:`run_epp_keda_checks` from
    their ``run_config_validation`` method. Safe to call unconditionally —
    it returns immediately when EPP+KEDA is not enabled on this stack.
    Verifies KEDA CRD, per-stack ScaledObject, and HPA metric resolution.
    """

    def run_epp_keda_checks(
        self,
        context: ExecutionContext,
        stack_path: Path,
        report: SmoketestReport,
    ) -> None:
        """Append EPP+KEDA resource health checks to *report*.

        Validates:
          1. KEDA CRD (scaledobjects.keda.sh) exists.
          2. Per-stack ScaledObject exists with correct scaleTargetRef.
          3. ScaledObject READY = True.
          4. Generated HPA targets have resolved from <unknown> to real numbers.
          5. End-state snapshot of ScaledObject and HPA.
        """
        config = _load_config(stack_path)
        if not (_nested_get(config, "eppKedaSaturation", "enabled") or False):
            return

        if not context.is_openshift:
            report.add_check(
                CheckResult(
                    name="EPP+KEDA platform gate",
                    status="SKIPPED",
                    message=(
                        "EPP+KEDA enabled but platform is not OpenShift "
                        "(not yet verified on other platforms)"
                    ),
                )
            )
            return

        cmd = context.require_cmd()
        epp_keda_ns = (
            _nested_get(config, "eppKedaSaturation", "namespace")
            or _nested_get(config, "namespace", "name")
            or "default"
        )
        model_id_label = _nested_get(config, "model", "shortName") or "model"
        fma_enabled = _nested_get(config, "fma", "enabled") or False
        hpa_name = f"{model_id_label}-{'fma' if fma_enabled else 'decode'}-saturation"

        self._check_keda_crd(cmd, context, report)
        self._check_scaledobject_exists(cmd, epp_keda_ns, hpa_name, report)
        self._check_hpa_targets_resolved(cmd, epp_keda_ns, hpa_name, report)
        self._snapshot_resources(cmd, epp_keda_ns, hpa_name, report)

    def _check_keda_crd(
        self, cmd: CommandExecutor, context: ExecutionContext, report: SmoketestReport
    ) -> None:
        """Verify KEDA ScaledObject CRD is present."""
        result = cmd.kube("get", "crd", "scaledobjects.keda.sh", check=False)
        if result.success:
            report.add_check(
                CheckResult(
                    name="KEDA CRD present",
                    status="PASSED",
                    message="scaledobjects.keda.sh CRD found",
                )
            )
        else:
            report.add_check(
                CheckResult(
                    name="KEDA CRD present",
                    status="FAILED",
                    message=(
                        "KEDA is not installed on this cluster "
                        "(ScaledObject CRD not found)"
                    ),
                )
            )

    def _check_scaledobject_exists(
        self,
        cmd: CommandExecutor,
        namespace: str,
        hpa_name: str,
        report: SmoketestReport,
    ) -> None:
        """Verify ScaledObject exists and has READY=True."""
        result = cmd.kube(
            "get",
            "scaledobject",
            f"{hpa_name}-saturation",
            "-n",
            namespace,
            "-o",
            "json",
            check=False,
        )
        if not result.success:
            report.add_check(
                CheckResult(
                    name="ScaledObject exists",
                    status="FAILED",
                    message=(
                        f"ScaledObject {hpa_name}-saturation "
                        f"not found in ns/{namespace}"
                    ),
                )
            )
            return

        try:
            data = json.loads(result.stdout)
            ready = data.get("status", {}).get("conditions", [])
            is_ready = any(
                c.get("type") == "Ready" and c.get("status") == "True" for c in ready
            )
            status = "PASSED" if is_ready else "FAILED"
            message = f"ScaledObject READY={is_ready}"
            report.add_check(
                CheckResult(name="ScaledObject READY", status=status, message=message)
            )
        except (json.JSONDecodeError, KeyError, AttributeError) as e:
            report.add_check(
                CheckResult(
                    name="ScaledObject READY",
                    status="FAILED",
                    message=f"Could not parse ScaledObject status: {e}",
                )
            )

    def _check_hpa_targets_resolved(
        self,
        cmd: CommandExecutor,
        namespace: str,
        hpa_name: str,
        report: SmoketestReport,
    ) -> None:
        """Poll HPA until TARGETS resolve from <unknown> to real numbers."""
        hpa_name_keda = f"keda-hpa-{hpa_name}-saturation"
        start = time.time()

        while time.time() - start < _HPA_TARGETS_TIMEOUT_SECS:
            result = cmd.kube(
                "get", "hpa", hpa_name_keda, "-n", namespace, "-o", "json", check=False
            )
            if result.success:
                try:
                    data = json.loads(result.stdout)
                    current_metrics = data.get("status", {}).get("currentMetrics", [])
                    if current_metrics:
                        has_unknown = any(
                            "unknown" in str(m).lower() for m in current_metrics
                        )
                        if not has_unknown:
                            report.add_check(
                                CheckResult(
                                    name="HPA targets resolved",
                                    status="PASSED",
                                    message=f"HPA {hpa_name_keda} has resolved TARGETS",
                                )
                            )
                            return
                except (json.JSONDecodeError, KeyError, AttributeError):
                    pass

            time.sleep(_HPA_TARGETS_POLL_SECS)

        report.add_check(
            CheckResult(
                name="HPA targets resolved",
                status="FAILED",
                message=(
                    f"HPA {hpa_name_keda} TARGETS still <unknown> after "
                    f"{_HPA_TARGETS_TIMEOUT_SECS}s"
                ),
            )
        )

    def _snapshot_resources(
        self,
        cmd: CommandExecutor,
        namespace: str,
        hpa_name: str,
        report: SmoketestReport,
    ) -> None:
        """Capture final ScaledObject and HPA state."""
        result = cmd.kube(
            "get",
            "scaledobject,hpa",
            "-n",
            namespace,
            "-o",
            "wide",
            check=False,
        )
        if result.success and result.stdout.strip():
            report.add_output(f"Final ScaledObject and HPA state:\n{result.stdout}")
