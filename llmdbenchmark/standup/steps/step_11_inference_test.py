"""Step 11 -- Sample inference test against the deployed endpoint.

Sends a real inference request via an ephemeral curl pod to verify that
the model can actually generate tokens, not just respond to health and
metadata probes.

Tries ``/v1/completions`` first (universal in vLLM for both base and
chat models).  If the endpoint returns a non-transient error (e.g. 4xx),
falls back to ``/v1/chat/completions`` for backends that only expose the
chat API.

On success, prints the working curl command so the user can reproduce
or demo it.
"""

import base64
import json
import time
from pathlib import Path

from llmdbenchmark.executor.step import Step, StepResult, Phase
from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.executor.command import CommandExecutor
from llmdbenchmark.utilities.endpoint import (
    _rand_suffix,
    _build_overrides,
    _ephemeral_label_args,
    find_standalone_endpoint,
    find_gateway_endpoint,
)


# Transient HTTP status codes / error substrings that warrant a retry.
_RETRYABLE_INDICATORS = ("502", "503", "504", "ServiceUnavailable", "not ready")


def _is_retryable(text: str) -> bool:
    """Return True if the response text indicates a transient failure."""
    return any(ind in text for ind in _RETRYABLE_INDICATORS) if text else False


def _is_non_transient_error(resp: dict) -> bool:
    """Return True if the parsed JSON response is a non-retryable API error.

    This catches errors like "this model does not support /v1/completions"
    which should trigger a fallback to /v1/chat/completions rather than
    a retry.
    """
    if "error" not in resp:
        return False
    error = resp["error"]
    msg = error.get("message", str(error)) if isinstance(error, dict) else str(error)
    # Don't treat transient errors as non-transient
    if _is_retryable(msg):
        return False
    return True


class InferenceTestStep(Step):
    """Run a sample inference request to verify end-to-end model serving."""

    def __init__(self):
        super().__init__(
            number=11,
            name="inference_test",
            description="Run sample inference request against deployed model",
            phase=Phase.STANDUP,
            per_stack=True,
        )

    def should_skip(self, context: ExecutionContext) -> bool:
        return "fma" in context.deployed_methods

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

        errors: list[str] = []
        cmd = context.require_cmd()
        namespace = context.require_namespace()
        stack_name = stack_path.name
        is_standalone = "standalone" in context.deployed_methods

        plan_config = self._load_stack_config(stack_path)
        model_name = self._require_config(plan_config, "model", "name")
        inference_port = self._require_config(
            plan_config, "vllmCommon", "inferencePort"
        )
        release = self._require_config(plan_config, "release")

        # Discover endpoint (same logic as smoketest)
        if is_standalone:
            service_ip, _, gateway_port = find_standalone_endpoint(
                cmd, namespace, inference_port
            )
        else:
            service_ip, _, gateway_port = find_gateway_endpoint(
                cmd, namespace, release
            )

        if not service_ip:
            if context.dry_run:
                service_ip = "<dry-run-endpoint>"
                gateway_port = "80"
            else:
                errors.append("Could not find service/gateway IP for inference test")
                return StepResult(
                    step_number=self.number,
                    step_name=self.name,
                    success=False,
                    message="Inference test failed -- no endpoint found",
                    errors=errors,
                    stack_name=stack_name,
                )

        protocol = "https" if str(gateway_port) == "443" else "http"
        base_url = f"{protocol}://{service_ip}:{gateway_port}"

        context.logger.log_info(
            f"Running sample inference against {base_url}..."
        )

        # --- Try /v1/completions first (universal in vLLM) ---
        context.logger.log_info("Trying /v1/completions endpoint...")
        completions_result = self._try_completions(
            cmd, context, namespace, base_url, model_name, plan_config,
        )

        if completions_result.success:
            self._print_demo_command(
                context, cmd, namespace, plan_config,
                base_url, "/v1/completions",
                completions_result.payload, completions_result.generated_text,
            )
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=True,
                message=f"Inference test passed for {stack_name} (via /v1/completions)",
                stack_name=stack_name,
            )

        # --- Fallback to /v1/chat/completions ---
        if completions_result.should_fallback:
            context.logger.log_info(
                f"/v1/completions returned non-transient error: "
                f"{completions_result.error[:100]}. "
                f"Falling back to /v1/chat/completions..."
            )
            chat_result = self._try_chat_completions(
                cmd, context, namespace, base_url, model_name, plan_config,
            )

            if chat_result.success:
                self._print_demo_command(
                    context, cmd, namespace, plan_config,
                    base_url, "/v1/chat/completions",
                    chat_result.payload, chat_result.generated_text,
                )
                return StepResult(
                    step_number=self.number,
                    step_name=self.name,
                    success=True,
                    message=(
                        f"Inference test passed for {stack_name} "
                        f"(via /v1/chat/completions)"
                    ),
                    stack_name=stack_name,
                )

            errors.append(
                f"/v1/completions failed: {completions_result.error}; "
                f"/v1/chat/completions also failed: {chat_result.error}"
            )
        else:
            errors.append(completions_result.error or "Inference test failed")

        for err in errors:
            context.logger.log_error(f"Inference test: {err}")

        return StepResult(
            step_number=self.number,
            step_name=self.name,
            success=False,
            message=f"Inference test failed for {stack_name}",
            errors=errors,
            stack_name=stack_name,
        )

    # ------------------------------------------------------------------
    # Internal result type
    # ------------------------------------------------------------------

    class _InferenceResult:
        """Encapsulates the outcome of a single inference attempt."""

        __slots__ = ("success", "error", "should_fallback",
                     "generated_text", "payload")

        def __init__(
            self,
            success: bool = False,
            error: str | None = None,
            should_fallback: bool = False,
            generated_text: str = "",
            payload: dict | None = None,
        ):
            self.success = success
            self.error = error
            self.should_fallback = should_fallback
            self.generated_text = generated_text
            self.payload = payload

    # ------------------------------------------------------------------
    # /v1/completions
    # ------------------------------------------------------------------

    def _try_completions(
        self,
        cmd: CommandExecutor,
        context: ExecutionContext,
        namespace: str,
        base_url: str,
        model_name: str,
        plan_config: dict | None,
        max_retries: int = 3,
        retry_interval: int = 15,
    ) -> _InferenceResult:
        """Try a /v1/completions request."""
        url = f"{base_url}/v1/completions"
        payload = {
            "model": model_name,
            "prompt": "The capital of the United States is",
            "max_tokens": 5,
            "temperature": 0,
        }

        for attempt in range(1, max_retries + 1):
            stdout, err = self._curl_post(
                cmd, namespace, url, payload, plan_config,
            )

            if cmd.dry_run:
                return self._InferenceResult(
                    success=True, payload=payload,
                    generated_text="<dry-run>",
                )

            if err:
                if _is_retryable(err) and attempt < max_retries:
                    context.logger.log_info(
                        f"Attempt {attempt}/{max_retries}: {err[:80]}, "
                        f"retrying in {retry_interval}s..."
                    )
                    time.sleep(retry_interval)
                    continue
                return self._InferenceResult(error=err)

            # Parse JSON
            try:
                resp = json.loads(stdout)
            except json.JSONDecodeError:
                if _is_retryable(stdout) and attempt < max_retries:
                    time.sleep(retry_interval)
                    continue
                return self._InferenceResult(
                    error=f"Non-JSON response from {url}: {stdout[:200]}"
                )

            # Non-transient API error to signal fallback
            if _is_non_transient_error(resp):
                error_msg = resp["error"]
                if isinstance(error_msg, dict):
                    error_msg = error_msg.get("message", str(error_msg))
                return self._InferenceResult(
                    error=str(error_msg),
                    should_fallback=True,
                )

            # Transient error to retry
            if "error" in resp:
                error_msg = resp["error"]
                if isinstance(error_msg, dict):
                    error_msg = error_msg.get("message", str(error_msg))
                if attempt < max_retries:
                    time.sleep(retry_interval)
                    continue
                return self._InferenceResult(error=str(error_msg))

            # Validate structure
            validation_err = self._validate_completions(resp, url)
            if validation_err:
                if attempt < max_retries:
                    time.sleep(retry_interval)
                    continue
                return self._InferenceResult(error=validation_err)

            # Success -- extract generated text
            text = resp["choices"][0].get("text", "").strip()
            return self._InferenceResult(
                success=True,
                generated_text=text,
                payload=payload,
            )

        return self._InferenceResult(
            error=f"Exhausted {max_retries} retries for {url}"
        )

    # ------------------------------------------------------------------
    # /v1/chat/completions
    # ------------------------------------------------------------------

    def _try_chat_completions(
        self,
        cmd: CommandExecutor,
        context: ExecutionContext,
        namespace: str,
        base_url: str,
        model_name: str,
        plan_config: dict | None,
        max_retries: int = 3,
        retry_interval: int = 15,
    ) -> _InferenceResult:
        """Try a /v1/chat/completions request."""
        url = f"{base_url}/v1/chat/completions"
        payload = {
            "model": model_name,
            "messages": [{"role": "user", "content": "What is the capital of the United States?"}],
            "max_tokens": 5,
            "temperature": 0,
        }

        for attempt in range(1, max_retries + 1):
            stdout, err = self._curl_post(
                cmd, namespace, url, payload, plan_config,
            )

            if err:
                if _is_retryable(err) and attempt < max_retries:
                    context.logger.log_info(
                        f"Chat attempt {attempt}/{max_retries}: {err[:80]}, "
                        f"retrying in {retry_interval}s..."
                    )
                    time.sleep(retry_interval)
                    continue
                return self._InferenceResult(error=err)

            try:
                resp = json.loads(stdout)
            except json.JSONDecodeError:
                if _is_retryable(stdout) and attempt < max_retries:
                    time.sleep(retry_interval)
                    continue
                return self._InferenceResult(
                    error=f"Non-JSON response from {url}: {stdout[:200]}"
                )

            # API error
            if "error" in resp:
                error_msg = resp["error"]
                if isinstance(error_msg, dict):
                    error_msg = error_msg.get("message", str(error_msg))
                if _is_retryable(str(error_msg)) and attempt < max_retries:
                    time.sleep(retry_interval)
                    continue
                return self._InferenceResult(error=str(error_msg))

            # Validate structure
            validation_err = self._validate_chat_completions(resp, url)
            if validation_err:
                if attempt < max_retries:
                    time.sleep(retry_interval)
                    continue
                return self._InferenceResult(error=validation_err)

            # Success -- extract generated text
            message = resp["choices"][0].get("message", {})
            text = message.get("content", "").strip()
            return self._InferenceResult(
                success=True,
                generated_text=text,
                payload=payload,
            )

        return self._InferenceResult(
            error=f"Exhausted {max_retries} retries for {url}"
        )

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _curl_post(
        self,
        cmd: CommandExecutor,
        namespace: str,
        url: str,
        payload: dict,
        plan_config: dict | None,
        timeout_seconds: int = 120,
    ) -> tuple[str, str | None]:
        """Execute a POST request via an ephemeral curl pod.

        Returns (stdout, error_string).  error_string is None on success.

        The JSON payload is base64-encoded and decoded inside the pod to
        avoid shell quoting issues when passing through kubectl to sh -c.
        """
        override_args = _build_overrides(plan_config)
        curl_image = "quay.io/curl/curl"
        pod_name = f"inference-test-{_rand_suffix()}"
        payload_json = json.dumps(payload)
        payload_b64 = base64.b64encode(payload_json.encode()).decode()

        # Decode the base64 payload inside the pod, pipe to curl via stdin.
        # This avoids all shell quoting problems with nested JSON.
        curl_cmd = (
            f"'echo {payload_b64} | base64 -d | "
            f"curl -sk --max-time {timeout_seconds} "
            f"-X POST {url} "
            f"-H \"Content-Type: application/json\" "
            f"-d @- 2>&1'"
        )

        kubectl_args = (
            [
                "run", pod_name, "--rm", "--attach", "--quiet",
                "--restart=Never", "--namespace", namespace,
                f"--image={curl_image}",
            ]
            + _ephemeral_label_args()
            + override_args
            + ["--command", "--", "sh", "-c", curl_cmd]
        )

        result = cmd.kube(*kubectl_args, check=False)

        if result.dry_run:
            return "", None  # Command logged, return empty success

        if not result.success:
            detail = result.stderr[:300] or result.stdout[:300]
            return "", f"Curl to {url} failed: {detail}"

        return result.stdout.strip(), None

    @staticmethod
    def _validate_completions(resp: dict, url: str) -> str | None:
        """Validate /v1/completions response structure."""
        if "choices" not in resp:
            return f"Missing 'choices' in response from {url}"
        if not resp["choices"]:
            return f"Empty 'choices' array from {url}"
        first = resp["choices"][0]
        if not first.get("text") and not first.get("message"):
            return f"No generated text in response from {url}"
        return None

    @staticmethod
    def _validate_chat_completions(resp: dict, url: str) -> str | None:
        """Validate /v1/chat/completions response structure."""
        if "choices" not in resp:
            return f"Missing 'choices' in response from {url}"
        if not resp["choices"]:
            return f"Empty 'choices' array from {url}"
        first = resp["choices"][0]
        message = first.get("message", {})
        if not message.get("content"):
            return f"No generated content in response from {url}"
        return None

    def _print_demo_command(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self,
        context: ExecutionContext,
        cmd: CommandExecutor,
        namespace: str,
        plan_config: dict,
        base_url: str,
        endpoint: str,
        payload: dict,
        generated_text: str,
    ):
        """Log a reproducible curl command the user can copy-paste to demo.

        Detects the OpenShift route (if available) and prints an
        externally-accessible URL.  Falls back to the cluster-internal
        URL with a note when no route is found.
        """
        payload_compact = json.dumps(payload, separators=(",", ":"))

        context.logger.log_info(f"✅ Inference test passed via {endpoint}")
        if generated_text:
            context.logger.log_info(f"   Generated: \"{generated_text[:80]}\"")
        context.logger.log_info("")

        # Try to detect the OpenShift route for an external URL
        external_url = self._detect_external_url(
            cmd, namespace, plan_config, endpoint,
        )

        if external_url:
            context.logger.log_info("   To reproduce or demo, run:")
            context.logger.log_info(f"   curl -sk -X POST \\")
            context.logger.log_info(f"     {external_url} \\")
            context.logger.log_info(f"     -H 'Content-Type: application/json' \\")
            context.logger.log_info(f"     -d '{payload_compact}'")
        else:
            cluster_url = f"{base_url}{endpoint}"
            context.logger.log_info(
                "   To reproduce (from inside the cluster or via port-forward):"
            )
            context.logger.log_info(f"   curl -sk -X POST \\")
            context.logger.log_info(f"     {cluster_url} \\")
            context.logger.log_info(f"     -H 'Content-Type: application/json' \\")
            context.logger.log_info(f"     -d '{payload_compact}'")

    def _detect_external_url(
        self,
        cmd: CommandExecutor,
        namespace: str,
        plan_config: dict,
        endpoint: str,
    ) -> str | None:
        """Detect an OpenShift route and build an external URL.

        Returns a full URL like
        ``https://route-host/model-short/v1/completions``
        or None if no route is found.
        """
        try:
            release = self._require_config(plan_config, "release")
            model_id_label = plan_config.get("model_id_label", "")
        except KeyError:
            return None

        route_name = f"{release}-inference-gateway-route"
        result = cmd.kube(
            "get", "route", route_name,
            "-n", namespace,
            "-o", "jsonpath={.spec.host}:{.spec.tls.termination}",
            check=False,
        )

        if not result.success or not result.stdout.strip():
            return None

        parts = result.stdout.strip().strip("'").split(":", 1)
        route_host = parts[0]
        tls_termination = parts[1] if len(parts) > 1 else ""
        protocol = "https" if tls_termination else "http"

        # External requests go through the HTTPRoute which requires
        # the model ID label path prefix.
        return f"{protocol}://{route_host}/{model_id_label}{endpoint}"
