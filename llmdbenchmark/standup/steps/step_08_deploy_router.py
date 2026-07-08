"""Step 08 -- Deploy the llm-d router (EPP + provider-specific resources)."""

import json
from pathlib import Path

import yaml

from llmdbenchmark.executor.step import Step, StepResult, Phase
from llmdbenchmark.executor.context import ExecutionContext


class DeployRouterStep(Step):
    """Deploy the llm-d router (EPP, InferencePool, provider resources)."""

    def __init__(self):
        super().__init__(
            number=8,
            name="deploy_router",
            description="Deploy llm-d router (EPP + provider resources)",
            phase=Phase.STANDUP,
            per_stack=True,
        )

    def should_skip(self, context: ExecutionContext) -> bool:
        return "modelservice" not in context.deployed_methods

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

        errors = []
        cmd = context.require_cmd()

        router_values = self._find_yaml(stack_path, "12_router-values")

        if not router_values:
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=True,
                message="No router values found, skipping",
                stack_name=stack_path.name,
            )

        plan_config = self._load_stack_config(stack_path)
        release = self._require_config(plan_config, "release")
        namespace = context.require_namespace()
        stack_name = stack_path.name

        if context.non_admin:
            self._patch_router_for_non_admin(context, stack_name)

        helm_dir = context.setup_helm_dir() / stack_name
        helmfile_work = helm_dir / "helmfile.yaml"

        # A router release left in a transitional Helm state by a previous,
        # interrupted teardown (most commonly `uninstalling`) makes the
        # `helmfile apply` below a silent no-op: helm sees a release already
        # present and skips the install, so the EPP + InferencePool never come
        # up and step 09 hangs forever on "inference pool: no pods found yet".
        # Clear such a release before applying so the apply performs a real
        # install instead of no-op'ing.
        model_id_label = plan_config.get("model_id_label", "")
        if model_id_label and not context.dry_run:
            self._clear_wedged_release(
                cmd, context, namespace, f"{model_id_label}-router"
            )

        if helmfile_work.exists():
            result = cmd.helmfile(
                "--namespace",
                namespace,
                "--selector",
                f"name={model_id_label}-router",
                "apply",
                "-f",
                str(helmfile_work),
                "--skip-diff-on-install",
                "--skip-schema-validation",
            )
            if not result.success:
                errors.append(f"Failed to deploy router: {result.stderr}")
        else:
            main_helmfile = self._find_yaml(stack_path, "10_helmfile-main")
            if main_helmfile:
                result = cmd.helmfile(
                    "--namespace",
                    namespace,
                    "--selector",
                    f"name={model_id_label}-router",
                    "apply",
                    "-f",
                    str(main_helmfile),
                    "--skip-diff-on-install",
                    "--skip-schema-validation",
                )
                if not result.success:
                    errors.append(f"Failed to deploy router: {result.stderr}")

        # Wait for gateway pod only (not EPP -- it stays NOT_SERVING until step 09)
        if not errors and not context.dry_run:
            gateway_class = self._require_config(plan_config, "gateway", "className")
            if gateway_class == "epponly":
                # No Gateway resource is deployed in epponly mode; the EPP
                # pod itself is the data-plane proxy and is waited on by
                # step_09 once the model servers come up.
                context.logger.log_info(
                    "gateway.className=epponly -- no Gateway pod to wait "
                    "for; EPP readiness is verified in step 09 after the "
                    "model servers are deployed"
                )
            else:
                if gateway_class == "data-science-gateway-class":
                    gw_label = "gateway.istio.io/managed=istio.io-gateway-controller"
                elif gateway_class == "agentgateway":
                    # agentgateway controller creates pods with the gateway name
                    # as the app.kubernetes.io/name label, not "llm-d-infra".
                    gw_label = (
                        f"app.kubernetes.io/name=infra-{release}-inference-gateway"
                    )
                else:
                    gw_label = "app.kubernetes.io/name=llm-d-infra"

                timeout = context.gateway_deploy_timeout
                gateway_wait = cmd.wait_for_pods(
                    label=gw_label,
                    namespace=namespace,
                    timeout=timeout,
                    poll_interval=10,
                    description="gateway infra",
                )
                if not gateway_wait.success:
                    errors.append(f"Gateway infra pod not ready: {gateway_wait.stderr}")
                else:
                    context.logger.log_info(
                        "Router deployed -- EPP pod will become Ready after "
                        "model servers are deployed in step 09"
                    )

        if errors:
            for err in errors:
                context.logger.log_error(f"    {err}")
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=False,
                message="Router deployment had errors",
                errors=errors,
                stack_name=stack_path.name,
            )

        return StepResult(
            step_number=self.number,
            step_name=self.name,
            success=True,
            message=f"Router deployed for {stack_path.name}",
            stack_name=stack_path.name,
        )

    # Helm statuses from which `helmfile apply` will NOT perform a real
    # install/upgrade -- a release in any of these must be cleared first or
    # the apply silently no-ops. `uninstalling` is the one we hit in practice
    # (interrupted teardown); the pending-* states are included because they
    # are equally un-recoverable by a plain re-apply.
    _WEDGED_HELM_STATES = frozenset(
        {"uninstalling", "pending-install", "pending-upgrade", "pending-rollback"}
    )

    def _clear_wedged_release(
        self,
        cmd,
        context: ExecutionContext,
        namespace: str,
        release_name: str,
    ) -> None:
        """Remove a router Helm release stuck in a transitional state.

        A release left `uninstalling` (or `pending-*`) by an interrupted
        teardown is invisible to `helm list` but still blocks `helmfile
        apply` from installing -- the apply sees the release and no-ops,
        the EPP/InferencePool never deploy, and step 09 hangs on
        "inference pool: no pods found yet". `helm uninstall` does not
        reliably clear an already-`uninstalling` release, so delete the
        backing release secret(s) directly. Best-effort: any failure here
        is logged and left to the apply below to surface.
        """
        status = cmd.helm(
            "status",
            release_name,
            "--namespace",
            namespace,
            "-o",
            "json",
            check=False,
        )
        if not status.success:
            # No such release (the common, healthy case) -- nothing to clear.
            return

        state = ""
        try:
            state = (json.loads(status.stdout).get("info", {}) or {}).get("status", "")
        except (ValueError, AttributeError):
            state = ""

        if state not in self._WEDGED_HELM_STATES:
            return

        context.logger.log_warning(
            f'Helm release "{release_name}" is stuck in state "{state}" '
            "(likely an interrupted teardown); clearing its release "
            "secret(s) so the router install can proceed."
        )
        cmd.kube(
            "delete",
            "secret",
            "-l",
            f"owner=helm,name={release_name}",
            "--ignore-not-found=true",
            namespace=namespace,
            check=False,
        )

    def _patch_router_for_non_admin(self, context: ExecutionContext, stack_name: str):
        """Disable cluster-admin features (Prometheus monitoring) in router values."""
        helm_dir = context.setup_helm_dir() / stack_name
        router_file = helm_dir / "router-values.yaml"
        if not router_file.exists():
            return

        try:
            content = yaml.safe_load(router_file.read_text(encoding="utf-8"))
            if not content:
                return

            # The rendered values use the llm-d-router chart's `router.*`
            # layout, so monitoring lives at
            # `router.monitoring.prometheus.enabled`.
            router = content.get("router", {})
            monitoring = router.get("monitoring", {})
            prometheus = monitoring.get("prometheus", {})
            if prometheus:
                prometheus["enabled"] = False
                context.logger.log_info(
                    "Non-admin: disabled router Prometheus monitoring"
                )

            with open(router_file, "w", encoding="utf-8") as f:
                yaml.dump(content, f, default_flow_style=False)

        except (OSError, yaml.YAMLError):
            pass
