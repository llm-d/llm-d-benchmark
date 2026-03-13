"""Step 07 -- Wait for harness pod(s) to complete."""

import json
import time
from pathlib import Path

from llmdbenchmark.executor.step import Step, StepResult, Phase
from llmdbenchmark.executor.context import ExecutionContext


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
                "Wait timeout is 0 — returning immediately"
            )
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=True,
                message="No-wait mode (timeout=0)",
                stack_name=stack_name,
            )

        # Debug mode — pods have sleep infinity
        if context.harness_debug:
            context.logger.log_info(
                f"Debug mode: {len(pod_names)} pod(s) running with "
                f"'sleep infinity'. Use kubectl exec to interact."
            )
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=True,
                message="Debug mode — pods running with sleep infinity",
                stack_name=stack_name,
            )

        if context.dry_run:
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=True,
                message=(
                    f"[DRY RUN] Would wait for {len(pod_names)} "
                    f"pod(s) (timeout={timeout}s)"
                ),
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
            result = self._wait_for_pod(
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
            # Non-fatal — partial results may still be available
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

    def _wait_for_pod(
        self,
        cmd,
        pod_name: str,
        namespace: str,
        timeout: int,
        context: ExecutionContext,
        poll_interval: int = 15,
    ) -> str:
        """Wait for a single pod to reach a terminal phase.

        Returns:
            'Succeeded', 'Failed', or an error string.
        """
        crash_states = {
            "CrashLoopBackOff", "Error", "OOMKilled",
            "CreateContainerConfigError", "ImagePullBackOff",
            "ErrImagePull", "InvalidImageName",
        }

        start = time.time()

        while True:
            elapsed = time.time() - start
            if elapsed > timeout:
                return f"Timed out after {timeout}s"

            result = cmd.kube(
                "get", "pod", pod_name,
                "--namespace", namespace,
                "-o", "jsonpath={.status.phase}:{.status.containerStatuses[0].state}",
                check=False,
            )

            if not result.success:
                # Pod may not exist yet
                time.sleep(poll_interval)
                continue

            output = result.stdout.strip()
            parts = output.split(":", 1)
            phase = parts[0] if parts else ""

            if phase == "Succeeded":
                context.logger.log_info(
                    f"Pod '{pod_name}' completed successfully "
                    f"({int(elapsed)}s)"
                )
                return "Succeeded"

            if phase == "Failed":
                # Get exit code for debugging
                exit_result = cmd.kube(
                    "get", "pod", pod_name,
                    "--namespace", namespace,
                    "-o", "jsonpath={.status.containerStatuses[0].state.terminated.exitCode}",
                    check=False,
                )
                exit_code = exit_result.stdout.strip() if exit_result.success else "?"
                context.logger.log_error(
                    f"Pod '{pod_name}' failed (exit_code={exit_code}, "
                    f"{int(elapsed)}s)"
                )
                return "Failed"

            # Check for crash states via container status
            container_result = cmd.kube(
                "get", "pod", pod_name,
                "--namespace", namespace,
                "-o", "jsonpath={.status.containerStatuses[0].state.waiting.reason}",
                check=False,
            )
            if container_result.success and container_result.stdout.strip():
                reason = container_result.stdout.strip()
                if reason in crash_states:
                    context.logger.log_error(
                        f"Pod '{pod_name}' in terminal state: {reason}"
                    )
                    return f"Terminal state: {reason}"

            remaining = int(timeout - elapsed)
            context.logger.log_info(
                f"Pod '{pod_name}': {phase} ({int(elapsed)}s elapsed, "
                f"{remaining}s remaining)"
            )
            time.sleep(poll_interval)
