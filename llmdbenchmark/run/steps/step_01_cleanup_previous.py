"""Step 01 -- Clean up leftover harness pods from a previous run."""

from pathlib import Path

from llmdbenchmark.executor.step import Step, StepResult, Phase
from llmdbenchmark.executor.context import ExecutionContext


class RunCleanupPreviousStep(Step):
    """Delete leftover harness pods from a previous run."""

    def __init__(self):
        super().__init__(
            number=1,
            name="run_cleanup_previous",
            description="Clean up leftover harness pods from previous run",
            phase=Phase.RUN,
            per_stack=False,
        )

    def should_skip(self, context: ExecutionContext) -> bool:
        """Skip cleanup in skip-run mode (collecting results only)."""
        return context.harness_skip_run

    def execute(
        self, context: ExecutionContext, stack_path: Path | None = None
    ) -> StepResult:
        # load_config=False because this step tolerates a missing plan_config
        # (uses a default pod label via _resolve() when not configured).
        prologue = self.start(context, stack_path, load_config=False)
        if isinstance(prologue, StepResult):
            return prologue
        cmd = prologue.cmd

        harness_ns = context.harness_namespace or context.namespace
        if not harness_ns:
            return self.success_result(
                "No namespace configured -- skipping cleanup",
            )

        plan_config = self._load_plan_config(context)
        pod_label = self._resolve(
            plan_config, "harness.podLabel", default="llmdbench-harness-launcher",
        )

        context.logger.log_info(
            f"Cleaning up previous harness pods (label={pod_label}, ns={harness_ns})..."
        )

        result = cmd.kube(
            "delete", "pod",
            "-l", f"app={pod_label},function=load_generator",
            "--namespace", harness_ns,
            "--ignore-not-found",
            check=False,
        )

        if not result.success:
            context.logger.log_warning(
                f"Cleanup of previous pods failed (non-fatal): {result.stderr}"
            )

        return self.success_result(
            f"Previous harness pods cleaned up (ns={harness_ns})",
        )
