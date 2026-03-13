"""Step 10 -- Post-run cleanup of harness pods and ConfigMaps."""

from pathlib import Path

from llmdbenchmark.executor.step import Step, StepResult, Phase
from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.run.steps.step_05_create_profile_configmap import (
    HARNESS_SCRIPTS_CONFIGMAP,
)


class RunCleanupPostStep(Step):
    """Delete harness pods and ConfigMaps after results are collected."""

    def __init__(self):
        super().__init__(
            number=10,
            name="run_cleanup_post",
            description="Clean up harness pods and ConfigMaps",
            phase=Phase.RUN,
            per_stack=False,
        )

    def should_skip(self, context: ExecutionContext) -> bool:
        """Skip cleanup in debug mode (keep pods alive for inspection)."""
        return context.harness_debug

    def execute(
        self, context: ExecutionContext, stack_path: Path | None = None
    ) -> StepResult:
        cmd = context.require_cmd()

        harness_ns = context.harness_namespace or context.namespace
        if not harness_ns:
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=True,
                message="No namespace configured — skipping cleanup",
            )

        plan_config = self._load_plan_config(context)
        pod_label = "llmdbench-harness-launcher"
        harness_name = "inference-perf"
        if plan_config:
            pod_label = plan_config.get("harness", {}).get("podLabel", pod_label)
            harness_name = plan_config.get("harness", {}).get("name", harness_name)
        if context.harness_name:
            harness_name = context.harness_name

        if context.dry_run:
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=True,
                message=(
                    f"[DRY RUN] Would clean up harness pods and "
                    f"ConfigMaps ('{harness_name}-profiles', "
                    f"'{HARNESS_SCRIPTS_CONFIGMAP}') in ns={harness_ns}"
                ),
            )

        context.logger.log_info(
            f"Cleaning up harness resources in ns={harness_ns}..."
        )

        # Delete harness pods
        result = cmd.kube(
            "delete", "pod",
            "-l", f"app={pod_label}",
            "--namespace", harness_ns,
            "--ignore-not-found",
            check=False,
        )
        if result.success:
            context.logger.log_info("Harness pods deleted")
        else:
            context.logger.log_warning(
                f"Pod cleanup warning: {result.stderr}"
            )

        # Delete profile ConfigMap
        profiles_cm = f"{harness_name}-profiles"
        self._delete_configmap(cmd, profiles_cm, harness_ns, context)

        # Delete harness scripts ConfigMap
        self._delete_configmap(
            cmd, HARNESS_SCRIPTS_CONFIGMAP, harness_ns, context,
        )

        return StepResult(
            step_number=self.number,
            step_name=self.name,
            success=True,
            message=f"Post-run cleanup completed (ns={harness_ns})",
        )

    @staticmethod
    def _delete_configmap(cmd, name: str, namespace: str, context) -> None:
        """Delete a ConfigMap, logging success or warning."""
        result = cmd.kube(
            "delete", "configmap", name,
            "--namespace", namespace,
            "--ignore-not-found",
            check=False,
        )
        if result.success:
            context.logger.log_info(f"ConfigMap '{name}' deleted")
        else:
            context.logger.log_warning(
                f"ConfigMap cleanup warning ({name}): {result.stderr}"
            )
