"""Step 08 -- Deploy GAIE (Gateway API Inference Extension)."""

import shutil
from pathlib import Path

import yaml

from llmdbenchmark.executor.step import Step, StepResult, Phase
from llmdbenchmark.executor.context import ExecutionContext


class DeployGaieStep(Step):
    """Deploy the GAIE inference extension components."""

    def __init__(self):
        super().__init__(
            number=8,
            name="deploy_gaie",
            description="Deploy GAIE inference extension",
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

        gaie_values = self._find_yaml(stack_path, "12_gaie-values")

        if not gaie_values:
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=True,
                message="No GAIE values found, skipping",
                stack_name=stack_path.name,
            )

        plan_config = self._load_stack_config(stack_path)
        release = self._require_config(plan_config, "release")
        namespace = context.require_namespace()
        stack_name = stack_path.name

        if context.non_admin:
            self._patch_gaie_for_non_admin(context, stack_name)

        helm_dir = context.setup_helm_dir() / stack_name
        helmfile_work = helm_dir / "helmfile.yaml"

        if helmfile_work.exists():
            model_short = self._require_config(plan_config, "model", "shortName")
            result = cmd.helmfile(
                "--namespace", namespace,
                "--selector", f"name={model_short}-gaie",
                "apply", "-f", str(helmfile_work),
                "--skip-diff-on-install", "--skip-schema-validation",
            )
            if not result.success:
                errors.append(f"Failed to deploy GAIE: {result.stderr}")
        else:
            main_helmfile = self._find_yaml(stack_path, "10_helmfile-main")
            if main_helmfile:
                model_short = self._require_config(plan_config, "model", "shortName")
                result = cmd.helmfile(
                    "--namespace", namespace,
                    "--selector", f"name={model_short}-gaie",
                    "apply", "-f", str(main_helmfile),
                    "--skip-diff-on-install", "--skip-schema-validation",
                )
                if not result.success:
                    errors.append(f"Failed to deploy GAIE: {result.stderr}")

        # Wait for gateway pod only (not EPP -- it stays NOT_SERVING until step 09)
        if not errors and not context.dry_run:
            gateway_class = self._require_config(plan_config, "gateway", "className")
            if gateway_class == "data-science-gateway-class":
                gw_label = "gateway.istio.io/managed=istio.io-gateway-controller"
            else:
                gw_label = "app.kubernetes.io/name=llm-d-infra"

            gateway_wait = cmd.wait_for_pods(
                label=gw_label,
                namespace=namespace,
                timeout=120,
                poll_interval=10,
                description="gateway infra",
            )
            if not gateway_wait.success:
                errors.append(
                    f"Gateway infra pod not ready: {gateway_wait.stderr}"
                )
            else:
                context.logger.log_info(
                    "GAIE deployed -- EPP pod will become Ready after "
                    "model servers are deployed in step 09"
                )

        if errors:
            for err in errors:
                context.logger.log_error(f"    {err}")
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=False,
                message="GAIE deployment had errors",
                errors=errors,
                stack_name=stack_path.name,
            )

        return StepResult(
            step_number=self.number,
            step_name=self.name,
            success=True,
            message=f"GAIE deployed for {stack_path.name}",
            stack_name=stack_path.name,
        )

    def _patch_gaie_for_non_admin(
        self, context: ExecutionContext, stack_name: str
    ):
        """Disable cluster-admin features (Prometheus monitoring, InferencePool) in GAIE values."""
        helm_dir = context.setup_helm_dir() / stack_name
        gaie_file = helm_dir / "gaie-values.yaml"
        if not gaie_file.exists():
            return

        try:
            content = yaml.safe_load(gaie_file.read_text(encoding="utf-8"))
            if not content:
                return

            ie = content.get("inferenceExtension", {})
            monitoring = ie.get("monitoring", {})
            prometheus = monitoring.get("prometheus", {})
            if prometheus:
                prometheus["enabled"] = False
                context.logger.log_info(
                    "Non-admin: disabled GAIE Prometheus monitoring"
                )

            with open(gaie_file, "w", encoding="utf-8") as f:
                yaml.dump(content, f, default_flow_style=False)

        except (OSError, yaml.YAMLError):
            pass
