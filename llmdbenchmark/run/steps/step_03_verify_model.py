"""Step 03 -- Verify the expected model is served at the detected endpoint."""

from pathlib import Path

from llmdbenchmark.executor.step import Step, StepResult, Phase
from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.utilities.endpoint import test_model_serving


class VerifyModelStep(Step):
    """Verify the expected model is served at the detected endpoint."""

    def __init__(self):
        super().__init__(
            number=3,
            name="verify_model",
            description="Verify model is served at endpoint",
            phase=Phase.RUN,
            per_stack=True,
        )

    def should_skip(self, context: ExecutionContext) -> bool:
        """Skip model verification in skip-run mode."""
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

        # Determine model name
        plan_config = self._load_stack_config(stack_path)
        model_name = self._resolve(
            plan_config, "model.name", context_value=context.model_name,
        )
        if not model_name:
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=False,
                message="No model name configured",
                errors=[
                    "Set 'model.name' in your scenario, or pass --model on the CLI."
                ],
                stack_name=stack_name,
            )

        # Get endpoint from previous step
        endpoint_url = context.deployed_endpoints.get(stack_name)
        if not endpoint_url:
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=False,
                message="No endpoint URL available",
                errors=[
                    "Endpoint detection (step 02) must run first."
                ],
                stack_name=stack_name,
            )

        if context.dry_run:
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=True,
                message=(
                    f"[DRY RUN] Would verify model '{model_name}' "
                    f"at {endpoint_url}"
                ),
                stack_name=stack_name,
            )

        # Parse host and port from endpoint URL
        host, port = self._parse_endpoint(endpoint_url)
        namespace = context.harness_namespace or context.namespace or ""

        context.logger.log_info(
            f"Verifying model '{model_name}' at {endpoint_url}..."
        )

        error = test_model_serving(
            cmd,
            namespace,
            host,
            port,
            model_name,
            plan_config,
            max_retries=3,
            retry_interval=10,
        )

        if error:
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=False,
                message=f"Model verification failed: {error}",
                errors=[error],
                stack_name=stack_name,
            )

        context.logger.log_info(
            f"Model '{model_name}' verified at {endpoint_url}"
        )
        return StepResult(
            step_number=self.number,
            step_name=self.name,
            success=True,
            message=f"Model '{model_name}' verified at {endpoint_url}",
            stack_name=stack_name,
        )

    @staticmethod
    def _parse_endpoint(url: str) -> tuple[str, str]:
        """Extract host and port from an endpoint URL.

        Examples:
            http://10.0.0.1:80 → ('10.0.0.1', '80')
            https://gateway.example.com:443 → ('gateway.example.com', '443')
        """
        # Strip protocol
        stripped = url
        if "://" in stripped:
            stripped = stripped.split("://", 1)[1]
        # Strip trailing path
        stripped = stripped.split("/", 1)[0]
        # Split host:port
        if ":" in stripped:
            host, port = stripped.rsplit(":", 1)
            return host, port
        # Default port based on protocol
        if url.startswith("https"):
            return stripped, "443"
        return stripped, "80"
