"""Step 07 -- Set up Helm repos and deploy gateway infrastructure for modelservice."""

import shutil
from pathlib import Path

import yaml

from llmdbenchmark.executor.step import Step, StepResult, Phase
from llmdbenchmark.executor.context import ExecutionContext


class DeploySetupStep(Step):
    """Set up Helm repositories and deploy gateway infrastructure."""

    # Map from rendered template prefix to the filename helmfile expects.
    # Helmfile references these by relative path in its values: sections.
    _VALUES_FILE_MAP = {
        "11_infra": "infra.yaml",
        "12_gaie-values": "gaie-values.yaml",
        "13_ms-values": "ms-values.yaml",
    }

    def __init__(self):
        super().__init__(
            number=7,
            name="deploy_setup",
            description="Set up Helm repos and gateway infrastructure",
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
        release = self._require_config(plan_config, "release")
        namespace = context.require_namespace()

        helm_dir = self._prepare_helm_dir(context, stack_path, errors)

        gateway_class = self._require_config(plan_config, "gateway", "className")
        if context.is_openshift and gateway_class == "agentgateway" and helm_dir:
            self._patch_infra_for_openshift_agentgateway(helm_dir, context)

        # Gateway provider helmfile (Istio) -- matches bash behavior:
        # call helmfile WITHOUT --kubeconfig so it uses the default context.
        # This ensures helmfile resolves release namespaces (istio-system)
        # from the helmfile itself, not from the kubeconfig context namespace
        # which may be set to the benchmark namespace (e.g., llmdbenchcicd).
        gw_helmfile = self._find_yaml(stack_path, "09_helmfile-gateway-provider")
        if gw_helmfile and self._has_yaml_content(gw_helmfile):
            result = cmd.helmfile(
                "apply",
                "-f",
                str(gw_helmfile),
                "--skip-diff-on-install",
                "--skip-schema-validation",
                use_kubeconfig=False,
            )
            if not result.success:
                errors.append(
                    f"Failed to install Istio via helmfile: {result.stderr}"
                )

        # Helmfile is copied to helm working dir so relative value paths resolve
        main_helmfile = self._find_yaml(stack_path, "10_helmfile-main")
        if main_helmfile and helm_dir:
            helmfile_work = helm_dir / "helmfile.yaml"
            shutil.copy2(main_helmfile, helmfile_work)

            if context.non_admin:
                self._patch_helmfile_for_non_admin(helmfile_work)

            result = cmd.helmfile(
                "--namespace",
                namespace,
                "--selector",
                f"name=infra-{release}",
                "apply",
                "-f",
                str(helmfile_work),
                "--skip-diff-on-install",
                "--skip-schema-validation",
            )
            if not result.success:
                errors.append(f"Failed to apply infra helmfile: {result.stderr}")

        if errors:
            for err in errors:
                context.logger.log_error(f"    {err}")
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=False,
                message="Deploy setup had errors",
                errors=errors,
                stack_name=stack_path.name,
            )

        return StepResult(
            step_number=self.number,
            step_name=self.name,
            success=True,
            message=(
                "Helm repos and gateway infrastructure deployed "
                f"for {stack_path.name}"
            ),
            stack_name=stack_path.name,
        )

    def _prepare_helm_dir(
        self, context: ExecutionContext, stack_path: Path, errors: list
    ) -> Path | None:
        """Prepare a helm working directory with value files named as helmfile expects."""
        try:
            helm_dir = context.setup_helm_dir() / stack_path.name
            helm_dir.mkdir(parents=True, exist_ok=True)

            for prefix, target_name in self._VALUES_FILE_MAP.items():
                source = self._find_yaml(stack_path, prefix)
                if source:
                    shutil.copy2(source, helm_dir / target_name)

            return helm_dir
        except OSError as exc:
            errors.append(f"Failed to prepare helm directory: {exc}")
            return None

    def _patch_infra_for_openshift_agentgateway(
        self, helm_dir: Path, context: ExecutionContext
    ):
        """Patch infra.yaml to add floatingUserId for agentgateway on OpenShift."""
        infra_file = helm_dir / "infra.yaml"
        if not infra_file.exists():
            return

        try:
            content = yaml.safe_load(infra_file.read_text(encoding="utf-8"))
            if not content or "gateway" not in content:
                return

            gw = content["gateway"]
            if gw.get("gatewayClassName") != "agentgateway":
                return

            gw.setdefault("gatewayParameters", {})
            gw["gatewayParameters"]["floatingUserId"] = True
            gw["gatewayParameters"]["enabled"] = True

            with open(infra_file, "w", encoding="utf-8") as f:
                yaml.dump(content, f, default_flow_style=False)

            context.logger.log_info(
                "Patched infra.yaml: agentgateway to agentgateway-openshift "
                "(floatingUserId=true)"
            )
        except (OSError, yaml.YAMLError):
            pass

    def _patch_helmfile_for_non_admin(self, helmfile_path: Path):
        """Prepend ``helmDefaults: createNamespace: false`` for non-admin users."""
        try:
            content = helmfile_path.read_text(encoding="utf-8")
            if "helmDefaults:" not in content:
                patched = (
                    "helmDefaults:\n" "  createNamespace: false\n" "---\n" + content
                )
                helmfile_path.write_text(patched, encoding="utf-8")
        except OSError:
            pass
