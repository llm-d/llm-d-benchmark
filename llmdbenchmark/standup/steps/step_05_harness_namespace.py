"""Step 05 -- Prepare the harness namespace (secrets/ConfigMap; PVC + data-access pod in PVC mode)."""

from pathlib import Path

import yaml

from llmdbenchmark.executor.step import Step, StepResult, Phase
from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.executor.command import CommandExecutor
from llmdbenchmark.utilities.preprocess_configmap import create_preprocess_configmap


class HarnessNamespaceStep(Step):
    """Prepare the harness namespace with PVC and data access pod."""

    def __init__(self):
        super().__init__(
            number=5,
            name="harness_namespace",
            description=(
                "Prepare harness namespace (PVC + data-access pod in PVC mode)"
            ),
            phase=Phase.STANDUP,
            per_stack=False,
        )

    def should_skip(self, context: ExecutionContext) -> bool:
        methods = context.deployed_methods or []
        if "nok8s" in methods:
            return True
        return methods == ["kustomize"] and context.kustomize_skip_infra

    def execute(
        self, context: ExecutionContext, stack_path: Path | None = None
    ) -> StepResult:
        errors = []
        cmd = context.require_cmd()

        plan_config = self._load_plan_config(context)

        harness_ns = None
        if plan_config:
            harness_ns = plan_config.get("harness", {}).get(
                "namespace", plan_config.get("namespace", {}).get("name", "")
            )
        if not harness_ns:
            harness_ns = context.require_namespace()
        context.harness_namespace = harness_ns

        self._create_harness_namespace(cmd, context, harness_ns, errors)
        hf_enabled = (
            plan_config.get("huggingface", {}).get("enabled", True)
            if plan_config
            else True
        )
        if hf_enabled:
            self._create_hf_token_secret(cmd, context, plan_config, harness_ns, errors)
        else:
            context.logger.log_info(
                "HF token not configured -- skipping secret creation"
            )

        # Model-ns copy is owned by standup step 04; only the harness copy
        # is this step's concern. When the namespaces are equal the step 04
        # write already covered it, but apply is idempotent.
        create_preprocess_configmap(cmd, context, [harness_ns])

        if context.no_pvc:
            context.logger.log_info(
                "\u2139\ufe0f  Skipped workload PVC + data-access pod (--no-pvc)"
            )
            if errors:
                for err in errors:
                    context.logger.log_error(f"    {err}")
                return StepResult(
                    step_number=self.number,
                    step_name=self.name,
                    success=False,
                    message="Harness namespace preparation had errors",
                    errors=errors,
                )
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=True,
                message=(
                    f"Harness namespace prepared (ns={harness_ns}; --no-pvc: "
                    f"workload PVC and data-access pod skipped)"
                ),
            )

        bind_deferred = False
        pvc_yaml = self._find_rendered_yaml(context, "01_pvc_workload-pvc")
        if pvc_yaml:
            pvc_name = (
                plan_config.get("storage", {})
                .get("workloadPvc", {})
                .get("name", "workload-pvc")
                if plan_config
                else "workload-pvc"
            )
            harness_pvc_size = (
                plan_config.get("harness", {}).get("pvcSize") if plan_config else None
            )
            pvc_size = harness_pvc_size or (
                plan_config.get("storage", {})
                .get("workloadPvc", {})
                .get("size", "20Gi")
                if plan_config
                else "20Gi"
            )

            if not context.dry_run and self._check_existing_pvc(
                cmd, context, pvc_name, pvc_size, harness_ns, errors
            ):
                context.logger.log_info(f"Using existing workload PVC '{pvc_name}'")
            else:
                result = cmd.kube("apply", "-f", str(pvc_yaml))
                if not result.success and "AlreadyExists" not in result.stderr:
                    errors.append(f"Failed to create workload PVC: {result.stderr}")

            # Verify the PVC binds before applying anything that mounts it.
            # A PVC stuck Pending (e.g. cluster has no default StorageClass
            # and the manifest was rendered with storage_class=auto, which
            # omits storageClassName) would otherwise surface only as a
            # silent pod-readiness timeout on the data-access pod below.
            if not errors:
                bind_result = cmd.wait_for_pvc(
                    pvc_name=pvc_name,
                    namespace=harness_ns,
                    timeout=context.pvc_bind_timeout,
                    poll_interval=5,
                    description=f'workload PVC "{pvc_name}"',
                )
                if not bind_result.success:
                    errors.append(
                        f'Workload PVC "{pvc_name}" did not bind: '
                        f"{bind_result.stderr}. Common cause: cluster has no "
                        "default StorageClass and storage_class is "
                        '"auto"/"default" (which omits storageClassName so '
                        "the cluster default is required). Run "
                        "`kubectl get sc` to verify, or set "
                        "storage.workloadPvc.storageClassName explicitly in "
                        "the scenario."
                    )
                    for err in errors:
                        context.logger.log_error(f"    {err}")
                    return StepResult(
                        step_number=self.number,
                        step_name=self.name,
                        success=False,
                        message="Workload PVC failed to bind -- aborting",
                        errors=errors,
                    )
                bind_deferred = bind_result.wait_skipped

        pod_yaml = self._find_rendered_yaml(context, "06_pod_access_to_harness_data")
        if pod_yaml:
            result = cmd.kube("apply", "-f", str(pod_yaml))
            if not result.success:
                if (
                    "Forbidden" in result.stderr
                    and "pod updates may not change" in result.stderr
                ):
                    context.logger.log_info(
                        "Data access pod spec changed — deleting stale pod and recreating..."
                    )
                    cmd.kube(
                        "delete",
                        "pod",
                        "access-to-harness-data-workload-pvc",
                        "--namespace",
                        harness_ns,
                        "--ignore-not-found",
                        check=False,
                    )
                    result = cmd.kube("apply", "-f", str(pod_yaml))
                if not result.success:
                    errors.append(f"Failed to create data access pod: {result.stderr}")

        svc_yaml = self._find_rendered_yaml(
            context, "07_service_access_to_harness_data"
        )
        if svc_yaml:
            result = cmd.kube("apply", "-f", str(svc_yaml))
            if not result.success:
                errors.append(f"Failed to create data access service: {result.stderr}")

        timeout = context.harness_data_access_timeout
        if bind_deferred:
            # The bind wait was skipped, so this wait covers dynamic volume
            # provisioning (which only starts once the pod schedules) plus
            # container start. Carry the unspent bind budget forward instead
            # of silently halving the budget for the harder job.
            timeout += context.pvc_bind_timeout
            context.logger.log_info(
                f"PVC bind wait was skipped (WaitForFirstConsumer) -- "
                f"extending data-access pod wait to {timeout}s "
                f"({context.harness_data_access_timeout}s + "
                f"{context.pvc_bind_timeout}s unspent bind budget)"
            )
        wait_result = cmd.wait_for_pods(
            label="role=llm-d-benchmark-data-access",
            namespace=harness_ns,
            timeout=timeout,
            poll_interval=5,
            description="harness data-access pod",
        )
        if not wait_result.success:
            msg = f"Data access pod not ready: {wait_result.stderr}"
            if bind_deferred:
                msg += (
                    ". The PVC bind wait was skipped (StorageClass "
                    "volumeBindingMode=WaitForFirstConsumer), so this wait "
                    "may also have covered volume provisioning. Raise "
                    "--data-access-timeout (LLMDBENCH_DATA_ACCESS_TIMEOUT) "
                    "and/or --pvc-bind-timeout for slow shared storage."
                )
            errors.append(msg)

        if errors:
            for err in errors:
                context.logger.log_error(f"    {err}")
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=False,
                message="Harness namespace preparation had errors",
                errors=errors,
            )

        return StepResult(
            step_number=self.number,
            step_name=self.name,
            success=True,
            message=f"Harness namespace prepared (ns={harness_ns})",
        )

    def _create_harness_namespace(
        self,
        cmd: CommandExecutor,
        context: ExecutionContext,
        harness_ns: str,
        errors: list,
    ):
        """Create the harness namespace if it doesn't exist."""
        check = cmd.kube("get", "namespace", harness_ns)
        if check.success:
            return

        ns_yaml = f"""apiVersion: v1
kind: Namespace
metadata:
  name: {harness_ns}
"""
        yaml_path = context.setup_yamls_dir() / "harness-namespace.yaml"
        yaml_path.write_text(ns_yaml, encoding="utf-8")
        result = cmd.kube("apply", "-f", str(yaml_path))
        if not result.success and "AlreadyExists" not in result.stderr:
            errors.append(f"Failed to create harness namespace: {result.stderr}")

    def _create_hf_token_secret(
        self,
        cmd: CommandExecutor,
        context: ExecutionContext,
        plan_config: dict | None,
        harness_ns: str,
        errors: list,
    ):
        """Copy the HuggingFace token secret into the harness namespace."""
        if not plan_config:
            return

        hf_token_name = self._require_config(plan_config, "huggingface", "secretName")
        hf_token_key = self._require_config(plan_config, "huggingface", "tokenKey")  # noqa: F841

        check = cmd.kube(
            "get",
            "secret",
            hf_token_name,
            "--namespace",
            harness_ns,
            "--ignore-not-found",
        )
        if check.success and check.stdout.strip():
            context.logger.log_info(
                f"✅ HF token secret already exists in {harness_ns}"
            )
            return

        model_ns = context.require_namespace()
        get_result = cmd.kube(
            "get",
            "secret",
            hf_token_name,
            "--namespace",
            model_ns,
            "-o",
            "yaml",
        )
        if get_result.success and get_result.stdout.strip():
            try:
                secret_doc = yaml.safe_load(get_result.stdout)
                if secret_doc:
                    secret_doc["metadata"]["namespace"] = harness_ns
                    secret_doc["metadata"].pop("resourceVersion", None)
                    secret_doc["metadata"].pop("uid", None)
                    secret_doc["metadata"].pop("creationTimestamp", None)
                    managed = secret_doc["metadata"].pop("managedFields", None)  # noqa: F841

                    yaml_path = context.setup_yamls_dir() / "harness-hf-secret.yaml"
                    with open(yaml_path, "w", encoding="utf-8") as f:
                        yaml.dump(secret_doc, f, default_flow_style=False)

                    apply_result = cmd.kube("apply", "-f", str(yaml_path))
                    if apply_result.success:
                        context.logger.log_info(
                            f"✅ HF token secret created in {harness_ns}"
                        )
                    else:
                        context.logger.log_warning(
                            f"Could not create HF secret in harness ns: "
                            f"{apply_result.stderr}"
                        )
            except (yaml.YAMLError, KeyError) as exc:
                context.logger.log_warning(
                    f"Could not copy HF secret to harness ns: {exc}"
                )
