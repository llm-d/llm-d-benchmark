"""Step 02 -- Detect the model serving endpoint for each stack."""

from pathlib import Path

from llmdbenchmark.executor.step import Step, StepResult, Phase
from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.utilities.endpoint import (
    find_standalone_endpoint,
    find_gateway_endpoint,
)


class DetectEndpointStep(Step):
    """Detect the model serving endpoint for each rendered stack."""

    def __init__(self):
        super().__init__(
            number=2,
            name="detect_endpoint",
            description="Detect model serving endpoint",
            phase=Phase.RUN,
            per_stack=True,
        )

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

        # Run-only mode: use the explicit endpoint URL
        if context.endpoint_url:
            url = context.endpoint_url
            context.deployed_endpoints[stack_name] = url
            # Determine stack type from deploy method
            stack_type = self._detect_stack_type(context)
            context.logger.log_info(
                f"Using explicit endpoint URL: {url} (stack_type={stack_type})"
            )
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=True,
                message=f"Endpoint set from CLI: {url}",
                stack_name=stack_name,
                context={"endpoint_url": url, "stack_type": stack_type},
            )

        # Auto-detect from cluster
        plan_config = self._load_stack_config(stack_path)
        namespace = context.namespace or self._require_config(
            plan_config, "namespace", "name"
        )
        is_standalone = (
            "standalone" in context.deployed_methods
            or plan_config.get("standalone", {}).get("enabled", False)
        )
        inference_port = plan_config.get("vllmCommon", {}).get("inferencePort", 8000)
        release = plan_config.get("release", context.release)

        if context.dry_run:
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=True,
                message=f"[DRY RUN] Would detect endpoint for {stack_name}",
                stack_name=stack_name,
            )

        service_ip = None
        service_name = None
        gateway_port = "80"
        stack_type = "vllm-prod"

        if is_standalone:
            service_ip, service_name, gateway_port = find_standalone_endpoint(
                cmd, namespace, inference_port
            )
            stack_type = "vllm-prod"
        else:
            service_ip, service_name, gateway_port = find_gateway_endpoint(
                cmd, namespace, release
            )
            stack_type = "llm-d"

        if not service_ip:
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=False,
                message=f"Could not detect endpoint for {stack_name}",
                errors=[
                    f"No service/gateway IP found in namespace '{namespace}'. "
                    f"Is the model deployed? (standalone={is_standalone})"
                ],
                stack_name=stack_name,
            )

        # Build full URL
        protocol = "https" if gateway_port == "443" else "http"
        endpoint_url = f"{protocol}://{service_ip}:{gateway_port}"
        context.deployed_endpoints[stack_name] = endpoint_url

        context.logger.log_info(
            f"Detected endpoint: {endpoint_url} "
            f"(service={service_name}, stack_type={stack_type})"
        )

        return StepResult(
            step_number=self.number,
            step_name=self.name,
            success=True,
            message=f"Endpoint detected: {endpoint_url}",
            stack_name=stack_name,
            context={"endpoint_url": endpoint_url, "stack_type": stack_type},
        )

    @staticmethod
    def _detect_stack_type(context: ExecutionContext) -> str:
        """Determine the stack type from deployed methods."""
        if "standalone" in context.deployed_methods:
            return "vllm-prod"
        if "modelservice" in context.deployed_methods:
            return "llm-d"
        # Default for run-only mode
        return "vllm-prod"
