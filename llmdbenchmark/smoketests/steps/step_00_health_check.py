"""Smoketest step 00 -- Health check: pods running, /health, /v1/models, endpoints."""

from pathlib import Path

from llmdbenchmark.executor.step import Step, StepResult, Phase
from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.smoketests import get_validator


class HealthCheckStep(Step):
    """Validate deployment health and model serving."""

    def __init__(self):
        super().__init__(
            number=0,
            name="health_check",
            description="Validate deployment health and model serving",
            phase=Phase.SMOKETEST,
            per_stack=True,
        )

    def execute(
        self, context: ExecutionContext, stack_path: Path | None = None
    ) -> StepResult:
        """Run health checks for the given stack and return pass/fail."""
        prologue = self.start(context, stack_path, load_config=False, require_cmd=False)
        if isinstance(prologue, StepResult):
            return prologue
        stack_name = prologue.stack_name

        validator = get_validator(stack_name)
        report = validator.run_health_checks(context, stack_path)

        if report.passed:
            return self.success_result(
                f"Health checks passed for {stack_name} ({report.summary()})",
                stack_name=stack_name,
            )

        for err in report.errors():
            context.logger.log_error(f"Health check: {err}")

        return self.failure_result(
            f"Health checks failed for {stack_name}",
            report.errors(),
            stack_name=stack_name,
            log_errors=False,
        )
