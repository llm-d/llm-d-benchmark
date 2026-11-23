"""Step 07 -- Deploy the llm-d router (EPP + provider-specific resources)."""

from pathlib import Path

import yaml

from llmdbenchmark.executor.step import Step, StepResult, Phase
from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.utilities.kube_helpers import (
    clear_wedged_release,
    restore_release_record,
    wait_for_epp,
)


class DeployRouterStep(Step):
    """Deploy the llm-d router (EPP, InferencePool, provider resources)."""

    def __init__(self):
        super().__init__(
            number=7,
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

        plan_config = self._load_stack_config(stack_path)
        gateway_class = self._require_config(plan_config, "gateway", "className")
        if gateway_class == "none":
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=True,
                message="Direct Service mode does not deploy a router",
                stack_name=stack_path.name,
            )

        router_values = self._find_yaml(stack_path, "12_router-values")

        if not router_values:
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=True,
                message="No router values found, skipping",
                stack_name=stack_path.name,
            )

        release = self._require_config(plan_config, "release")
        namespace = context.require_namespace()
        stack_name = stack_path.name

        if context.non_admin:
            self._patch_router_for_non_admin(context, stack_name)

        helm_dir = context.setup_helm_dir() / stack_name
        helmfile_work = helm_dir / "helmfile.yaml"

        model_id_label = plan_config.get("model_id_label", "")
        # the helmfile declares every release in the gateway namespace
        release_ns = (plan_config.get("gateway", {}) or {}).get(
            "namespace"
        ) or namespace
        cleared = False
        record = None
        if model_id_label and not context.dry_run:
            cleared, record = clear_wedged_release(
                cmd, release_ns, f"{model_id_label}-router", context.logger
            )
        # the surviving record can claim the desired state is already
        # live while the objects are not
        diff_env = {"HELM_DIFF_THREE_WAY_MERGE": "true"} if cleared else None

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
                env=diff_env,
            )
            if not result.success:
                errors.append(f"Failed to deploy router: {result.stderr}")
                if record:
                    restore_release_record(
                        cmd,
                        release_ns,
                        f"{model_id_label}-router",
                        record,
                        context.logger,
                    )
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
                    env=diff_env,
                )
                if not result.success:
                    errors.append(f"Failed to deploy router: {result.stderr}")
                    if record:
                        restore_release_record(
                            cmd,
                            release_ns,
                            f"{model_id_label}-router",
                            record,
                            context.logger,
                        )

        # Wait for gateway pod only (not EPP -- it stays NOT_SERVING until step 09)
        if not errors and not context.dry_run:
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

        # A standup waits for the EPP in step 08, once the model servers are
        # up. On an update they are up already, and step 08 may not run.
        if not errors and context.current_phase is Phase.UPDATE:
            epp_wait = wait_for_epp(
                cmd, namespace, model_id_label, context.modelservice_deploy_timeout
            )
            if epp_wait is not None and not epp_wait.success:
                errors.append(f"EPP pod not ready: {epp_wait.stderr}")

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

        except OSError, yaml.YAMLError:
            pass
