"""Teardown Step 01 -- Uninstall Helm releases, OpenShift routes, and download jobs."""

from pathlib import Path

from llmdbenchmark.executor.step import Step, StepResult, Phase
from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.executor.command import CommandExecutor


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
        return "modelservice" not in context.deployed_methods

    def execute(
        self, context: ExecutionContext, stack_path: Path | None = None
    ) -> StepResult:
        errors = []
        cmd = context.require_cmd()

        release = context.release
        namespaces = self._all_target_namespaces(context)

        model_labels = self._collect_model_labels(context)

        for ns in namespaces:
            self._uninstall_releases(cmd, context, ns, release, model_labels, errors)
            self._delete_openshift_routes(cmd, context, ns, release)
            self._delete_download_job(cmd, context, ns)

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

    def _collect_model_labels(self, context: ExecutionContext) -> list[str]:
        """Collect model short names used to match helm releases by model ID label."""
        labels: list[str] = []
        for stack_path in context.rendered_stacks or []:
            cfg = self._load_stack_config(stack_path)
            short = cfg.get("model", {}).get("shortName", "")
            if short and short not in labels:
                labels.append(short)
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
