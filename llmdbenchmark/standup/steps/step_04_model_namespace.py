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

        # PVC and download are per-stack: only needed for "pvc" protocol or
        # standalone mode — S3/OCI/hf protocols fetch at runtime and skip
        # PVC creation entirely, deferring to the modelservice chart (hf
        # downloads happen in the decode Pod's init container).
        # In multi-model scenarios different stacks can pick different
        # uriProtocols; the scenario's first stack no longer dictates the
        # choice for everyone.
        stacks = list(context.rendered_stacks or [])
        pvc_stacks: list[Path] = []
        for stack_path in stacks:
            stack_cfg = self._load_stack_config(stack_path)
            if self._requires_pvc_download(stack_cfg):
                pvc_stacks.append(stack_path)

        # Context secret (kubeconfig for harness pods) is scenario-wide —
        # one per namespace regardless of per-stack uriProtocol — so it
        # still keys off the first stack's config.
        plan_config = self._load_plan_config(context)

        # Multi-stack preflight: check the shared PVC is sized for the sum
        # of every pvc-protocol stack's model weights. Warn-only so single-
        # stack scenarios and operators with intentional headroom are
        # unaffected. See _warn_on_undersized_model_pvc for the rules.
        if len(pvc_stacks) >= 2:
            self._warn_on_undersized_model_pvc(context, pvc_stacks)

        for stack_path in pvc_stacks:
            self._create_model_pvc(cmd, context, errors, stack_path)
            self._create_extra_pvc(cmd, context, errors, stack_path)

        self._add_context_secret(cmd, context, errors, plan_config)

        if pvc_stacks:
            # Two-phase parallel download (only for stacks that need it):
            #   Phase 1 — apply every download Job back-to-back; they all
            #             start running concurrently in the cluster.
            #   Phase 2 — wait on each in turn. Subsequent waits return
            #             nearly instantly once their Job is already done,
            #             so total wall time ≈ max(individual), not sum.
            launched = []
            for stack_path in pvc_stacks:
                applied = self._apply_download_job(
                    cmd, context, errors, stack_path
                )
                if applied is not None:
                    job_name, download_yaml = applied
                    launched.append((stack_path, job_name, download_yaml))

            if len(launched) > 1:
                context.logger.log_info(
                    f"📦 Launched {len(launched)} model downloads in parallel; "
                    "waiting for completion..."
                )

            for stack_path, job_name, download_yaml in launched:
                self._wait_for_download_job(
                    cmd, context, errors, stack_path, job_name, download_yaml
                )

        skipped = len(stacks) - len(pvc_stacks)
        if skipped:
            context.logger.log_info(
                f"ℹ️  Skipped PVC + download job for {skipped} stack(s) using "
                "hf / s3 / oci protocol (modelservice handles fetch at runtime)"
            )

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

    @staticmethod
    def _parse_size_to_gib(raw: str | None) -> float:
        """Parse a k8s resource-size string to GiB as a float.

        Returns 0.0 when input is empty or unparseable — caller treats
        that as "unknown, skip comparison", which is the right fallback
        for a warn-only pre-flight.
        """
        import re
        if not raw:
            return 0.0
        s = str(raw).strip()
        m = re.match(r"^([0-9]*\.?[0-9]+)\s*([KMGTPE]i?)?$", s)
        if not m:
            return 0.0
        n = float(m.group(1))
        unit = (m.group(2) or "Gi").strip()
        table = {
            # Binary (IEC) units
            "Ki": n / (1024 ** 2), "Mi": n / 1024,
            "Gi": n, "Ti": n * 1024, "Pi": n * 1024 ** 2, "Ei": n * 1024 ** 3,
            # Decimal (SI) units — approximate to GiB
            "K": n / (1024 ** 2), "M": n / 1024, "G": n,
            "T": n * 1024, "P": n * 1024 ** 2, "E": n * 1024 ** 3,
        }
        return table.get(unit, n)

    def _warn_on_undersized_model_pvc(
        self, context: ExecutionContext, pvc_stacks: list[Path],
    ) -> None:
        """Warn if the shared model PVC looks too small to hold all models.

        Sums ``model.size`` across every pvc-protocol stack and compares
        against ``storage.modelPvc.size``. If the sum exceeds 90% of
        capacity, log a warning — the first download to run out of space
        would otherwise fail opaquely mid-standup. Warn-only: cluster
        storage classes with thin provisioning or aggressive compression
        may legitimately oversubscribe, so we don't block.

        Called only when ``len(pvc_stacks) >= 2``; single-stack scenarios
        don't have a summing problem.
        """
        first_cfg = self._load_stack_config(pvc_stacks[0])
        pvc_capacity_str = (
            first_cfg.get("storage", {}).get("modelPvc", {}).get("size")
        )
        capacity_gib = self._parse_size_to_gib(pvc_capacity_str)
        if capacity_gib == 0:
            return

        total_gib = 0.0
        per_stack_sizes: list[tuple[str, str]] = []
        for stack_path in pvc_stacks:
            cfg = self._load_stack_config(stack_path)
            model_size = cfg.get("model", {}).get("size")
            per_stack_sizes.append((stack_path.name, str(model_size or "?")))
            total_gib += self._parse_size_to_gib(model_size)

        if total_gib == 0:
            return  # all model.size unset/unparseable — skip quietly

        if total_gib > capacity_gib * 0.9:
            breakdown = ", ".join(f"{n}={s}" for n, s in per_stack_sizes)
            context.logger.log_warning(
                f"Shared model PVC size ({pvc_capacity_str}) may be "
                f"under-sized for this scenario: sum of model.size across "
                f"{len(pvc_stacks)} pvc-protocol stack(s) is ~{total_gib:.0f}GiB "
                f"(>= 90% of capacity). Stacks: {breakdown}. Downloads that "
                f"exceed capacity will fail mid-standup with an opaque PVC error."
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
        self,
        cmd: CommandExecutor,
        context: ExecutionContext,
        errors: list,
        stack_path: Path,
    ):
        """Create a single stack's model PVC from its rendered YAML.

        Reads PVC manifest + name/size from *this stack's* plan directory,
        not "the first stack across the scenario", so each model gets its
        own PVC named after its model ID label (see
        ``render_plans._resolve_per_stack_identity``).
        """
        pvc_yaml = self._find_yaml(stack_path, "02_pvc_model-pvc")
        if not pvc_yaml:
            return

        plan_config = self._load_stack_config(stack_path)
        pvc_name = self._require_config(plan_config, "storage", "modelPvc", "name")
        pvc_size = self._require_config(plan_config, "storage", "modelPvc", "size")

        namespace = context.require_namespace()
        already_exists = (
            not context.dry_run and self._check_existing_pvc(
                cmd, context, pvc_name, pvc_size, namespace, errors
            )
        )
        if not already_exists:
            result = cmd.kube("apply", "-f", str(pvc_yaml))
            if not result.success and "AlreadyExists" not in result.stderr:
                errors.append(f"Failed to create model PVC: {result.stderr}")
                return

        # Verify the PVC binds. Without this, a PVC stuck Pending (e.g.
        # cluster has no default StorageClass and the manifest was rendered
        # with storage_class=auto, which omits storageClassName) only
        # surfaces as a silent download-job timeout further downstream.
        bind_result = cmd.wait_for_pvc(
            pvc_name=pvc_name,
            namespace=namespace,
            timeout=context.pvc_bind_timeout,
            poll_interval=5,
            description=f'model PVC "{pvc_name}"',
        )
        if not bind_result.success:
            errors.append(
                f'Model PVC "{pvc_name}" did not bind: {bind_result.stderr}. '
                "Common cause: cluster has no default StorageClass and "
                'storage_class is "auto"/"default" (which omits storageClassName '
                "so the cluster default is required). Run `kubectl get sc` to "
                "verify, or set storage.modelPvc.storageClassName explicitly "
                "in the scenario."
            )

    def _create_extra_pvc(
        self,
        cmd: CommandExecutor,
        context: ExecutionContext,
        errors: list,
        stack_path: Path,
    ):
        """Create a single stack's extra PVC from its rendered YAML, if present."""
        extra_pvc_yaml = self._find_yaml(stack_path, "16_pvc_extra-pvc")
        if not extra_pvc_yaml:
            return

        try:
            content = extra_pvc_yaml.read_text(encoding="utf-8").strip()
            if not content:
                return
        except OSError:
            return

        plan_config = self._load_stack_config(stack_path)
        extra_pvc = plan_config.get("storage", {}).get("extraPvc", {}) if plan_config else {}
        pvc_name = extra_pvc.get("name", "")
        pvc_size = extra_pvc.get("size", "10Gi")

        if not pvc_name:
            return

        namespace = context.require_namespace()
        already_exists = (
            not context.dry_run and self._check_existing_pvc(
                cmd, context, pvc_name, pvc_size, namespace, errors
            )
        )
        if not already_exists:
            result = cmd.kube("apply", "-f", str(extra_pvc_yaml))
            if not result.success and "AlreadyExists" not in result.stderr:
                errors.append(f"Failed to create extra PVC: {result.stderr}")
                return

        # Verify the PVC binds. Without this, a PVC stuck Pending (e.g.
        # cluster has no default StorageClass and the manifest was rendered
        # with storage_class=auto, which omits storageClassName) only
        # surfaces as a silent pod-readiness timeout further downstream.
        bind_result = cmd.wait_for_pvc(
            pvc_name=pvc_name,
            namespace=namespace,
            timeout=context.pvc_bind_timeout,
            poll_interval=5,
            description=f'extra PVC "{pvc_name}"',
        )
        if not bind_result.success:
            errors.append(
                f'Extra PVC "{pvc_name}" did not bind: {bind_result.stderr}. '
                "Common cause: cluster has no default StorageClass and "
                'storage_class is "auto"/"default" (which omits storageClassName '
                "so the cluster default is required). Run `kubectl get sc` to "
                "verify, or set storage.extraPvc.storageClassName explicitly "
                "in the scenario."
            )

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

    def _apply_download_job(
        self, cmd: CommandExecutor, context: ExecutionContext,
        errors: list, stack_path: Path,
    ) -> tuple[str, Path] | None:
        """Apply a single stack's download Job without waiting.

        Phase 1 of the two-phase parallel-download flow: every stack's
        Job is applied back-to-back in ``execute()`` so all N downloads
        run concurrently in the cluster. Phase 2 (``_wait_for_download_job``)
        then waits on each in turn — since ``kubectl wait`` is a watch,
        not a poll, subsequent waits return nearly instantly once their
        Job has already completed. Total wall time is bounded by the
        slowest model, not the sum.

        Returns ``(job_name, download_yaml_path)`` for the waiter, or
        ``None`` if nothing was applied (missing rendered manifest, or
        apply failed — the error is appended to ``errors`` in that case).
        """
        download_yaml = self._find_yaml(stack_path, "04_download_job")
        if not download_yaml:
            return None

        plan_config = self._load_stack_config(stack_path)
        job_name = plan_config.get("downloadJob", {}).get("name") or "download-model"

        self._cleanup_download_pods(cmd, context, job_name)
        self._delete_existing_job(cmd, context, download_yaml)

        result = cmd.kube("apply", "-f", str(download_yaml))
        if not result.success:
            errors.append(
                f"Failed to launch model download job {job_name}: {result.stderr}"
            )
            return None

        return job_name, download_yaml

    def _wait_for_download_job(
        self, cmd: CommandExecutor, context: ExecutionContext,
        errors: list, stack_path: Path, job_name: str, download_yaml: Path,
    ) -> None:
        """Wait for a single stack's download Job to complete, with retries.

        Phase 2 of the parallel-download flow. By the time this runs the
        Job is already executing in the cluster (applied in phase 1), so
        the wait itself isn't on a cold start. Retries re-apply just
        *this* Job while other stacks' downloads continue or complete
        independently.
        """
        plan_config = self._load_stack_config(stack_path)
        timeout = int(self._require_config(plan_config, "storage", "downloadTimeout"))
        max_retries = int(self._require_config(plan_config, "storage", "downloadMaxRetries"))

        for attempt in range(1, max_retries + 1):
            wait_result = cmd.wait_for_job(
                job_name=job_name,
                namespace=context.require_namespace(),
                timeout=timeout,
                poll_interval=15,
                description=f"model download {job_name} (attempt {attempt}/{max_retries})",
            )
            if wait_result.success:
                context.logger.log_info(
                    f"✅ Model download {job_name} completed (attempt {attempt})"
                )
                return

            if attempt < max_retries:
                context.logger.log_warning(
                    f"⚠️  Model download {job_name} failed "
                    f"(attempt {attempt}), retrying..."
                )
                self._cleanup_download_pods(cmd, context, job_name)
                self._delete_existing_job(cmd, context, download_yaml)
                re_result = cmd.kube("apply", "-f", str(download_yaml))
                if not re_result.success:
                    errors.append(
                        f"Failed to re-launch download job {job_name}: {re_result.stderr}"
                    )
                    return
            else:
                errors.append(
                    f"Model download job {job_name} did not complete after "
                    f"{max_retries} attempts: {wait_result.stderr}"
                )

    def _cleanup_download_pods(
        self,
        cmd: CommandExecutor,
        context: ExecutionContext,
        job_name: str = "download-model",
    ):
        """Delete completed/failed download job pods before re-launching.

        Scoped to a specific ``job_name`` so multi-stack scenarios (each
        with its own ``download-{model_id_label}`` Job) only clean up
        their own Pods instead of clobbering a sibling stack's
        in-flight download.
        """
        namespace = context.require_namespace()
        cmd.kube(
            "delete",
            "pods",
            f"--selector=job-name={job_name}",
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
