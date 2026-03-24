"""Step 06 -- Deploy vLLM as standalone Kubernetes Deployments and Services."""

from pathlib import Path

import yaml

from llmdbenchmark.executor.step import Step, StepResult, Phase
from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.executor.command import CommandExecutor


class StandaloneDeployStep(Step):
    """Deploy vLLM models as standalone Kubernetes Deployments and Services."""

    def __init__(self):
        super().__init__(
            number=6,
            name="standalone_deploy",
            description="Deploy vLLM standalone models (Deployment + Service)",
            phase=Phase.STANDUP,
            per_stack=True,
        )

    def should_skip(self, context: ExecutionContext) -> bool:
        return "standalone" not in context.deployed_methods

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

        if not context.dry_run:
            pc_error = self._check_priority_class(cmd, plan_config, context)
            if pc_error:
                errors.append(pc_error)
                return StepResult(
                    step_number=self.number,
                    step_name=self.name,
                    success=False,
                    message="PriorityClass validation failed",
                    errors=errors,
                    stack_name=stack_path.name,
                )

        deploy_yaml = self._find_yaml(stack_path, "14_standalone-deployment")
        service_yaml = self._find_yaml(stack_path, "15_standalone-service")

        if not deploy_yaml or not self._has_yaml_content(deploy_yaml):
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=True,
                message="No standalone deployment YAML found, skipping",
                stack_name=stack_path.name,
            )

        result = cmd.kube("apply", "-f", str(deploy_yaml))
        if not result.success:
            errors.append(
                f"Failed to apply standalone deployment: {result.stderr}"
            )

        if service_yaml:
            result = cmd.kube("apply", "-f", str(service_yaml))
            if not result.success:
                errors.append(
                    f"Failed to apply standalone service: {result.stderr}"
                )

        podmonitor_yaml = self._find_yaml(stack_path, "17_standalone-podmonitor")
        if podmonitor_yaml and self._has_yaml_content(podmonitor_yaml):
            result = cmd.kube("apply", "-f", str(podmonitor_yaml))
            if not result.success:
                context.logger.log_warning(
                    f"PodMonitor apply failed (non-fatal): {result.stderr}"
                )
            else:
                context.logger.log_info(
                    "PodMonitor created for Prometheus scraping"
                )
        else:
            context.logger.log_info(
                "PodMonitor skipped (template not rendered for this configuration)"
            )

        httproute_yaml = self._find_yaml(stack_path, "08_httproute")
        if httproute_yaml and self._has_yaml_content(httproute_yaml):
            result = cmd.kube("apply", "-f", str(httproute_yaml))
            if not result.success:
                context.logger.log_warning(
                    f"HTTPRoute apply failed (non-fatal): {result.stderr}"
                )
            else:
                context.logger.log_info("HTTPRoute created")
        else:
            context.logger.log_info(
                "HTTPRoute skipped (template not rendered for this configuration)"
            )

        deploy_name = None
        try:
            with open(deploy_yaml, encoding="utf-8") as f:
                deploy_config = yaml.safe_load(f)
            deploy_name = deploy_config.get("metadata", {}).get(
                "name", ""
            )
        except (yaml.YAMLError, OSError):
            pass

        if deploy_name and not errors:
            replicas = int(
                self._require_config(plan_config, "standalone", "replicas")
            )

            wait_result = cmd.wait_for_pods(
                label=f"app={deploy_name}",
                namespace=namespace,
                timeout=900,
                poll_interval=10,
                description=f"standalone {deploy_name}",
            )
            if not wait_result.success:
                errors.append(
                    "Standalone deployment pods not ready: "
                    f"{wait_result.stderr}"
                )

        if deploy_name and not errors and not context.dry_run:
            self._collect_logs(cmd, context, namespace, deploy_name)

        if service_yaml:
            try:
                with open(service_yaml, encoding="utf-8") as f:
                    svc_config = yaml.safe_load(f)
                svc_name = svc_config.get("metadata", {}).get(
                    "name", ""
                )
                if svc_name:
                    context.deployed_endpoints[stack_path.name] = (
                        svc_name
                    )
            except (yaml.YAMLError, OSError):
                pass

        if context.is_openshift and service_yaml:
            self._create_openshift_route(
                cmd, context, plan_config, service_yaml
            )

        if not errors:
            resource_types = "deployment,service,pods,secrets"
            if context.is_openshift:
                resource_types += ",route"
            cmd.kube(
                "get", resource_types,
                "--namespace", namespace,
            )

        self._propagate_standup_parameters(cmd, context, plan_config)

        if errors:
            for err in errors:
                context.logger.log_error(f"    {err}")
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=False,
                message="Standalone deployment had errors",
                errors=errors,
                stack_name=stack_path.name,
            )

        return StepResult(
            step_number=self.number,
            step_name=self.name,
            success=True,
            message=(
                f"Standalone deployment applied from {stack_path.name}"
            ),
            stack_name=stack_path.name,
        )

    def _collect_logs(
        self, cmd: CommandExecutor, context: ExecutionContext,
        namespace: str, deploy_name: str
    ):
        """Collect vLLM pod logs after deployment is ready."""
        logs_dir = context.setup_logs_dir()
        result = cmd.kube(
            "get", "pods",
            "-l", f"app={deploy_name}",
            "--namespace", namespace,
            "-o", "jsonpath={.items[*].metadata.name}",
        )
        if result.success and result.stdout.strip():
            pod_names = result.stdout.strip().split()
            for pod_name in pod_names:
                log_result = cmd.kube(
                    "logs", pod_name,
                    "--namespace", namespace,
                    "--tail=-1",
                )
                if log_result.success:
                    log_file = logs_dir / f"{pod_name}.log"
                    log_file.write_text(
                        log_result.stdout, encoding="utf-8"
                    )

    def _create_openshift_route(
        self,
        cmd: CommandExecutor,
        context: ExecutionContext,
        plan_config: dict,
        service_yaml: Path,
    ):
        """Expose a service as an OpenShift route with --target-port."""
        try:
            with open(service_yaml, encoding="utf-8") as f:
                svc_config = yaml.safe_load(f)
            svc_name = svc_config.get("metadata", {}).get("name", "")
            namespace = context.require_namespace()

            inference_port = self._require_config(
                plan_config, "vllmCommon", "inferencePort"
            )

            if svc_name:
                # Use a shorter route name to stay within the 63-char DNS label limit.
                # The full service name (vllm-standalone-{hash}) can be too long
                # when combined with namespace and cluster domain.
                model_id = plan_config.get("model_id_label", "")
                route_name = f"sa-{model_id}-route" if model_id else f"{svc_name}-route"
                if len(route_name) > 63:
                    route_name = route_name[:63]
                check = cmd.kube(
                    "get", "route", route_name,
                    "-n", namespace, "--ignore-not-found",
                )
                if check.success and not check.stdout.strip():
                    cmd.kube(
                        "expose", f"service/{svc_name}",
                        f"--name={route_name}",
                        f"--target-port={inference_port}",
                        "-n", namespace,
                    )
        except (yaml.YAMLError, OSError):
            pass

    def _check_priority_class(
        self, cmd: CommandExecutor, plan_config: dict,
        context: ExecutionContext,
    ) -> str | None:
        """Validate that the configured priorityClassName exists on the cluster."""
        priority_class = (
            plan_config.get("standalone", {}).get("priorityClassName")
            or plan_config.get("vllmCommon", {}).get("priorityClassName", "")
        )
        if not priority_class or priority_class.lower() == "none":
            return None

        result = cmd.kube(
            "get", "priorityclass", priority_class,
            "--ignore-not-found",
            "-o", "jsonpath={.metadata.name}",
            check=False,
        )
        if result.success and result.stdout.strip() == priority_class:
            context.logger.log_info(
                f'PriorityClass "{priority_class}" found on cluster'
            )
            return None

        list_result = cmd.kube(
            "get", "priorityclass",
            "-o", "jsonpath={.items[*].metadata.name}",
            check=False,
        )
        available = list_result.stdout.strip() if list_result.success else "(unable to list)"
        return (
            f'PriorityClass "{priority_class}" does not exist on this cluster. '
            f"Available priority classes: {available}"
        )

    def _propagate_standup_parameters(
        self, cmd: CommandExecutor, context: ExecutionContext,
        plan_config: dict
    ):
        """Persist deploy metadata as a ConfigMap so run-phase steps can read it."""
        from datetime import datetime, timezone
        from llmdbenchmark import __version__

        harness_ns = context.harness_namespace or context.require_namespace()
        cm_name = "llm-d-benchmark-standup-parameters"

        params = {
            "tool_name": "llm-d-benchmark",
            "tool_version": __version__,
            "deployed_by": context.username or "unknown",
            "deployed_at": datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            "cluster_name": context.cluster_name or "",
            "platform_type": context.platform_type,
            "namespace": context.namespace or "",
            "harness_namespace": harness_ns,
            "deploy_methods": ",".join(context.deployed_methods),
        }

        if plan_config:
            params["model_name"] = self._require_config(plan_config, "model", "name")
            params["model_short_name"] = self._require_config(plan_config, "model", "shortName")
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

        create_args = [
            "create", "configmap", cm_name,
            "--namespace", harness_ns,
        ] + literal_args + ["--dry-run=client", "-o", "yaml"]

        result = cmd.kube(*create_args)
        if result.success:
            yaml_path = (
                context.setup_yamls_dir() / "standup-parameters.yaml"
            )
            yaml_path.write_text(result.stdout, encoding="utf-8")
            apply_result = cmd.kube("apply", "-f", str(yaml_path))
            if apply_result.success:
                context.logger.log_info(
                    f"📋 Deployment metadata to configmap/{cm_name} in ns/{harness_ns}"
                )
                context.logger.log_info(
                    f"   oc get configmap {cm_name} -n {harness_ns} -o yaml"
                )
