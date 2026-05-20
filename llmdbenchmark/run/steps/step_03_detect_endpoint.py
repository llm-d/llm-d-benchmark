"""Step 02 -- Detect the model serving endpoint for each stack."""

from pathlib import Path

from llmdbenchmark.executor.step import Step, StepResult, Phase
from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.utilities.endpoint import (
    find_standalone_endpoint,
    find_fma_endpoint,
    find_gateway_endpoint,
    find_custom_endpoint,
    find_kustomize_endpoint,
    discover_hf_token_secret,
    extract_hf_token_from_secret,
    compute_gateway_path_prefix,
)


class DetectEndpointStep(Step):
    """Detect the model serving endpoint for each rendered stack."""

    def __init__(self):
        super().__init__(
            number=3,
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

        if context.endpoint_url:
            url = context.endpoint_url
            context.deployed_endpoints[stack_name] = url
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

        plan_config = self._load_stack_config(stack_path)
        namespace = context.namespace or self._require_config(
            plan_config, "namespace", "name"
        )
        is_standalone = (
            "standalone" in context.deployed_methods
            or self._resolve(plan_config, "standalone.enabled", default=False)
        )
        is_fma = (
            "fma" in context.deployed_methods
            or self._resolve(plan_config, "fma.enabled", default=False)
        )
        inference_port = self._resolve(
            plan_config, "vllmCommon.inferencePort", default=8000,
        )
        release = self._resolve(
            plan_config, "release", context_value=context.release,
        )

        service_ip = None
        service_name = None
        gateway_port = "80"
        stack_type = "vllm-prod"

        deploy_method = None
        is_kustomize = "kustomize" in context.deployed_methods
        methods_arg = getattr(context, "deployed_methods", [])
        if methods_arg:
            for m in methods_arg:
                if m not in ("standalone", "modelservice", "fma"):
                    deploy_method = m
                    break

        if is_standalone:
            service_ip, service_name, gateway_port = find_standalone_endpoint(
                cmd, namespace, inference_port
            )
            stack_type = "vllm-prod"
        elif is_fma:
            service_ip = find_fma_endpoint(cmd, namespace)
            service_name = service_ip
            gateway_port = 0
        elif is_kustomize:
            guide_name = self._resolve(
                plan_config, "kustomize.guideName", default=""
            )
            context.logger.log_info(
                f"Kustomize deployment -- looking for service '{guide_name}-epp'",
            )
            if guide_name:
                service_ip, service_name, gateway_port = find_kustomize_endpoint(
                    cmd, namespace, guide_name,
                )
            else:
                service_ip, service_name, gateway_port = find_custom_endpoint(
                    cmd, namespace, "epp",
                )
            stack_type = "vllm-prod"
        elif deploy_method:
            context.logger.log_info(
                f"Method '{deploy_method}' is neither standalone nor "
                f"modelservice -- trying custom endpoint discovery...",
                emoji="🔍",
            )
            service_ip, service_name, gateway_port = find_custom_endpoint(
                cmd, namespace, deploy_method,
            )
            stack_type = "vllm-prod"
        else:
            service_ip, service_name, gateway_port = find_gateway_endpoint(
                cmd, namespace, release
            )
            stack_type = "llm-d"

        if not service_ip:
            if context.dry_run:
                service_ip = "<dry-run-endpoint>"
                service_name = "<dry-run>"
                gateway_port = "80"
            else:
                return StepResult(
                    step_number=self.number,
                    step_name=self.name,
                    success=False,
                    message=f"Could not detect endpoint for {stack_name}",
                    errors=[
                        f"No service/gateway IP found in namespace '{namespace}'. "
                        f"Is the model deployed? (standalone={is_standalone}). "
                        f"Tip: If the stack was not deployed via standup, use "
                        f"--methods <string-matching-service-or-pod-name> or "
                        f"--endpoint-url <URL>."
                    ],
                    stack_name=stack_name,
                )

        protocol = "https" if gateway_port == "443" else "http"
        endpoint_url = f"{protocol}://{service_ip}:{gateway_port}"
        path_prefix = compute_gateway_path_prefix(
            plan_config, stack_name, is_standalone=is_standalone,
        )
        if path_prefix:
            endpoint_url = f"{endpoint_url}{path_prefix}"
        context.deployed_endpoints[stack_name] = endpoint_url

        context.logger.log_info(
            f"Detected endpoint: {endpoint_url} "
            f"(service={service_name}, stack_type={stack_type}"
            f"{', path_prefix=' + path_prefix if path_prefix else ''})"
        )

        if deploy_method and not context.dry_run:
            self._discover_hf_token(cmd, namespace, context)

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
        if "standalone" in context.deployed_methods:
            return "vllm-prod"
        if "fma" in context.deployed_methods:
            return "vllm-prod"
        if "modelservice" in context.deployed_methods:
            return "llm-d"
        return "vllm-prod"

    @staticmethod
    def _discover_hf_token(cmd, namespace: str, context: ExecutionContext) -> None:
        import os

        if os.environ.get("HF_TOKEN") or os.environ.get("LLMDBENCH_HF_TOKEN"):
            return

        context.logger.log_info(
            "Trying to find a matching HuggingFace token "
            "secret in the cluster...",
            emoji="🔍",
        )

        secret_name = discover_hf_token_secret(cmd, namespace)
        if not secret_name:
            context.logger.log_warning(
                "Could not find a HuggingFace token secret "
                f"(pattern 'llm-d-hf*token*') in namespace '{namespace}'. "
                "If the model is gated, set HF_TOKEN in your environment."
            )
            return

        context.logger.log_info(
            f"HuggingFace token secret detected: '{secret_name}'"
        )

        token = extract_hf_token_from_secret(cmd, namespace, secret_name)
        if token:
            os.environ["HF_TOKEN"] = token
            context.logger.log_info(
                "HuggingFace token extracted from cluster secret "
                "and set in environment",
                emoji="✅",
            )
        else:
            context.logger.log_warning(
                f"Found secret '{secret_name}' but could not extract "
                "a valid HF token (expected token starting with 'hf_')."
            )
