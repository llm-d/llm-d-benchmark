"""Step 05 -- Prepare the harness namespace (PVC, data access pod, secrets)."""

import base64
from pathlib import Path

import yaml

from llmdbenchmark.executor.step import Step, StepResult, Phase
from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.executor.command import CommandExecutor


class HarnessNamespaceStep(Step):
    """Prepare the harness namespace with PVC and data access pod."""

    def __init__(self):
        super().__init__(
            number=5,
            name="harness_namespace",
            description="Prepare harness namespace (PVC, data access pod)",
            phase=Phase.STANDUP,
            per_stack=False,
        )

    def execute(
        self, context: ExecutionContext, stack_path: Path | None = None
    ) -> StepResult:
        errors = []
        cmd = context.require_cmd()

        plan_config = self._load_plan_config(context)

        harness_ns = None
        if plan_config:
            harness_ns = plan_config.get("harness", {}).get(
                "namespace",
                plan_config.get("namespace", {}).get("name", "")
            )
        if not harness_ns:
            harness_ns = context.require_namespace()
        context.harness_namespace = harness_ns

        self._create_harness_namespace(cmd, context, harness_ns, errors)
        self._create_hf_token_secret(cmd, context, plan_config, harness_ns, errors)

        pvc_yaml = self._find_rendered_yaml(context, "01_pvc_workload-pvc")
        if pvc_yaml:
            pvc_name = plan_config.get("storage", {}).get("workloadPvc", {}).get("name", "workload-pvc") if plan_config else "workload-pvc"
            harness_pvc_size = plan_config.get("harness", {}).get("pvcSize") if plan_config else None
            pvc_size = harness_pvc_size or (plan_config.get("storage", {}).get("workloadPvc", {}).get("size", "20Gi") if plan_config else "20Gi")

            if not context.dry_run and self._check_existing_pvc(
                cmd, context, pvc_name, pvc_size, harness_ns, errors
            ):
                context.logger.log_info(f"Using existing workload PVC '{pvc_name}'")
            else:
                result = cmd.kube("apply", "-f", str(pvc_yaml))
                if not result.success and "AlreadyExists" not in result.stderr:
                    errors.append(
                        f"Failed to create workload PVC: {result.stderr}"
                    )

        pod_yaml = self._find_rendered_yaml(
            context, "06_pod_access_to_harness_data"
        )
        if pod_yaml:
            result = cmd.kube("apply", "-f", str(pod_yaml))
            if not result.success:
                errors.append(
                    f"Failed to create data access pod: {result.stderr}"
                )

        svc_yaml = self._find_rendered_yaml(
            context, "07_service_access_to_harness_data"
        )
        if svc_yaml:
            result = cmd.kube("apply", "-f", str(svc_yaml))
            if not result.success:
                errors.append(
                    f"Failed to create data access service: {result.stderr}"
                )

        model_ns = context.require_namespace()
        configmap_namespaces = [harness_ns]
        if model_ns != harness_ns:
            configmap_namespaces.append(model_ns)
        self._create_preprocesses_configmap(cmd, context, configmap_namespaces, errors)

        wait_result = cmd.wait_for_pods(
            label="role=llm-d-benchmark-data-access",
            namespace=harness_ns,
            timeout=600,
            poll_interval=5,
            description="harness data-access pod",
        )
        if not wait_result.success:
            errors.append(
                f"Data access pod not ready: {wait_result.stderr}"
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
            message=f"Harness namespace prepared (ns={harness_ns})",
        )

    def _create_harness_namespace(
        self, cmd: CommandExecutor, context: ExecutionContext,
        harness_ns: str, errors: list
    ):
        """Create the harness namespace if it doesn't exist."""
        ns_yaml = f"""apiVersion: v1
kind: Namespace
metadata:
  name: {harness_ns}
"""
        yaml_path = context.setup_yamls_dir() / "harness-namespace.yaml"
        yaml_path.write_text(ns_yaml, encoding="utf-8")
        result = cmd.kube("apply", "-f", str(yaml_path))
        if not result.success and "AlreadyExists" not in result.stderr:
            errors.append(
                f"Failed to create harness namespace: {result.stderr}"
            )

    def _create_hf_token_secret(
        self, cmd: CommandExecutor, context: ExecutionContext,
        plan_config: dict | None, harness_ns: str, errors: list
    ):
        """Copy the HuggingFace token secret into the harness namespace."""
        if not plan_config:
            return

        hf_token_name = self._require_config(plan_config, "huggingface", "secretName")
        hf_token_key = self._require_config(plan_config, "huggingface", "tokenKey")

        check = cmd.kube(
            "get", "secret", hf_token_name,
            "--namespace", harness_ns,
            "--ignore-not-found",
        )
        if check.success and check.stdout.strip():
            context.logger.log_info(
                f"✅ HF token secret already exists in {harness_ns}"
            )
            return

        model_ns = context.require_namespace()
        get_result = cmd.kube(
            "get", "secret", hf_token_name,
            "--namespace", model_ns,
            "-o", "yaml",
        )
        if get_result.success and get_result.stdout.strip():
            try:
                secret_doc = yaml.safe_load(get_result.stdout)
                if secret_doc:
                    secret_doc["metadata"]["namespace"] = harness_ns
                    secret_doc["metadata"].pop("resourceVersion", None)
                    secret_doc["metadata"].pop("uid", None)
                    secret_doc["metadata"].pop("creationTimestamp", None)
                    managed = secret_doc["metadata"].pop("managedFields", None)

                    yaml_path = (
                        context.setup_yamls_dir() / "harness-hf-secret.yaml"
                    )
                    with open(yaml_path, "w", encoding="utf-8") as f:
                        yaml.dump(secret_doc, f, default_flow_style=False)

                    apply_result = cmd.kube(
                        "apply", "-f", str(yaml_path)
                    )
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

    def _create_preprocesses_configmap(
        self, cmd: CommandExecutor, context: ExecutionContext,
        namespaces: list[str], errors: list
    ):
        """Bundle preprocess scripts into a ConfigMap and apply to each namespace."""
        preprocess_dir = context.preprocess_dir()
        config_map_name = "llm-d-benchmark-preprocesses"

        context.logger.log_info(
            "🚚 Creating configmap with preprocess scripts..."
        )

        if not preprocess_dir or not preprocess_dir.is_dir():
            context.logger.log_warning(
                "Preprocess directory not found -- creating empty ConfigMap"
            )
            for ns in namespaces:
                result = cmd.kube(
                    "create", "configmap", config_map_name,
                    "--namespace", ns,
                    "--dry-run=client", "-o", "yaml",
                )
                if result.success:
                    yaml_path = (
                        context.setup_yamls_dir()
                        / f"preprocesses-configmap-empty-{ns}.yaml"
                    )
                    yaml_path.write_text(result.stdout, encoding="utf-8")
                    cmd.kube("apply", "-f", str(yaml_path))
            return

        from_file_args = []
        file_paths = []
        try:
            file_paths = sorted(
                p for p in preprocess_dir.rglob("*") if p.is_file()
            )
            for path in file_paths:
                from_file_args.extend([
                    f"--from-file={path.name}={path}",
                ])
        except OSError as exc:
            context.logger.log_warning(
                f"Error reading preprocess directory: {exc}"
            )

        if not from_file_args:
            context.logger.log_info(
                "No preprocess files found -- creating empty ConfigMap"
            )
            return

        for ns in namespaces:
            create_args = [
                "create", "configmap", config_map_name,
                "--namespace", ns,
            ] + from_file_args + ["--dry-run=client", "-o", "yaml"]

            result = cmd.kube(*create_args)
            if result.success:
                yaml_path = (
                    context.setup_yamls_dir() / f"preprocesses-configmap-{ns}.yaml"
                )
                yaml_path.write_text(result.stdout, encoding="utf-8")
                apply_result = cmd.kube("apply", "-f", str(yaml_path))
                if not apply_result.success:
                    context.logger.log_warning(
                        f"Failed to apply preprocesses configmap in ns/{ns}: "
                        f"{apply_result.stderr}"
                    )
                else:
                    context.logger.log_info(
                        f"📦 ConfigMap \"{config_map_name}\" created in ns/{ns} "
                        f"with {len(file_paths)} file(s):"
                    )
                    for path in file_paths:
                        size_kb = path.stat().st_size / 1024
                        context.logger.log_info(
                            f"    │ {path.name} ({size_kb:.1f} KB)"
                        )
            else:
                context.logger.log_warning(
                    f"Failed to generate preprocesses configmap for ns/{ns}: {result.stderr}"
                )
