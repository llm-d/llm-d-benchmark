"""Step 06 -- Deploy Fast Model Actuation Controllers."""

from pathlib import Path
from datetime import datetime, timezone

from llmdbenchmark import __version__
from llmdbenchmark.executor.step import Step, StepResult, Phase
from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.executor.command import CommandExecutor


class FMADeployStep(Step):
    """Deploy Fast Model Actuation Controllers."""

    def __init__(self):
        super().__init__(
            number=6,
            name="fma_deploy",
            description="Deploy Fast Model Actuation Controllers.",
            phase=Phase.STANDUP,
            per_stack=True,
        )

    def should_skip(self, context: ExecutionContext) -> bool:
        return "fma" not in context.deployed_methods

    def execute(  # pylint: disable=too-many-branches,too-many-locals
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
        namespace = context.require_namespace()

        clusterrole_yaml = self._find_yaml(stack_path, "25_fma-clusterrole")
        if not clusterrole_yaml or not self._has_yaml_content(clusterrole_yaml):
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=False,
                message="No FMA ClusterRole YAML found, skipping",
                errors=["FMA ClusterRole YAML is required"],
                stack_name=stack_path.name,
            )

        fma_helmfile = self._find_yaml(stack_path, "26_helmfile-fma-controllers")
        if not fma_helmfile or not self._has_yaml_content(fma_helmfile):
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=False,
                message="No FMA Controllers helm values found, skipping",
                errors=["FMA Controllers helm values are required"],
                stack_name=stack_path.name,
            )

        deploy_yaml = self._find_yaml(stack_path, "24_fma-deployment")
        if not deploy_yaml or not self._has_yaml_content(deploy_yaml):
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=False,
                message="No FMA deployment YAML found, skipping",
                errors=["FMA deployment YAML is required"],
                stack_name=stack_path.name,
            )

        # Fast Model Actuation CRDS
        self._install_fma_crds(
            context, plan_config, errors
        )  # pylint disable=too-many-function-args

        # Fast Model Actuation ClusterRole
        self._install_fma_clusterole(context, clusterrole_yaml, errors)

        if len(errors) == 0:
            # Fast Model Actuation Controllers chart
            result = cmd.helmfile(
                "apply",
                "-f",
                str(fma_helmfile),
                "--skip-diff-on-install",
            )
            if not result.success:
                errors.append(
                    f"Failed to apply FMA controllets helmfile: {result.stderr}"
                )

        if len(errors) == 0:
            # Wait for fma dual pod to be created, running, and ready
            label_selector = (
                "app.kubernetes.io/name=fma-controllers,"
                "app.kubernetes.io/component=dual-pods-controller"
            )
            wait_result = cmd.wait_for_pods(
                label=label_selector,
                namespace=namespace,
                timeout=900,
                poll_interval=10,
                description="FMA Dual Pod Controller",
            )
            if not wait_result.success:
                errors.append(
                    f"Standalone deployment pods not ready: {wait_result.stderr}"
                )

        if len(errors) == 0:
            # Wait for fma launcher populator pod to be created, running, and ready
            label_selector = (
                "app.kubernetes.io/name=fma-controllers,"
                "app.kubernetes.io/component=launcher-populator"
            )
            wait_result = cmd.wait_for_pods(
                label=label_selector,
                namespace=namespace,
                timeout=900,
                poll_interval=10,
                description="FMA Launcher Populator",
            )
            if not wait_result.success:
                errors.append(
                    f"Standalone deployment pods not ready: {wait_result.stderr}"
                )

        if len(errors) == 0:
            # Apply deployment
            result = cmd.kube("apply", "-f", str(deploy_yaml))
            if not result.success:
                errors.append(f"Failed to apply fma deployment: {result.stderr}")

        if len(errors) == 0:
            resource_types = (
                "InferenceServerConfig,LauncherConfig,"
                "LauncherPopulationPolicy,ReplicaSet,pods"
            )
            cmd.kube(
                "get",
                resource_types,
                "--namespace",
                namespace,
            )

        self._propagate_standup_parameters(cmd, context, plan_config)

        if len(errors) > 0:
            for err in errors:
                context.logger.log_error(f"    {err}")
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=False,
                message="FMA deployment had errors",
                errors=errors,
                stack_name=stack_path.name,
            )

        return StepResult(
            step_number=self.number,
            step_name=self.name,
            success=True,
            message=(f"FMA deployment applied from {stack_path.name}"),
            stack_name=stack_path.name,
        )

    def _install_fma_crds(
        self, context: ExecutionContext, plan_config: dict, errors: list[str]
    ) -> None:
        if context.non_admin:
            errors.append(
                "❗No privileges to setup Fast Model Actuation API crds. "
                "Will assume a user with proper privileges already performed this action."
            )
            return

        crds = plan_config.get("fma", {}).get("crds", {})
        crd_urls = {
            "inferenceserverconfigs.fma.llm-d.ai": crds.get(
                "inferenceServerConfig", ""
            ),
            "launcherconfigs.fma.llm-d.ai": crds.get("launcherConfig", ""),
            "launcherpopulationpolicies.fma.llm-d.ai": crds.get(
                "launcherPopulatorConfig", ""
            ),
        }
        cmd = context.require_cmd()
        result = cmd.kube(
            "get", "crd", "-o", "jsonpath='{.items[*].metadata.name}'", check=False
        )
        if not result.success:
            errors.append(f"Failed to query crds: {result.stderr}")
            return

        crd_names = result.stdout.strip().split()
        for name in crd_names:
            if name in crd_urls:
                del crd_urls[name]
                context.logger.log_info(
                    f"✅ Kubernetes Fast Fast Model Actuation CRD {name} already installed"
                )

        errors = []
        for name, url in crd_urls.items():
            context.logger.log_info(f"🚀 Fast Fast Model Actuation API {name} CRD...")
            result = cmd.kube("apply", "--server-side", "-f", url)
            if not result.success:
                errors.append(f"Failed to apply crd '{name}': {result.stderr}")
                continue
            context.logger.log_info(
                f"✅ Fast Fast Model Actuation API {name} CRD installed"
            )

    def _install_fma_clusterole(
        self, context: ExecutionContext, clusterrole_yaml: Path, errors: list[str]
    ) -> None:
        cmd = context.require_cmd()
        result = cmd.kube(
            "get", "clusterroles", "-o", "jsonpath='{.items[*].metadata.name}'"
        )
        if not result.success:
            errors.append(f"Failed to query clusterroles: {result.stderr}")
            return

        clusterrole_names = result.stdout.strip().split()
        for name in clusterrole_names:
            if name == "fma-node-viewer":
                context.logger.log_info(
                    f"✅ Kubernetes Fast Fast Model Actuation ClusterRole {name} already installed"
                )
                return

        context.logger.log_info("🚚 Deploying Fast Model Actuation ClusterRole ...")

        result = cmd.kube("apply", "-f", str(clusterrole_yaml))
        if not result.success:
            errors.append(f"Failed to apply fma clusterrole: {result.stderr}")
            return

        context.logger.log_info("✅ Fast Model Actuation ClusterRole installed")

    def _propagate_standup_parameters(
        self, cmd: CommandExecutor, context: ExecutionContext, plan_config: dict
    ):
        """Persist deploy metadata as a ConfigMap so run-phase steps can read it."""

        harness_ns = context.harness_namespace or context.require_namespace()
        cm_name = "llm-d-benchmark-standup-parameters"

        params = {
            "tool_name": "llm-d-benchmark",
            "tool_version": __version__,
            "deployed_by": context.username or "unknown",
            "deployed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "cluster_name": context.cluster_name or "",
            "platform_type": context.platform_type,
            "namespace": context.namespace or "",
            "harness_namespace": harness_ns,
            "deploy_methods": ",".join(context.deployed_methods),
        }

        if plan_config:
            params["model_name"] = self._require_config(plan_config, "model", "name")
            params["model_short_name"] = self._require_config(
                plan_config, "model", "shortName"
            )
            params["model_huggingface_id"] = plan_config.get("model", {}).get(
                "huggingfaceId", ""
            )
            params["inference_port"] = str(
                self._require_config(plan_config, "vllmCommon", "inferencePort")
            )
            params["release"] = self._require_config(plan_config, "release")
            params["standalone_replicas"] = str(
                self._require_config(plan_config, "standalone", "replicas")
            )

        literal_args = []
        for key, value in params.items():
            literal_args.append(f"--from-literal={key}={value}")

        create_args = (
            [
                "create",
                "configmap",
                cm_name,
                "--namespace",
                harness_ns,
            ]
            + literal_args
            + ["--dry-run=client", "-o", "yaml"]
        )

        result = cmd.kube(*create_args)
        if result.success:
            yaml_path = context.setup_yamls_dir() / "standup-parameters.yaml"
            yaml_path.write_text(result.stdout, encoding="utf-8")
            apply_result = cmd.kube("apply", "-f", str(yaml_path))
            if apply_result.success:
                context.logger.log_info(
                    f"📋 Deployment metadata to configmap/{cm_name} in ns/{harness_ns}"
                )
                context.logger.log_info(
                    f"   oc get configmap {cm_name} -n {harness_ns} -o yaml"
                )
