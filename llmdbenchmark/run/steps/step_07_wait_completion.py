"""Step 07 -- Wait for harness pod(s) to complete."""

from pathlib import Path

from llmdbenchmark.executor.step import Step, StepResult, Phase
from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.utilities.kube_helpers import wait_for_pod


class WaitCompletionStep(Step):
    """Wait for all harness pods to reach Succeeded or Failed phase."""

    def __init__(self):
        super().__init__(
            number=7,
            name="wait_completion",
            description="Wait for harness pod(s) to complete",
            phase=Phase.RUN,
            per_stack=True,
        )

    def should_skip(self, context: ExecutionContext) -> bool:
        """Skip waiting in skip-run mode."""
        return context.harness_skip_run

    def execute(
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

        stack_name = stack_path.name
        cmd = context.require_cmd()

        pod_names = context.deployed_pod_names
        if not pod_names:
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=True,
                message="No harness pods to wait for",
                stack_name=stack_name,
            )

        timeout = context.harness_wait_timeout

        # No-wait mode
        if timeout == 0:
            context.logger.log_info(
                "Wait timeout is 0 -- returning immediately"
            )
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=True,
                message="No-wait mode (timeout=0)",
                stack_name=stack_name,
            )

        # Debug mode -- pods have sleep infinity
        if context.harness_debug:
            context.logger.log_info(
                f"Debug mode: {len(pod_names)} pod(s) running with "
                f"'sleep infinity'. Use kubectl exec to interact."
            )
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=True,
                message="Debug mode -- pods running with sleep infinity",
                stack_name=stack_name,
            )

        harness_ns = context.harness_namespace or context.namespace or ""
        errors: list[str] = []
        succeeded = 0
        failed = 0

        context.logger.log_info(
            f"Waiting for {len(pod_names)} harness pod(s) to complete "
            f"(timeout={timeout}s)..."
        )

        # Wait for each pod
        for pod_name in pod_names:
            result = wait_for_pod(
                cmd, pod_name, harness_ns, timeout, context
            )
            if result == "Succeeded":
                succeeded += 1
            elif result == "Failed":
                failed += 1
                errors.append(f"Pod '{pod_name}' failed")
            else:
                errors.append(f"Pod '{pod_name}': {result}")

        total = len(pod_names)
        summary = (
            f"{succeeded}/{total} succeeded, {failed}/{total} failed"
        )

        if errors:
            context.logger.log_warning(
                f"Some harness pods had issues: {summary}"
            )
            # Non-fatal -- partial results may still be available
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=failed == 0,
                message=f"Harness completion: {summary}",
                errors=errors,
                stack_name=stack_name,
            )

        context.logger.log_info(
            f"All harness pods completed successfully ({summary})"
        )
        return StepResult(
            step_number=self.number,
            step_name=self.name,
            success=True,
            message=f"All {total} harness pod(s) completed",
            stack_name=stack_name,
        )

    # _wait_for_pod is now provided by kube_helpers.wait_for_pod
