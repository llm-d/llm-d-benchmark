"""Step 03 -- Verify the expected model is served at the detected endpoint."""

from pathlib import Path

from llmdbenchmark.executor.step import Step, StepResult, Phase
from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.utilities.endpoint import test_model_serving, cleanup_ephemeral_pods


class VerifyModelStep(Step):
    """Verify the expected model is served at the detected endpoint."""

    def __init__(self):
        super().__init__(
            number=4,
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
        prologue = self.start(context, stack_path)
        if isinstance(prologue, StepResult):
            return prologue
        cmd = prologue.cmd
        plan_config = prologue.plan_config
        stack_name = prologue.stack_name

        # Determine model name
        model_name = self._resolve(
            plan_config, "model.name", context_value=context.model_name,
        )
        if not model_name:
            return self.failure_result(
                "No model name configured",
                ["Set 'model.name' in your scenario, or pass --model on the CLI."],
                stack_name=stack_name,
                log_errors=False,
            )

        # Get endpoint from previous step
        endpoint_url = context.deployed_endpoints.get(stack_name)
        if not endpoint_url:
            return self.failure_result(
                "No endpoint URL available",
                ["Endpoint detection (step 02) must run first."],
                stack_name=stack_name,
                log_errors=False,
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
            service_account=context.harness_service_account,
        )

        # Clean up ephemeral smoketest/curl pods
        if not context.dry_run:
            cleanup_ephemeral_pods(cmd, namespace, context.logger)

        if error:
            return self.failure_result(
                f"Model verification failed: {error}",
                [error],
                stack_name=stack_name,
                log_errors=False,
            )

        context.logger.log_info(
            f"Model '{model_name}' verified at {endpoint_url}"
        )
        return self.success_result(
            f"Model '{model_name}' verified at {endpoint_url}",
            stack_name=stack_name,
        )

    @staticmethod
    def _parse_endpoint(url: str) -> tuple[str, str]:
        """Extract host and port from an endpoint URL.

        Examples:
            http://10.0.0.1:80 to ('10.0.0.1', '80')
            https://gateway.example.com:443 to ('gateway.example.com', '443')
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
