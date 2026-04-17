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


EPHEMERAL_POD_LABEL = "llm-d-benchmark/ephemeral=true"
"""Label applied to all ephemeral curl/smoketest pods for cleanup."""


def _build_overrides(plan_config: dict | None, service_account: str | None = None) -> list[str]:
    """Build --overrides args for ephemeral curl pods (imagePullSecrets, serviceAccount)."""
    overrides: dict = {}
    if plan_config:
        pull_secret = plan_config.get("vllmCommon", {}).get("pullSecret", "")
        if pull_secret:
            overrides.setdefault("spec", {})["imagePullSecrets"] = [
                {"name": pull_secret}
            ]

    sa_name = service_account or (plan_config.get("serviceAccount", {}).get("name") if plan_config else None)
    if sa_name:
        overrides.setdefault("spec", {})["serviceAccountName"] = sa_name

    if overrides:
        return ["--overrides", f"'{json.dumps(overrides)}'"]
    return []


def _ephemeral_label_args() -> list[str]:
    """Return kubectl args to label ephemeral pods for cleanup."""
    return [f"--labels={EPHEMERAL_POD_LABEL}"]


def cleanup_ephemeral_pods(
    cmd: CommandExecutor, namespace: str, logger=None,
) -> None:
    """Delete all completed ephemeral pods created by smoketest/endpoint checks.

    Targets pods with the ``llm-d-benchmark/ephemeral=true`` label that are
    in Succeeded or Failed phase.
    """
    for phase in ("Succeeded", "Failed"):
        result = cmd.kube(
            "delete", "pods",
            f"-l", EPHEMERAL_POD_LABEL,
            f"--field-selector=status.phase={phase}",
            "--namespace", namespace,
            check=False,
        )
        if result.success and result.stdout.strip() and "No resources" not in result.stdout:
            if logger:
                logger.log_info(
                    f"Cleaned up ephemeral pods ({phase}) in ns/{namespace}"
                )


def find_standalone_endpoint(
    cmd: CommandExecutor, namespace: str, inference_port: int | str = 80
) -> tuple[str | None, str | None, str]:
    """Find standalone service IP and port.

    Queries for services labelled ``stood-up-from=llm-d-benchmark``.

    Returns:
        (ip, service_name, port) -- any may be None/default if not found.
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

def find_fma_endpoint(cmd: CommandExecutor, namespace: str) -> str | None:
    """Find FMA replicaset name.

    Queries for replicaset labelled ``stood-up-from=llm-d-benchmark``.

    Returns:
        name -- None if not found.
    """

    result = cmd.kube(
        "get",
        "replicaset",
        "-l",
        "stood-up-from=llm-d-benchmark",
        "--namespace",
        namespace,
        "-o",
        "jsonpath={.items[*].metadata.name}",
        check=False,
    )
    if result.success and result.stdout.strip():
        return f"{result.stdout.strip()}.{namespace}.cluster.local"

    return None


def find_gateway_endpoint(
    cmd: CommandExecutor, namespace: str, release: str
) -> tuple[str | None, str | None, str]:
    """Find the gateway IP and detect HTTPS from the Gateway resource.

    Returns:
        (ip_or_hostname, gateway_name, port) -- port is '443' for HTTPS, '80' otherwise.
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


def discover_hf_token_secret(
    cmd: CommandExecutor,
    namespace: str,
) -> str | None:
    """Auto-discover a HuggingFace token secret in the given namespace.

    Matches the original bash run.sh pattern which searches for secrets
    whose name matches ``llm-d-hf.*token.*``.

    Returns:
        The secret name (e.g. ``llm-d-hf-token``) if found, else None.
    """
    result = cmd.kube(
        "get", "secrets",
        "--namespace", namespace,
        "--no-headers",
        "-o", "custom-columns=NAME:.metadata.name",
        check=False,
    )
    if not result.success or not result.stdout.strip():
        return None

    import re
    pattern = re.compile(r"llm-d-hf.*token", re.IGNORECASE)
    for line in result.stdout.strip().splitlines():
        secret_name = line.strip()
        if pattern.search(secret_name):
            return secret_name
    return None


def extract_hf_token_from_secret(
    cmd: CommandExecutor,
    namespace: str,
    secret_name: str,
    key: str = "HF_TOKEN",
) -> str | None:
    """Extract the HuggingFace token value from a Kubernetes secret.

    Tries the specific *key* first; if the secret has a different data key
    it falls back to reading all data values and returning the first one
    that starts with ``hf_``.

    Returns:
        The decoded token string, or None if extraction fails.
    """
    import base64 as _b64

    # Try the explicit key
    result = cmd.kube(
        "get", "secret", secret_name,
        "--namespace", namespace,
        "-o", f"jsonpath={{.data.{key}}}",
        check=False,
    )
    if result.success and result.stdout.strip():
        try:
            decoded = _b64.b64decode(result.stdout.strip()).decode("utf-8")
            if decoded.startswith("hf_"):
                return decoded
        except Exception:
            pass

    # Fallback: dump all data values
    result = cmd.kube(
        "get", "secret", secret_name,
        "--namespace", namespace,
        "-o", "jsonpath={.data}",
        check=False,
    )
    if result.success and result.stdout.strip():
        try:
            data = json.loads(result.stdout.strip())
            for val in data.values():
                decoded = _b64.b64decode(val).decode("utf-8")
                if decoded.startswith("hf_"):
                    return decoded
        except Exception:
            pass

    return None


def find_custom_endpoint(
    cmd: CommandExecutor,
    namespace: str,
    method_pattern: str,
) -> tuple[str | None, str | None, str]:
    """Discover an endpoint for non-standard (custom) deployments.

    Implements the same multi-level fallback as the original bash run.sh
    for deployments that are neither *standalone* nor *modelservice*:

    1. **Service match** -- look for a service whose name contains
       *method_pattern*; extract port from named ports (``default``,
       ``http``, ``https``).
    2. **Pod match** -- look for a pod whose name contains *method_pattern*;
       extract port from liveness/readiness probes, then fall back to the
       ``metrics`` container port; resolve to the pod IP.

    Returns:
        ``(ip_or_service, name, port)`` -- any may be ``None`` if nothing
        matched.
    """
    # --- 1. Try to find a matching Service ---
    svc_result = cmd.kube(
        "get", "service",
        "--namespace", namespace,
        "--no-headers",
        "-o", "custom-columns=NAME:.metadata.name",
        check=False,
    )
    if svc_result.success and svc_result.stdout.strip():
        for svc_name in svc_result.stdout.strip().splitlines():
            svc_name = svc_name.strip()
            if method_pattern not in svc_name:
                continue
            # Found a matching service -- try port names: default, http, https
            for port_name in ("default", "http", "https"):
                port_result = cmd.kube(
                    "get", f"service/{svc_name}",
                    "--namespace", namespace,
                    "-o", f"jsonpath={{.spec.ports[?(@.name==\"{port_name}\")].port}}",
                    check=False,
                )
                port_val = (
                    port_result.stdout.strip()
                    if port_result.success else ""
                )
                if port_val and port_val != "null":
                    return svc_name, svc_name, port_val

            # Fall back to first port
            port_result = cmd.kube(
                "get", f"service/{svc_name}",
                "--namespace", namespace,
                "-o", "jsonpath={.spec.ports[0].port}",
                check=False,
            )
            port_val = port_result.stdout.strip() if port_result.success else ""
            if port_val and port_val != "null":
                return svc_name, svc_name, port_val

    # --- 2. Try to find a matching Pod ---
    pod_result = cmd.kube(
        "get", "pod",
        "--namespace", namespace,
        "--no-headers",
        "-o", "custom-columns=NAME:.metadata.name",
        check=False,
    )
    if pod_result.success and pod_result.stdout.strip():
        for pod_line in pod_result.stdout.strip().splitlines():
            pod_name = pod_line.strip()
            if method_pattern not in pod_name:
                continue
            # Found a pod -- try probes for port
            port_val = None
            for probe in ("livenessProbe", "readinessProbe"):
                probe_result = cmd.kube(
                    "get", f"pod/{pod_name}",
                    "--namespace", namespace,
                    "-o", f"jsonpath={{.spec.containers[0].{probe}.httpGet.port}}",
                    check=False,
                )
                pv = probe_result.stdout.strip() if probe_result.success else ""
                if pv and pv != "null":
                    port_val = pv
                    break

            # Fall back to metrics container port
            if not port_val:
                metrics_result = cmd.kube(
                    "get", f"pod/{pod_name}",
                    "--namespace", namespace,
                    "-o", 'jsonpath={.spec.containers[0].ports[?(@.name=="metrics")].containerPort}',
                    check=False,
                )
                pv = metrics_result.stdout.strip() if metrics_result.success else ""
                if pv and pv != "null":
                    port_val = pv

            if not port_val:
                continue

            # Resolve pod IP
            ip_result = cmd.kube(
                "get", f"pod/{pod_name}",
                "--namespace", namespace,
                "-o", "jsonpath={.status.podIP}",
                check=False,
            )
            pod_ip = ip_result.stdout.strip() if ip_result.success else None
            if pod_ip and pod_ip != "null":
                return pod_ip, pod_name, port_val

    return None, None, "80"


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
    service_account: str | None = None,
) -> str | None:
    """Test an endpoint by querying /v1/models via an ephemeral curl pod.

    Retries up to *max_retries* times (default 12 x 15 s = 3 min) when
    the response indicates the model is still loading or the decode
    node isn't ready (503 / ServiceUnavailable).

    Returns None on success, or an error string describing the failure.
    """
    protocol = "https" if str(port) == "443" else "http"
    url = f"{protocol}://{host}:{port}/v1/models"
    
    # Auto-ensure service account and RBAC
    sa_name = service_account or (plan_config.get("serviceAccount", {}).get("name") if plan_config else "default")
    if sa_name:
        if sa_name != "default":
            sa_check = cmd.kube("get", "sa", sa_name, "--namespace", namespace, check=False)
            if not sa_check.success or "not found" in (sa_check.stderr + sa_check.stdout).lower():
                cmd.logger.log_info(f"ServiceAccount '{sa_name}' not found, auto-creating it...")
                cmd.kube("create", "sa", sa_name, "--namespace", namespace, check=False)
        
        # Always ensure the required RBAC role exists
        role_name = f"{sa_name}-role"
        role_check = cmd.kube("get", "role", role_name, "--namespace", namespace, check=False)
        if not role_check.success or "not found" in (role_check.stderr + role_check.stdout).lower():
            cmd.logger.log_info(f"RBAC Role '{role_name}' missing for ServiceAccount '{sa_name}', auto-creating it...")
            cmd.kube(
                "create", "role", role_name,
                "--verb=get,list", "--resource=configmaps,pods,pods/log",
                "--namespace", namespace,
                check=False
            )
            
            # Always ensure RoleBinding
            binding_name = f"{sa_name}-binding"
            cmd.kube(
                "create", "rolebinding", binding_name,
                f"--role={role_name}",
                f"--serviceaccount={namespace}:{sa_name}",
                "--namespace", namespace,
                check=False
            )

    override_args = _build_overrides(plan_config, service_account=service_account)
    curl_image = "quay.io/curl/curl"
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
            + _ephemeral_label_args()
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

        if result.dry_run:
            return None  # Command logged, skip retries

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
