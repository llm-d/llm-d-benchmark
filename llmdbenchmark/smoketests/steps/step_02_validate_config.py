"""Smoketest step 02 — Validate deployed pod config matches scenario expectations."""

from pathlib import Path

from llmdbenchmark.executor.step import Step, StepResult, Phase
from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.smoketests import get_validator


class ValidateConfigStep(Step):
    """Validate that deployed pod configuration matches the scenario."""

    def __init__(self):
        super().__init__(
            number=2,
            name="validate_config",
            description="Validate deployed configuration matches scenario",
            phase=Phase.SMOKETEST,
            per_stack=True,
        )

    def execute(
        self, context: ExecutionContext, stack_path: Path | None = None
    ) -> StepResult:
        """Run the scenario-specific config validator and log grouped results.

        Skips validation for stacks without a dedicated validator subclass.
        """
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

        # Only well-lit-path scenarios have dedicated validators
        from llmdbenchmark.smoketests.base import BaseSmoketest
        if type(validator) is BaseSmoketest:
            context.logger.log_info(
                f"    Skipping config validation — no dedicated validator for '{stack_name}'"
            )
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=True,
                message=f"Skipped config validation for {stack_name} (no dedicated validator)",
                stack_name=stack_name,
            )

        report = validator.run_config_validation(context, stack_path)

        # Log checks with grouped indentation under pod headers
        for check in report.checks:
            if check.is_header:
                # Header line — no indent, acts as group separator
                context.logger.log_info(f"    {check}")
            elif check.group:
                # Grouped check — extra indent under its header
                if check.passed:
                    context.logger.log_info(f"        {check}")
                else:
                    context.logger.log_error(f"        {check}")
            else:
                # Ungrouped check (e.g. replica count, scenario-specific)
                if check.passed:
                    context.logger.log_info(f"    {check}")
                else:
                    context.logger.log_error(f"    {check}")

        if report.passed:
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=True,
                message=f"Config validation passed for {stack_name} ({report.summary()})",
                stack_name=stack_name,
            )

        return StepResult(
            step_number=self.number,
            step_name=self.name,
            success=False,
            message=f"Config validation failed for {stack_name} ({report.summary()})",
            errors=report.errors(),
            stack_name=stack_name,
        )
