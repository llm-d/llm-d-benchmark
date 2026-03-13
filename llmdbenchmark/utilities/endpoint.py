"""Shared endpoint detection and model verification utilities.

Extracted from standup/steps/step_10_smoketest.py so that both the
smoketest step and the run phase can reuse the same logic.
"""

import json
import random
import string
import time

from llmdbenchmark.executor.command import CommandExecutor


def _rand_suffix(length: int = 8) -> str:
    """Generate a random lowercase alphanumeric suffix for pod names."""
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))


def _build_overrides(plan_config: dict | None) -> list[str]:
    """Build --overrides args for ephemeral curl pods (imagePullSecrets, serviceAccount)."""
    overrides: dict = {}
    if plan_config:
        pull_secret = plan_config.get("vllmCommon", {}).get("imagePullSecrets", "")
        if pull_secret:
            overrides.setdefault("spec", {})["imagePullSecrets"] = [
                {"name": pull_secret}
            ]
        sa_name = plan_config.get("serviceAccount", {}).get("name")
        if sa_name:
            overrides.setdefault("spec", {})["serviceAccountName"] = sa_name

    if overrides:
        return ["--overrides", f"'{json.dumps(overrides)}'"]
    return []


def find_standalone_endpoint(
    cmd: CommandExecutor, namespace: str, inference_port: int | str = 80
) -> tuple[str | None, str | None, str]:
    """Find standalone service IP and port.

    Queries for services labelled ``stood-up-from=llm-d-benchmark``.

    Returns:
        (ip, service_name, port) — any may be None/default if not found.
    """
    result = cmd.kube(
        "get",
        "service",
        "-l",
        "stood-up-from=llm-d-benchmark",
        "--namespace",
        namespace,
        "-o",
        "jsonpath={.items[0].spec.clusterIP}:{.items[0].metadata.name}:{.items[0].spec.ports[0].port}",
        check=False,
    )
    if result.success and result.stdout.strip():
        parts = result.stdout.strip().split(":")
        ip = parts[0] if parts else None
        name = parts[1] if len(parts) > 1 else None
        svc_port = parts[2] if len(parts) > 2 else "80"
        return ip, name, svc_port
    return None, None, "80"


def find_gateway_endpoint(
    cmd: CommandExecutor, namespace: str, release: str
) -> tuple[str | None, str | None, str]:
    """Find the gateway IP and detect HTTPS from the Gateway resource.

    Returns:
        (ip_or_hostname, gateway_name, port) — port is '443' for HTTPS, '80' otherwise.
    """
    gateway_name = f"infra-{release}-inference-gateway"
    gateway_port = "80"

    result = cmd.kube(
        "get",
        "gateway",
        gateway_name,
        "--namespace",
        namespace,
        "-o",
        "json",
        check=False,
    )
    if result.success and result.stdout.strip():
        try:
            gw_data = json.loads(result.stdout)

            managed_fields = gw_data.get("metadata", {}).get("managedFields", [])
            for mf in managed_fields:
                fields_v1 = mf.get("fieldsV1", {})
                f_status = fields_v1.get("f:status", {})
                f_listeners = f_status.get("f:listeners", {})
                for key in f_listeners:
                    if "https" in key.lower():
                        gateway_port = "443"
                        break

            addresses = gw_data.get("status", {}).get("addresses", [])
            for addr in addresses:
                addr_type = addr.get("type", "")
                value = addr.get("value", "")
                if addr_type == "IPAddress" and value:
                    return value, gateway_name, gateway_port
                if addr_type == "Hostname" and value:
                    return value, gateway_name, gateway_port

        except (json.JSONDecodeError, KeyError):
            pass

    # Fallback: try querying the service directly
    result = cmd.kube(
        "get",
        "service",
        gateway_name,
        "--namespace",
        namespace,
        "-o",
        "jsonpath={.spec.clusterIP}",
        check=False,
    )
    if result.success and result.stdout.strip():
        return result.stdout.strip(), gateway_name, gateway_port

    return None, gateway_name, gateway_port


# Retryable HTTP status codes / error substrings that indicate the
# model is still loading or the P/D topology isn't ready yet.
_RETRYABLE_INDICATORS = (
    "ServiceUnavailable",
    "not ready",
    "still loading",
    "503",
    "502",
)


def _is_retryable(text: str) -> bool:
    """Return True if the response text indicates a transient failure."""
    if not text:
        return False
    return any(indicator in text for indicator in _RETRYABLE_INDICATORS)


def validate_model_response(
    stdout: str, expected_model: str, host: str, port: str | int
) -> str | None:
    """Check that the /v1/models response contains the expected model.

    Returns None on success, or an error string describing the mismatch.
    """
    try:
        models_response = json.loads(stdout)
        model_ids = [m.get("id", "") for m in models_response.get("data", [])]
        if expected_model not in model_ids:
            return (
                f"Endpoint {host}:{port} did not return expected "
                f"model '{expected_model}'. "
                f"Available models: {model_ids}"
            )
    except (json.JSONDecodeError, KeyError, TypeError):
        if expected_model not in stdout:
            return (
                f"Endpoint {host}:{port} did not return expected "
                f"model '{expected_model}'. "
                f"Got: {stdout[:200]}"
            )
    return None


def test_model_serving(
    cmd: CommandExecutor,
    namespace: str,
    host: str,
    port: str | int,
    expected_model: str,
    plan_config: dict | None = None,
    max_retries: int = 12,
    retry_interval: int = 15,
) -> str | None:
    """Test an endpoint by querying /v1/models via an ephemeral curl pod.

    Retries up to *max_retries* times (default 12 x 15 s = 3 min) when
    the response indicates the model is still loading or the decode
    node isn't ready (503 / ServiceUnavailable).

    Returns None on success, or an error string describing the failure.
    """
    protocol = "https" if str(port) == "443" else "http"
    url = f"{protocol}://{host}:{port}/v1/models"
    override_args = _build_overrides(plan_config)
    curl_image = "curlimages/curl"
    last_error: str | None = None

    for attempt in range(1, max_retries + 1):
        pod_name = f"smoketest-{_rand_suffix()}"

        curl_cmd = (
            f"'curl -sk --retry 3 --retry-delay 3 "
            f"--retry-all-errors --max-time 30 {url} 2>&1'"
        )

        kubectl_args = (
            [
                "run",
                pod_name,
                "--rm",
                "--attach",
                "--quiet",
                "--restart=Never",
                "--namespace",
                namespace,
                f"--image={curl_image}",
            ]
            + override_args
            + [
                "--command",
                "--",
                "sh",
                "-c",
                curl_cmd,
            ]
        )

        result = cmd.kube(*kubectl_args, check=False)

        if not result.success:
            detail = result.stderr[:200] or result.stdout[:200]
            last_error = f"Curl to {host}:{port} failed: {detail}"
            if _is_retryable(detail) and attempt < max_retries:
                cmd.logger.log_info(
                    f"Attempt {attempt}/{max_retries}: endpoint not "
                    f"ready, retrying in {retry_interval}s..."
                )
                time.sleep(retry_interval)
                continue
            return last_error

        stdout = result.stdout.strip()

        # Check for retryable error responses (e.g. 503 decode not ready)
        if _is_retryable(stdout) and attempt < max_retries:
            cmd.logger.log_info(
                f"Attempt {attempt}/{max_retries}: endpoint returned "
                f"transient error, retrying in {retry_interval}s..."
            )
            time.sleep(retry_interval)
            continue

        # Validate the model is being served
        if expected_model and stdout:
            check_err = validate_model_response(
                stdout, expected_model, host, port
            )
            if check_err:
                # If it looks retryable, keep trying
                if _is_retryable(stdout) and attempt < max_retries:
                    time.sleep(retry_interval)
                    continue
                return check_err

        return None  # success

    return last_error
