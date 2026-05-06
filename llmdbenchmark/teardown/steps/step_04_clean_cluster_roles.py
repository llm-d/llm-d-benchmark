"""Teardown Step 04 -- Remove cluster-scoped ClusterRoles and ClusterRoleBindings."""

from pathlib import Path

from llmdbenchmark.executor.step import Step, StepResult, Phase
from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.executor.command import CommandExecutor

_MODELSERVICE_ROLE_SUFFIXES = [
    "modelservice-endpoint-picker",
    "modelservice-epp-metrics-scrape",
    "modelservice-manager",
    "modelservice-metrics-auth",
    "modelservice-admin",
    "modelservice-editor",
    "modelservice-viewer",
]


class CleanClusterRolesStep(Step):
    """Remove cluster-scoped ClusterRoles and ClusterRoleBindings."""

    def __init__(self):
        super().__init__(
            number=4,
            name="clean_cluster_roles",
            description="Remove cluster-scoped roles and bindings (admin only)",
            phase=Phase.TEARDOWN,
            per_stack=False,
        )

    def should_skip(self, context: ExecutionContext) -> bool:
        if context.non_admin:
            return True
        if "modelservice" not in context.deployed_methods:
            return True
        return False

    def execute(
        self, context: ExecutionContext, stack_path: Path | None = None
    ) -> StepResult:
        errors = []
        cmd = context.require_cmd()

        release = context.release

        self._delete_matching(cmd, context, "ClusterRoleBinding", release, errors)
        self._delete_matching(cmd, context, "ClusterRole", release, errors)

        context.logger.log_info("Deleting well-known modelservice ClusterRoles...")
        for suffix in _MODELSERVICE_ROLE_SUFFIXES:
            cr_name = f"{release}-{suffix}"
            context.logger.log_info(
                f"  Deleting ClusterRole/{cr_name}", emoji="🗑️"
            )
            cmd.kube(
                "delete", "--ignore-not-found=true",
                "ClusterRole", cr_name,
            )

        if context.is_openshift:
            namespaces = self._all_target_namespaces(context)
            for ns in namespaces:
                context.logger.log_info(
                    f"Deleting HTTPRoutes in {ns}..."
                )
                result = cmd.kube(
                    "get", "httproute",
                    "--namespace", ns,
                    "-o", "name",
                    "--ignore-not-found",
                )
                if result.success and result.stdout.strip():
                    for route in result.stdout.strip().splitlines():
                        context.logger.log_info(
                            f"  Deleting {route}", emoji="🗑️"
                        )
                        cmd.kube(
                            "delete", "--namespace", ns,
                            "--ignore-not-found=true",
                            route,
                        )
                else:
                    context.logger.log_info(
                        f"  No HTTPRoutes found in {ns}"
                    )

        if errors:
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=False,
                message="Cluster role cleanup had errors",
                errors=errors,
            )

        return StepResult(
            step_number=self.number,
            step_name=self.name,
            success=True,
            message="Cluster-scoped resources cleaned",
        )

    def _delete_matching(
        self, cmd: CommandExecutor, context: ExecutionContext,
        kind: str, release: str, errors: list
    ):
        """Delete cluster-scoped resources of a given kind matching the release name."""
        result = cmd.kube(
            "get", kind, "--no-headers", "-o", "name",
        )
        if not result.success or not result.stdout.strip():
            return

        for line in result.stdout.strip().splitlines():
            resource_name = line.strip()
            if release in resource_name:
                context.logger.log_info(
                    f"Deleting {kind}: {resource_name}"
                )
                del_result = cmd.kube(
                    "delete", "--ignore-not-found=true", resource_name,
                )
                if not del_result.success:
                    stderr_lower = del_result.stderr.lower()
                    if "not found" in stderr_lower or "no matches" in stderr_lower:
                        context.logger.log_info(
                            f"Already removed: {resource_name}"
                        )
                    else:
                        errors.append(
                            f"Failed to delete {resource_name}: "
                            f"{del_result.stderr}"
                        )
