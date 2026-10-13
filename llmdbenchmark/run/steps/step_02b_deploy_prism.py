"""Step 02b -- Deploy the persistent in-cluster llm-d-prism dashboard.

Runs right after step 02 has prepared the harness namespace, so the workload
PVC that prism plots from already exists. The dashboard outlives the run and
normal teardown; re-applying it on every run is idempotent.
"""

from __future__ import annotations

from pathlib import Path

from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.executor.step import Phase, Step, StepResult


class DeployPrismStep(Step):
    """Deploy the persistent llm-d-prism dashboard into the model namespace."""

    def __init__(self):
        super().__init__(
            number=2,
            name="deploy_prism",
            description="Deploy persistent llm-d-prism dashboard",
            phase=Phase.RUN,
            per_stack=False,
        )

    def should_skip(self, context: ExecutionContext) -> bool:
        # Same gating as step 02: no cluster for nok8s, and collect-only mode
        # deploys nothing new.
        if "nok8s" in (context.deployed_methods or []):
            return True
        if context.harness_skip_run:
            return True
        plan_config = self._load_plan_config(context)
        prism = (plan_config or {}).get("prism", {}) or {}
        return not prism.get("enabled", True)

    def execute(
        self, context: ExecutionContext, stack_path: Path | None = None
    ) -> StepResult:
        cmd = context.require_cmd()
        plan_config = self._load_plan_config(context) or {}
        prism = plan_config.get("prism", {}) or {}

        ns = plan_config.get("namespace", {}).get("name") or context.require_namespace()

        if context.dry_run:
            context.logger.log_info(f"[DRY RUN] Would deploy llm-d-prism to ns/{ns}")
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=True,
                message=f"[DRY RUN] prism -> ns/{ns}",
            )

        prism_yaml = self._find_rendered_yaml(context, "35_prism")
        if not prism_yaml or not self._has_yaml_content(prism_yaml):
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=True,
                message="prism template not rendered (disabled) -- skipping",
            )

        # Step 02 creates workload-pvc in the harness namespace; make sure it
        # also exists here when the model namespace differs (idempotent).
        if not self._ensure_workload_pvc(cmd, context, ns):
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=True,
                message="prism skipped: workload PVC unavailable (non-fatal)",
            )

        result = cmd.kube("apply", "-f", str(prism_yaml))
        if not result.success:
            context.logger.log_warning(
                f"Prism deploy failed (non-fatal): {result.stderr}"
            )
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=True,
                message="prism deploy skipped due to apply error (non-fatal)",
            )

        # OpenShift: expose via a route (skipped if it already exists).
        route_enabled = (prism.get("route", {}) or {}).get("enabled", True)
        if context.is_openshift and route_enabled:
            route_name = prism.get("name", "llm-d-prism")
            svc_name = prism.get("serviceName", "llm-d-prism")
            check = cmd.kube(
                "get",
                "route",
                route_name,
                "-n",
                ns,
                "--ignore-not-found",
                check=False,
            )
            if not (check.success and check.stdout.strip()):
                # --port names the service port; a numeric value 503s the route
                expose = cmd.kube(
                    "expose",
                    f"service/{svc_name}",
                    f"--name={route_name}",
                    "--port=http",
                    "-n",
                    ns,
                    check=False,
                )
                if not expose.success:
                    context.logger.log_warning(
                        f"Could not create prism route (non-fatal): {expose.stderr}"
                    )

        # Best-effort readiness wait -- a stuck pull must not fail the run.
        wait_result = cmd.wait_for_pods(
            label="app.kubernetes.io/name=llm-d-prism",
            namespace=ns,
            timeout=120,
            poll_interval=5,
            description="llm-d-prism dashboard",
        )
        if not wait_result.success:
            context.logger.log_warning(
                "llm-d-prism pod not ready within timeout (non-fatal): "
                f"{wait_result.stderr}"
            )

        return StepResult(
            step_number=self.number,
            step_name=self.name,
            success=True,
            message=f"llm-d-prism dashboard deployed (ns={ns})",
        )

    def _ensure_workload_pvc(
        self, cmd, context: ExecutionContext, namespace: str
    ) -> bool:
        """False when prism cannot mount it."""
        if context.no_pvc:
            context.logger.log_info(
                "\u2139\ufe0f  --no-pvc: skipping prism (nothing to plot from)"
            )
            return False

        pvc_yaml = self._find_rendered_yaml(context, "01_pvc_workload-pvc")
        if not pvc_yaml:
            context.logger.log_warning(
                "workload PVC not rendered -- skipping prism (non-fatal)"
            )
            return False

        result = cmd.kube("apply", "-f", str(pvc_yaml), "--namespace", namespace)
        if not result.success and "AlreadyExists" not in result.stderr:
            context.logger.log_warning(
                f"Could not ensure workload PVC (non-fatal): {result.stderr}"
            )
            return False
        return True
