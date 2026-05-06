"""Teardown Step 01 -- Uninstall Helm releases, OpenShift routes, and download jobs."""

from pathlib import Path

import yaml

from llmdbenchmark.executor.step import Step, StepResult, Phase
from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.executor.command import CommandExecutor
from llmdbenchmark.utilities.kube_helpers import wait_for_pods_deleted


class UninstallHelmStep(Step):
    """Uninstall Helm releases and associated routes."""

    def __init__(self):
        super().__init__(
            number=1,
            name="uninstall_helm",
            description="Uninstall Helm releases in target namespaces",
            phase=Phase.TEARDOWN,
            per_stack=False,
        )

    def should_skip(self, context: ExecutionContext) -> bool:
        # We still run for WVA-enabled stacks even when neither
        # modelservice nor fma is in deployed_methods, so the VA+HPA
        # resources get cleaned up.
        if ("modelservice" in context.deployed_methods
                or "fma" in context.deployed_methods):
            return False
        return not self._any_stack_has_wva(context)

    @staticmethod
    def _any_stack_has_wva(context: ExecutionContext) -> bool:
        """Return True if any rendered stack has wva.enabled: true."""
        for stack_path in context.rendered_stacks or []:
            cfg_file = stack_path / "config.yaml"
            if not cfg_file.exists():
                continue
            try:
                with open(cfg_file, encoding="utf-8") as fh:
                    cfg = yaml.safe_load(fh) or {}
            except (OSError, yaml.YAMLError):
                continue
            if (cfg.get("wva", {}) or {}).get("enabled", False):
                return True
        return False

    def execute(
        self, context: ExecutionContext, stack_path: Path | None = None
    ) -> StepResult:
        errors = []
        cmd = context.require_cmd()

        release = context.release
        namespaces = self._all_target_namespaces(context)

        model_labels = self._collect_model_labels(context)

        is_fma_enabled = "fma" in context.deployed_methods

        # Delete FMA CRs before uninstalling the Helm chart so the
        # controller is still running and can remove pod finalizers.
        if is_fma_enabled:
            for ns in namespaces:
                self._delete_fma_crs(cmd, context, ns)

        for ns in namespaces:
            self._uninstall_releases(cmd, context, ns, release, model_labels, errors)
            if not is_fma_enabled:
                self._delete_openshift_routes(cmd, context, ns, release)
                self._delete_download_job(cmd, context, ns)

        # WVA teardown: always remove per-stack VA+HPA. The shared controller
        # is uninstalled on full-scenario teardowns (no --stack filter); a
        # partial-stack teardown preserves it because the remaining stacks
        # still depend on it. --deep forces uninstall regardless.
        self._teardown_wva(cmd, context, errors)

        if errors:
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=False,
                message="Helm uninstall had errors",
                errors=errors,
            )

        return StepResult(
            step_number=self.number,
            step_name=self.name,
            success=True,
            message="Helm releases uninstalled",
        )

    def _delete_fma_crs(
        self,
        cmd: CommandExecutor,
        context: ExecutionContext,
        namespace: str,
    ) -> None:
        """Delete FMA objects so the controller can remove pod finalizers
        before the Helm chart uninstall takes it down.
        """
        # Delete requester to start the unbinding
        context.logger.log_info(
            f"  Deleting FMA requester ReplicaSet in {namespace} "
            "(before Helm uninstall)",
            emoji="🗑️",
        )
        cmd.kube(
            "delete", "replicaset",
            "--selector=stood-up-via=fma",
            "--namespace", namespace,
            "--ignore-not-found=true",
            check=False,
        )

        # Delete FMA CRs
        fma_cr_kinds = [
            "launcherpopulationpolicy",
            "inferenceserverconfig",
            "launcherconfig",
        ]
        for kind in fma_cr_kinds:
            result = cmd.kube(
                "get", kind, "--namespace", namespace,
                "-o", "name", "--ignore-not-found",
                check=False,
            )
            if not result.success or not result.stdout.strip():
                continue
            for cr in result.stdout.strip().splitlines():
                context.logger.log_info(
                    f"  Deleting FMA CR {cr} (before Helm uninstall)",
                    emoji="🗑️",
                )
                cmd.kube(
                    "delete", "--namespace", namespace,
                    "--ignore-not-found=true",
                    cr, check=False,
                )

        # Wait for all FMA pods to terminate while the controller is still running
        timeout = context.fma_teardown_timeout
        for selector in [
            "app.kubernetes.io/component=launcher",
            "llm-d.ai/role=requester",
        ]:
            wait_for_pods_deleted(cmd, selector, namespace, timeout, context)

    def _collect_model_labels(self, context: ExecutionContext) -> list[str]:
        """Collect model ID labels used to match helm releases."""
        labels: list[str] = []
        for stack_path in context.rendered_stacks or []:
            cfg = self._load_stack_config(stack_path)
            label = cfg.get("model_id_label", "")
            if label and label not in labels:
                labels.append(label)
        return labels

    def _uninstall_releases(
        self, cmd: CommandExecutor, context: ExecutionContext,
        namespace: str, release: str, model_labels: list[str], errors: list
    ):
        """Find and uninstall Helm releases matching the release name or model labels."""
        result = cmd.helm(
            "list", "--namespace", namespace, "--no-headers",
        )
        if not result.success:
            return

        for line in result.stdout.strip().splitlines():
            parts = line.split()
            if not parts:
                continue
            release_name = parts[0]
            if self._release_matches(release_name, release, model_labels):
                context.logger.log_info(
                    f"Uninstalling Helm release \"{release_name}\" "
                    f"from {namespace}"
                )
                uninstall = cmd.helm(
                    "uninstall", release_name, "--namespace", namespace,
                )
                if not uninstall.success:
                    errors.append(
                        f"Failed to uninstall {release_name}: "
                        f"{uninstall.stderr}"
                    )

    @staticmethod
    def _release_matches(
        release_name: str, release: str, model_labels: list[str]
    ) -> bool:
        """Check if a helm release belongs to this deployment."""
        if release and release in release_name:
            return True
        return any(label in release_name for label in model_labels)

    def _delete_openshift_routes(
        self, cmd: CommandExecutor, context: ExecutionContext,
        namespace: str, release: str
    ):
        """Delete OpenShift routes for the inference gateway."""
        if not context.is_openshift:
            return

        for route_name in [
            f"infra-{release}-inference-gateway",
            f"{release}-inference-gateway",
        ]:
            context.logger.log_info(
                f"Deleting OpenShift route \"{route_name}\" "
                f"from {namespace}"
            )
            result = cmd.kube(
                "delete", "--namespace", namespace,
                "--ignore-not-found=true",
                "route", route_name,
            )
            if result.success:
                context.logger.log_info(
                    f"  Deleted route/{route_name}", emoji="🗑️"
                )

    def _teardown_wva(
        self, cmd: CommandExecutor, context: ExecutionContext, errors: list
    ) -> None:
        """Tear down WVA resources.

        Always deletes the per-stack VariantAutoscaling + HPA so the model
        no longer auto-scales after teardown.

        The (per-namespace) WVA controller is uninstalled on full-scenario
        teardowns (no ``--stack`` filter), because there are no remaining
        stacks of this scenario to depend on it. A partial-stack teardown
        (``--stack X``) preserves the controller -- the sibling stacks of
        this scenario in the same namespace still need it. ``--deep``
        forces uninstall regardless of the filter.

        prometheus-adapter and its supporting cluster-wide resources
        (``allow-thanos-querier-api-access`` ClusterRole, ``prometheus-ca``
        ConfigMap) are NEVER touched by teardown — not even with --deep.
        They are shared cluster-wide infrastructure used by every WVA
        tenant's HPA pipeline. Their lifecycle is managed once at the
        cluster level (e.g. by a platform admin), and removing them on a
        per-tenant teardown would silently break every other namespace's
        autoscaling.

        Skipped entirely on non-OpenShift platforms — standup gates the
        WVA install on OpenShift (see step_03), so on other platforms
        there's nothing for teardown to remove.
        """
        if not context.is_openshift:
            context.logger.log_info(
                f"WVA teardown skipped: platform is {context.platform_type}, "
                "not OpenShift (matches standup behavior — WVA was never installed)."
            )
            return

        wva_stacks: list[tuple[str, str]] = []  # (wva_namespace, model_id_label)
        seen_ns: set[str] = set()

        for stack_path in context.rendered_stacks or []:
            cfg_file = stack_path / "config.yaml"
            if not cfg_file.exists():
                continue
            try:
                with open(cfg_file, encoding="utf-8") as fh:
                    cfg = yaml.safe_load(fh) or {}
            except (OSError, yaml.YAMLError):
                continue
            wva_cfg = cfg.get("wva", {}) or {}
            if not wva_cfg.get("enabled", False):
                continue
            wva_ns = (
                wva_cfg.get("namespace")
                or cfg.get("namespace", {}).get("name", "")
            )
            model_id_label = cfg.get("model_id_label", "")
            if wva_ns and model_id_label:
                wva_stacks.append((wva_ns, model_id_label))
                seen_ns.add(wva_ns)

        if not wva_stacks:
            return

        # 1. Per-stack VariantAutoscaling + HPA: always delete.
        for wva_ns, model_id_label in wva_stacks:
            resource_name = f"{model_id_label}-decode"
            for kind in ("hpa", "variantautoscaling.llmd.ai"):
                context.logger.log_info(
                    f"Deleting {kind}/{resource_name} from ns/{wva_ns}"
                )
                result = cmd.kube(
                    "delete", kind, resource_name,
                    "--namespace", wva_ns,
                    "--ignore-not-found=true",
                    check=False,
                )
                if result.success:
                    context.logger.log_info(
                        f"  Deleted {kind}/{resource_name}", emoji="🗑️"
                    )

        # 2. WVA controller(s): uninstalled on full-scenario teardowns and
        # forced on --deep; preserved on partial-stack teardowns so sibling
        # stacks of this scenario keep autoscaling.
        #
        # PRINCIPLE: teardown only removes resources that live in the target
        # namespace(s). It MUST NOT touch cross-namespace or cluster-scoped
        # resources -- doing so would silently break other tenants. That's
        # why we explicitly do NOT remove on any teardown:
        #   - prometheus-adapter (lives in openshift-user-workload-monitoring)
        #   - prometheus-ca ConfigMap (lives in openshift-user-workload-monitoring)
        #   - allow-thanos-querier-api-access ClusterRole (cluster-scoped)
        # All three are shared infrastructure for every WVA tenant's HPA
        # pipeline. Their lifecycle is managed once at the cluster level
        # (e.g. by a platform admin). Our standup install is idempotent
        # and skips when a cluster-wide install already exists, so leaving
        # them in place across teardowns is always correct.
        is_partial_stack_teardown = bool(context.stack_filter)
        if is_partial_stack_teardown and not context.deep_clean:
            context.logger.log_info(
                "Preserving WVA controller: --stack filter is active, "
                "sibling stacks in this scenario still depend on it. "
                "Pass -d/--deep to force uninstall."
            )
            return

        if context.deep_clean:
            mode_msg = "Deep clean: uninstalling WVA controller(s)."
        else:
            mode_msg = "Full-scenario teardown: uninstalling WVA controller(s)."
        context.logger.log_info(
            f"{mode_msg} prometheus-adapter and shared cluster RBAC are "
            "kept intact."
        )
        for wva_ns in sorted(seen_ns):
            context.logger.log_info(
                f"Uninstalling helm release "
                f"workload-variant-autoscaler from ns/{wva_ns}"
            )
            uninstall = cmd.helm(
                "uninstall", "workload-variant-autoscaler",
                "--namespace", wva_ns,
                "--ignore-not-found",
                check=False,
            )
            if not uninstall.success and "not found" not in (uninstall.stderr or "").lower():
                errors.append(
                    f"Failed to uninstall WVA controller in {wva_ns}: "
                    f"{uninstall.stderr}"
                )

    def _delete_download_job(
        self, cmd: CommandExecutor, context: ExecutionContext,
        namespace: str
    ):
        """Delete the model download job."""
        context.logger.log_info(
            f"Deleting download job in {namespace}"
        )
        result = cmd.kube(
            "delete", "--namespace", namespace,
            "--ignore-not-found=true",
            "job", "download-model",
        )
        if result.success:
            context.logger.log_info(
                "  Deleted job/download-model", emoji="🗑️"
            )
