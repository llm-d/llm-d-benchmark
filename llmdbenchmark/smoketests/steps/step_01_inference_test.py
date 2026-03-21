"""Smoketest step 01 — Sample inference request against deployed model."""

from pathlib import Path

from llmdbenchmark.executor.step import Step, StepResult, Phase
from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.smoketests import get_validator


class InferenceTestStep(Step):
    """Run a sample inference request to verify end-to-end model serving."""

    def __init__(self):
        super().__init__(
            number=1,
            name="inference_test",
            description="Run sample inference request against deployed model",
            phase=Phase.SMOKETEST,
            per_stack=True,
        )

    def execute(
        self, context: ExecutionContext, stack_path: Path | None = None
    ) -> StepResult:
        """Send a sample inference request and verify the model responds."""
        if stack_path is None:
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=False,
                message="No stack path provided for per-stack step",
                errors=["stack_path is required"],
            )

        stack_name = stack_path.name
        validator = get_validator(stack_name)
        report = validator.run_inference_test(context, stack_path)

        if report.passed:
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=True,
                message=f"Inference test passed for {stack_name}",
                stack_name=stack_name,
            )

        for err in report.errors():
            context.logger.log_error(f"Inference test: {err}")

        return StepResult(
            step_number=self.number,
            step_name=self.name,
            success=False,
            message=f"Inference test failed for {stack_name}",
            errors=report.errors(),
            stack_name=stack_name,
        )
