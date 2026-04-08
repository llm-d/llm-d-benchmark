"""Smoketest step 01 -- Sample inference request against deployed model."""

from pathlib import Path

from llmdbenchmark.executor.step import Step, StepResult, Phase
from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.smoketests import get_validator
from llmdbenchmark.utilities.endpoint import cleanup_ephemeral_pods


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
        # load_config=False/require_cmd=False: this step delegates to the
        # validator and only acquires cmd below when cleaning up pods.
        prologue = self.start(
            context, stack_path, load_config=False, require_cmd=False,
        )
        if isinstance(prologue, StepResult):
            return prologue
        stack_name = prologue.stack_name

        validator = get_validator(stack_name)
        report = validator.run_inference_test(context, stack_path)

        # Clean up ephemeral curl pods left behind by health + inference checks
        if not context.dry_run:
            namespace = context.harness_namespace or context.namespace
            if namespace:
                cmd = context.require_cmd()
                cleanup_ephemeral_pods(cmd, namespace, context.logger)

        if report.passed:
            return self.success_result(
                f"Inference test passed for {stack_name}",
                stack_name=stack_name,
            )

        for err in report.errors():
            context.logger.log_error(f"Inference test: {err}")

        return self.failure_result(
            f"Inference test failed for {stack_name}",
            report.errors(),
            stack_name=stack_name,
            log_errors=False,
        )
