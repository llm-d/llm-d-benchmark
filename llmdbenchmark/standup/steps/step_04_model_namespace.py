"""Step 04 -- Prepare the model namespace (PVC, secrets, download job)."""

import json
import tempfile
from pathlib import Path

import yaml

from llmdbenchmark.executor.step import Step, StepResult, Phase
from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.executor.command import CommandExecutor


class ModelNamespaceStep(Step):
    """Prepare the model namespace with PVC, secrets, and download job."""

    def __init__(self):
        super().__init__(
            number=4,
            name="model_namespace",
            description="Prepare model namespace (PVC, secrets, download job)",
            phase=Phase.STANDUP,
            per_stack=False,
        )

    def execute(
        self, context: ExecutionContext, stack_path: Path | None = None
    ) -> StepResult:
        errors = []
        cmd = context.require_cmd()

        self._apply_namespace_resources(cmd, context, errors)

        # Must run after namespace creation so the annotation exists
        if context.is_openshift and context.namespace:
            self._extract_openshift_uid_range(cmd, context)

        if not context.dry_run:
            sc_error = self._validate_storage_class(cmd, context)
            if sc_error:
                errors.append(sc_error)
                return StepResult(
                    step_number=self.number,
                    step_name=self.name,
                    success=False,
                    message="Storage class validation failed",
                    errors=errors,
                )

        # PVC and download only needed for "pvc" protocol or standalone mode;
        # S3/OCI/hf protocols fetch at runtime
        plan_config = self._load_plan_config(context)
        needs_pvc = self._requires_pvc_download(plan_config)

        if needs_pvc:
            self._create_model_pvc(cmd, context, errors)
            self._create_extra_pvc(cmd, context, errors)

        self._add_context_secret(cmd, context, errors, plan_config)

        if needs_pvc:
            self._launch_download_job(cmd, context, errors, plan_config)

        if errors:
            for err in errors:
                context.logger.log_error(f"    {err}")
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=False,
                message="Model namespace preparation had errors",
                errors=errors,
            )

        return StepResult(
            step_number=self.number,
            step_name=self.name,
            success=True,
            message=f"Model namespace prepared (ns={context.namespace})",
        )

    def _requires_pvc_download(self, plan_config: dict | None) -> bool:
        """Return True when models need a PVC and pre-download job."""
        if not plan_config:
            raise KeyError(
                "Required plan config not found. Ensure the plan phase "
                "has been run before standup."
            )

        uri_protocol = self._require_config(plan_config, "modelservice", "uriProtocol")
        standalone_enabled = (
            plan_config.get("standalone", {}).get("enabled", False)
        )

        return uri_protocol == "pvc" or standalone_enabled

    def _apply_namespace_resources(
        self, cmd: CommandExecutor, context: ExecutionContext, errors: list
    ):
        """Apply namespace, ServiceAccount, RBAC, and secret resources."""
        ns_yaml = self._find_rendered_yaml(context, "05_namespace_sa_rbac_secret")
        if not ns_yaml:
            return

        result = cmd.kube("apply", "-f", str(ns_yaml))
        if not result.success:
            errors.append(f"Failed to apply namespace resources: {result.stderr}")

        try:
            with open(ns_yaml, encoding="utf-8") as f:
                docs = list(yaml.safe_load_all(f))
            for doc in docs:
                if doc and doc.get("kind") == "Namespace":
                    context.namespace = doc["metadata"]["name"]
                    break
        except (yaml.YAMLError, OSError):
            pass

    def _create_model_pvc(
        self, cmd: CommandExecutor, context: ExecutionContext, errors: list
    ):
        """Create the model PVC from rendered YAML."""
        pvc_yaml = self._find_rendered_yaml(context, "02_pvc_model-pvc")
        if not pvc_yaml:
            return

        plan_config = self._load_plan_config(context)
        pvc_name = self._require_config(plan_config, "storage", "modelPvc", "name")
        pvc_size = self._require_config(plan_config, "storage", "modelPvc", "size")

        namespace = context.require_namespace()
        if not context.dry_run and self._check_existing_pvc(
            cmd, context, pvc_name, pvc_size, namespace, errors
        ):
            return

        result = cmd.kube("apply", "-f", str(pvc_yaml))
        if not result.success and "AlreadyExists" not in result.stderr:
            errors.append(f"Failed to create model PVC: {result.stderr}")

    def _create_extra_pvc(
        self, cmd: CommandExecutor, context: ExecutionContext, errors: list
    ):
        """Create the extra PVC from rendered YAML, if present."""
        extra_pvc_yaml = self._find_rendered_yaml(context, "16_pvc_extra-pvc")
        if not extra_pvc_yaml:
            return

        try:
            content = extra_pvc_yaml.read_text(encoding="utf-8").strip()
            if not content:
                return
        except OSError:
            return

        plan_config = self._load_plan_config(context)
        extra_pvc = plan_config.get("storage", {}).get("extraPvc", {}) if plan_config else {}
        pvc_name = extra_pvc.get("name", "")
        pvc_size = extra_pvc.get("size", "10Gi")

        if not pvc_name:
            return

        namespace = context.require_namespace()
        if not context.dry_run and self._check_existing_pvc(
            cmd, context, pvc_name, pvc_size, namespace, errors
        ):
            return

        result = cmd.kube("apply", "-f", str(extra_pvc_yaml))
        if not result.success and "AlreadyExists" not in result.stderr:
            errors.append(f"Failed to create extra PVC: {result.stderr}")

    def _add_context_secret(
        self, cmd: CommandExecutor, context: ExecutionContext,
        errors: list, plan_config: dict | None = None,
    ):
        """Save the kubeconfig as a Secret so downstream pods can access the cluster."""
        namespace = context.require_namespace()

        secret_name = self._require_config(plan_config, "control", "contextSecretName")

        kubeconfig_path = context.kubeconfig
        if not kubeconfig_path:
            ctx_file = context.environment_dir() / "context.ctx"
            if ctx_file.exists():
                kubeconfig_path = str(ctx_file)

        if not kubeconfig_path:
            context.logger.log_info(
                "ℹ️  No kubeconfig available -- skipping context secret"
            )
            return

        # Data key matches the secret name for consistent volume mounts
        result = cmd.kube(
            "create",
            "secret",
            "generic",
            secret_name,
            f"--from-file={secret_name}={kubeconfig_path}",
            "--namespace",
            namespace,
            "--dry-run=client",
            "-o",
            "yaml",
        )
        if result.success and result.stdout.strip():
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".yaml", delete=False
            ) as tmp:
                tmp.write(result.stdout)
                tmp_path = tmp.name
            apply_result = cmd.kube("apply", "-f", tmp_path, check=False)
            Path(tmp_path).unlink(missing_ok=True)
            if not apply_result.success:
                context.logger.log_warning(
                    f"Could not create context secret: {apply_result.stderr}"
                )
        else:
            context.logger.log_warning(
                f"Could not generate context secret: {result.stderr}"
            )

    def _launch_download_job(
        self, cmd: CommandExecutor, context: ExecutionContext,
        errors: list, plan_config: dict | None = None,
    ):
        """Launch the model download job and wait for completion."""
        download_yaml = self._find_rendered_yaml(context, "04_download_job")
        if not download_yaml:
            return

        self._cleanup_download_pods(cmd, context)

        self._delete_existing_job(cmd, context, download_yaml)

        result = cmd.kube("apply", "-f", str(download_yaml))
        if not result.success:
            errors.append(f"Failed to launch model download job: {result.stderr}")
            return

        timeout = int(self._require_config(plan_config, "storage", "downloadTimeout"))
        max_retries = int(self._require_config(plan_config, "storage", "downloadMaxRetries"))
        for attempt in range(1, max_retries + 1):
            wait_result = cmd.wait_for_job(
                job_name="download-model",
                namespace=context.require_namespace(),
                timeout=timeout,
                poll_interval=15,
                description=f"model download (attempt {attempt}/{max_retries})",
            )
            if wait_result.success:
                context.logger.log_info(
                    f"✅ Model download completed (attempt {attempt})"
                )
                return

            if attempt < max_retries:
                context.logger.log_warning(
                    f"⚠️  Model download failed (attempt {attempt}), retrying..."
                )
                self._cleanup_download_pods(cmd, context)
                self._delete_existing_job(cmd, context, download_yaml)
                re_result = cmd.kube("apply", "-f", str(download_yaml))
                if not re_result.success:
                    errors.append(
                        f"Failed to re-launch download job: {re_result.stderr}"
                    )
                    return
            else:
                errors.append(
                    f"Model download job did not complete after "
                    f"{max_retries} attempts: {wait_result.stderr}"
                )

    def _cleanup_download_pods(self, cmd: CommandExecutor, context: ExecutionContext):
        """Delete completed/failed download job pods before re-launching."""
        namespace = context.require_namespace()
        cmd.kube(
            "delete",
            "pods",
            "--selector=job-name=download-model",
            "--namespace",
            namespace,
            "--field-selector=status.phase!=Running",
            "--ignore-not-found",
        )

    def _delete_existing_job(
        self, cmd: CommandExecutor, context: ExecutionContext, download_yaml: Path
    ):
        """Delete any existing download job before re-creating it."""
        try:
            with open(download_yaml, encoding="utf-8") as f:
                job_config = yaml.safe_load(f)

            if job_config:
                job_name = job_config.get("metadata", {}).get("name", "download-model")
                job_ns = job_config.get("metadata", {}).get(
                    "namespace", context.require_namespace()
                )

                cmd.kube(
                    "delete",
                    "job",
                    job_name,
                    "--namespace",
                    job_ns,
                    "--ignore-not-found",
                )
        except (yaml.YAMLError, OSError):
            pass

    def _validate_storage_class(
        self, cmd: CommandExecutor, context: ExecutionContext
    ) -> str | None:
        """Validate that configured PVC storage class exists on the cluster."""
        plan_config = self._load_plan_config(context)
        if not plan_config:
            return None

        storage = plan_config.get("storage", {})

        storage_classes_to_check: set[str] = set()
        for pvc_key in ("modelPvc", "workloadPvc", "extraPvc"):
            pvc = storage.get(pvc_key, {})
            sc = pvc.get("storageClassName", "")

            if pvc_key == "extraPvc" and not pvc.get("name"):
                continue  # extraPvc disabled

            if sc:
                storage_classes_to_check.add(sc)

        if not storage_classes_to_check:
            return None

        result = cmd.kube("get", "storageclass", "-o", "json", check=False)
        if not result.success:
            return f"Cannot list storage classes: {result.stderr}"

        try:
            sc_data = json.loads(result.stdout)
        except Exception as e:
            return f"Failed to parse storage class list: {e}"

        items = sc_data.get("items", [])
        available = [i["metadata"]["name"] for i in items]

        default_sc = None
        for item in items:
            ann = item.get("metadata", {}).get("annotations", {})
            if (
                ann.get("storageclass.kubernetes.io/is-default-class") == "true"
                or ann.get("storageclass.beta.kubernetes.io/is-default-class") == "true"
            ):
                default_sc = item["metadata"]["name"]
                break

        for sc_name in storage_classes_to_check:
            if sc_name.lower() in ("auto", "default"):
                if default_sc:
                    context.logger.log_info(
                        f"Auto-detected default storage class: {default_sc}"
                    )
                else:
                    context.logger.log_warning(
                        "No default storage class found on cluster, "
                        "PVCs will rely on cluster default behavior"
                    )
                continue

            if sc_name not in available:
                return (
                    f'StorageClass "{sc_name}" does not exist. '
                    f"Available: {' '.join(available) if available else '(none found)'}"
                )

            context.logger.log_info(f'StorageClass "{sc_name}" validated')

        return None

    def _extract_openshift_uid_range(
        self, cmd: CommandExecutor, context: ExecutionContext
    ):
        """Extract proxy UID from the OpenShift namespace UID range annotation."""
        if context.dry_run:
            return

        namespace = context.namespace
        result = cmd.kube(
            "get",
            "namespace",
            namespace,
            "-o",
            "jsonpath={.metadata.annotations.openshift\\.io/sa\\.scc\\.uid-range}",
            check=False,
        )
        if not result.success or not result.stdout.strip():
            context.logger.log_info(
                "ℹ️  Could not read openshift.io/sa.scc.uid-range -- " "proxy_uid not set"
            )
            return

        uid_range = result.stdout.strip().strip("'\"")
        try:
            first_uid = int(uid_range.split("/")[0])
            context.proxy_uid = first_uid + 1
            context.logger.log_info(
                f"🔑 OpenShift proxy UID: {context.proxy_uid} "
                f"(from uid-range {uid_range})"
            )
        except (ValueError, IndexError):
            context.logger.log_warning(f"⚠️  Could not parse uid-range '{uid_range}'")
