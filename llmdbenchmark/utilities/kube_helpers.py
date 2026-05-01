"""Shared Kubernetes helper functions used across run-phase steps.

Extracts common kubectl patterns (waiting, collecting, logging, cleanup)
into reusable functions to avoid duplication between step_06, step_07,
step_08, and step_10.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from llmdbenchmark.executor.context import ExecutionContext

# Container states that indicate a pod will never succeed.
CRASH_STATES = {
    "CrashLoopBackOff",
    "Error",
    "OOMKilled",
    "CreateContainerConfigError",
    "ImagePullBackOff",
    "ErrImagePull",
    "InvalidImageName",
}

DATA_ACCESS_LABEL = "role=llm-d-benchmark-data-access"


# ---------------------------------------------------------------------------
# Pod discovery
# ---------------------------------------------------------------------------

def find_data_access_pod(cmd, namespace: str) -> str | None:
    """Find the data-access pod by its well-known label.

    Returns the pod name, or ``None`` if not found.
    """
    result = cmd.kube(
        "get", "pod",
        "-l", DATA_ACCESS_LABEL,
        "--namespace", namespace,
        "-o", "jsonpath={.items[0].metadata.name}",
        check=False,
    )
    if result.success and result.stdout.strip():
        return result.stdout.strip()
    return None


# ---------------------------------------------------------------------------
# Pod waiting
# ---------------------------------------------------------------------------

def wait_for_pods_deleted(
    cmd,
    selector: str,
    namespace: str,
    timeout: int,
    context: ExecutionContext,
) -> None:
    """Wait for all pods matching a label selector to be fully deleted.

    Uses ``kubectl wait pod --for=delete --selector=<selector>``.
    Errors are logged as warnings but not raised, so teardown continues
    even if the wait times out.
    """
    context.logger.log_info(
        f"Waiting for pods ({selector}) to be deleted in {namespace} "
        f"(timeout={timeout}s)..."
    )
    result = cmd.kube(
        "wait", "pod",
        "--for=delete",
        f"--selector={selector}",
        "--namespace", namespace,
        f"--timeout={timeout}s",
        check=False,
    )
    if not result.success and result.stderr.strip():
        context.logger.log_warning(
            f"Wait for pod deletion timed out or failed ({selector}): "
            f"{result.stderr.strip()}"
        )


def wait_for_pods_by_label(
    cmd,
    label: str,
    namespace: str,
    timeout: int,
    context: ExecutionContext,
) -> list[str]:
    """Wait for pods to start and then complete using label-based kubectl wait.

    Uses the same two-phase approach as the original bash:

    1. ``kubectl wait --for=condition=Ready=True`` -- pods are running
    2. ``kubectl wait --for=condition=ready=False`` -- pods have finished

    Returns a list of error strings (empty on success).
    """
    errors: list[str] = []

    # Phase A: Wait for pods to become Ready (running)
    context.logger.log_info(
        f"Waiting for pods (label=app={label}) to start "
        f"(timeout={timeout}s)..."
    )
    result = cmd.kube(
        "wait", "--for=condition=Ready=True",
        "pod", "-l", f"app={label}",
        "--namespace", namespace,
        f"--timeout={timeout}s",
        check=False,
    )
    if not result.success:
        errors.append(
            f"Pods failed to become Ready: {result.stderr.strip()}"
        )
        return errors

    context.logger.log_info("All pods are running")

    # Phase B: Wait for pods to complete (Ready=False after finish)
    context.logger.log_info(
        f"Waiting for pods (label=app={label}) to complete "
        f"(timeout={timeout}s)..."
    )
    result = cmd.kube(
        "wait", f"--timeout={timeout}s",
        "--for=condition=ready=False",
        "pod", "-l", f"app={label}",
        "--namespace", namespace,
        check=False,
    )
    if not result.success:
        errors.append(
            f"Pods did not complete within timeout: {result.stderr.strip()}"
        )
        return errors

    # Check for crash states
    check_result = cmd.kube(
        "get", "pods",
        "-l", f"app={label}",
        "--namespace", namespace,
        "--no-headers",
        check=False,
    )
    if check_result.success and check_result.stdout:
        for state in CRASH_STATES:
            if state in check_result.stdout:
                errors.append(
                    f"Found pods in error state. Run: "
                    f"kubectl --namespace {namespace} get pods "
                    f"-l app={label}"
                )
                break

    if not errors:
        context.logger.log_info("All pods completed successfully")

    return errors


def wait_for_pod(
    cmd,
    pod_name: str,
    namespace: str,
    timeout: int,
    context: ExecutionContext,
    poll_interval: int = 15,
) -> str:
    """Wait for a single pod to reach a terminal phase via polling.

    This is the per-pod polling fallback used by step_07 when pods
    are tracked individually (e.g. when step_06 populated
    ``context.deployed_pod_names``).

    Returns:
        ``'Succeeded'``, ``'Failed'``, or an error description string.
    """
    start = time.time()

    while True:
        elapsed = time.time() - start
        if elapsed > timeout:
            return f"Timed out after {timeout}s"

        result = cmd.kube(
            "get", "pod", pod_name,
            "--namespace", namespace,
            "-o", "jsonpath={.status.phase}:{.status.containerStatuses[0].state}",
            check=False,
        )

        if result.dry_run:
            return "Succeeded"  # Command logged, skip polling

        if not result.success:
            # Pod may not exist yet
            time.sleep(poll_interval)
            continue

        output = result.stdout.strip()
        parts = output.split(":", 1)
        phase = parts[0] if parts else ""

        if phase == "Succeeded":
            context.logger.log_info(
                f"Pod '{pod_name}' completed successfully "
                f"({int(elapsed)}s)"
            )
            return "Succeeded"

        if phase == "Failed":
            exit_result = cmd.kube(
                "get", "pod", pod_name,
                "--namespace", namespace,
                "-o", "jsonpath={.status.containerStatuses[0].state.terminated.exitCode}",
                check=False,
            )
            exit_code = (
                exit_result.stdout.strip() if exit_result.success else "?"
            )
            context.logger.log_error(
                f"Pod '{pod_name}' failed (exit_code={exit_code}, "
                f"{int(elapsed)}s)"
            )
            return "Failed"

        # Check for crash states via container status
        container_result = cmd.kube(
            "get", "pod", pod_name,
            "--namespace", namespace,
            "-o", "jsonpath={.status.containerStatuses[0].state.waiting.reason}",
            check=False,
        )
        if container_result.success and container_result.stdout.strip():
            reason = container_result.stdout.strip()
            if reason in CRASH_STATES:
                context.logger.log_error(
                    f"Pod '{pod_name}' in terminal state: {reason}"
                )
                return f"Terminal state: {reason}"

        remaining = int(timeout - elapsed)
        context.logger.log_info(
            f"Pod '{pod_name}': {phase} ({int(elapsed)}s elapsed, "
            f"{remaining}s remaining)"
        )
        time.sleep(poll_interval)


# ---------------------------------------------------------------------------
# Result collection
# ---------------------------------------------------------------------------

def collect_pod_results(
    cmd,
    data_pod: str,
    namespace: str,
    remote_prefix: str,
    experiment_id: str,
    parallel_idx: int,
    local_results_dir: Path,
    context: ExecutionContext,
) -> tuple[Path, bool, str]:
    """Copy results for a single parallel pod instance from the PVC.

    Each pod stores results in ``<remote_prefix>/<experiment_id>_<idx>``.
    Results are copied to ``<local_results_dir>/<experiment_id>_<idx>``.

    Returns:
        ``(local_path, success, error_msg)`` tuple.
    """
    pod_suffix = f"{experiment_id}_{parallel_idx}"
    remote_path = (
        f"{data_pod}:"
        f"{remote_prefix}/{pod_suffix}"
    )
    local_path = local_results_dir / pod_suffix
    local_path.mkdir(parents=True, exist_ok=True)

    cp_result = cmd.kube(
        "cp", "--retries=5",
        remote_path, str(local_path),
        namespace=namespace,
        check=False,
    )

    if not cp_result.success:
        return local_path, False, (
            f"Failed to copy results for {pod_suffix}: "
            f"{cp_result.stderr[:200]}"
        )

    file_count = sum(1 for f in local_path.rglob("*") if f.is_file())
    if file_count > 0:
        context.logger.log_info(
            f"Collected {file_count} file(s) for {pod_suffix}"
        )
    else:
        context.logger.log_warning(
            f"No files collected for {pod_suffix} (directory may be empty)"
        )

    return local_path, True, ""


def sync_analysis_dir(
    local_path: Path,
    analysis_dir: Path,
    experiment_suffix: str,
) -> None:
    """Sync the ``analysis/`` sub-directory from results to a dedicated dir.

    Removes the ``analysis/`` dir from the results directory after syncing,
    matching the bash ``rsync + rm`` pattern.
    """
    analysis_src = local_path / "analysis"
    if not analysis_src.is_dir():
        return

    pod_analysis_dir = analysis_dir / experiment_suffix
    pod_analysis_dir.mkdir(parents=True, exist_ok=True)
    for item in analysis_src.iterdir():
        dest = pod_analysis_dir / item.name
        if item.is_file():
            shutil.copy2(str(item), str(dest))
        elif item.is_dir():
            shutil.copytree(str(item), str(dest), dirs_exist_ok=True)
    # Remove analysis from results dir (matches bash rsync + rm)
    shutil.rmtree(str(analysis_src), ignore_errors=True)


# ---------------------------------------------------------------------------
# Pod cleanup
# ---------------------------------------------------------------------------

def delete_pods_by_names(
    cmd,
    pod_names: list[str],
    namespace: str,
    context: ExecutionContext,
) -> None:
    """Delete pods by individual name."""
    for pod_name in pod_names:
        result = cmd.kube(
            "delete", "pod", pod_name,
            "--namespace", namespace,
            "--ignore-not-found",
            check=False,
        )
        if result.success:
            context.logger.log_info(f"Deleted pod '{pod_name}'")
        else:
            context.logger.log_warning(
                f"Could not delete pod '{pod_name}': {result.stderr}"
            )


def delete_pods_by_label(
    cmd,
    label: str,
    namespace: str,
    context: ExecutionContext,
) -> None:
    """Delete all pods matching a label selector."""
    result = cmd.kube(
        "delete", "pod",
        "-l", f"app={label}",
        "--namespace", namespace,
        "--ignore-not-found",
        check=False,
    )
    if result.success:
        context.logger.log_info("Harness pods deleted")
    else:
        context.logger.log_warning(
            f"Pod cleanup warning: {result.stderr}"
        )


# ---------------------------------------------------------------------------
# Log capture
# ---------------------------------------------------------------------------

def capture_pod_logs(
    cmd,
    pod_names: list[str],
    namespace: str,
    log_dir: Path,
    context: ExecutionContext,
) -> None:
    """Capture logs from individual harness pods."""
    log_dir.mkdir(parents=True, exist_ok=True)
    for pod_name in pod_names:
        result = cmd.kube(
            "logs", pod_name,
            "--namespace", namespace,
            check=False,
        )
        if result.success and result.stdout:
            log_file = log_dir / f"{pod_name}.log"
            log_file.write_text(result.stdout, encoding="utf-8")
            context.logger.log_info(
                f"Captured logs for pod '{pod_name}'"
            )
        else:
            context.logger.log_warning(
                f"Could not capture logs for pod '{pod_name}'"
            )


def capture_label_logs(
    cmd,
    namespace: str,
    label: str,
    dest: Path,
    label_name: str,
    context: ExecutionContext,
) -> None:
    """Capture aggregated logs for all pods matching *label* in *namespace*."""
    result = cmd.kube(
        "logs",
        "--tail=-1",
        "--prefix=true",
        "--all-containers=true",
        "-l", label,
        "--namespace", namespace,
        check=False,
    )
    if result.success and result.stdout.strip():
        dest.write_text(result.stdout, encoding="utf-8")
        context.logger.log_info(
            f"Captured {label_name} logs \u2192 {dest.name}"
        )
    else:
        # Write an empty file so the user knows we tried
        dest.write_text("", encoding="utf-8")
        context.logger.log_info(
            f"No {label_name} pods found (label={label})"
        )


def capture_infrastructure_logs(
    cmd,
    namespace: str,
    log_dir: Path,
    model_label: str | None,
    results_dir: Path,
    context: ExecutionContext,
) -> None:
    """Capture pod status snapshot and infrastructure logs.

    Captures:
    - Pod status (``kubectl get pods -o wide``) \u2192 ``pod_status.txt``
    - Model-serving logs (``llm-d.ai/model=<label>``) \u2192 ``modelserving_pods.log``
    - EPP logs (``inferencepool=<label>-gaie-epp``) \u2192 ``epp_pods.log``
    - IGW logs (``app.kubernetes.io/component=inference-gateway``) \u2192 ``igw_pods.log``
    """
    log_dir.mkdir(parents=True, exist_ok=True)

    # Pod status snapshot
    context.logger.log_info(
        f"Capturing pod status in namespace '{namespace}'..."
    )
    status_result = cmd.kube(
        "get", "pods", "-o", "wide",
        "--namespace", namespace,
        check=False,
    )
    if status_result.success and status_result.stdout:
        status_file = log_dir / "pod_status.txt"
        status_file.write_text(status_result.stdout, encoding="utf-8")
        context.logger.log_info(
            f"Pod status captured to {status_file.name}"
        )

    # Infrastructure logs (require model label)
    if model_label:
        capture_label_logs(
            cmd, namespace,
            f"llm-d.ai/model={model_label}",
            log_dir / "modelserving_pods.log",
            "model-serving", context,
        )
        capture_label_logs(
            cmd, namespace,
            f"inferencepool={model_label}-gaie-epp",
            log_dir / "epp_pods.log",
            "EPP", context,
        )

    # IGW logs (no model label needed)
    capture_label_logs(
        cmd, namespace,
        "app.kubernetes.io/component=inference-gateway",
        log_dir / "igw_pods.log",
        "IGW", context,
    )

    # Process EPP logs if present
    epp_log = log_dir / "epp_pods.log"
    if epp_log.exists() and epp_log.stat().st_size > 0:
        try:
            import subprocess
            script = Path(__file__).resolve().parents[1] / ".." / "workload" / "harnesses" / "process_epp_logs.py"
            if not script.exists():
                # Try installed location
                import shutil
                script_str = shutil.which("process_epp_logs.py")
                if script_str:
                    script = Path(script_str)
            if script.exists():
                context.logger.log_info("Processing EPP logs...")
                result = subprocess.run(
                    ["python3", str(script), str(results_dir), "--visualize"],
                    capture_output=True, text=True, timeout=120,
                )
                if result.returncode == 0:
                    context.logger.log_info("EPP log processing complete")
                else:
                    context.logger.log_warning(
                        f"EPP log processing failed (non-fatal): {result.stderr[:200]}"
                    )
        except Exception as e:
            context.logger.log_warning(f"EPP log processing failed (non-fatal): {e}")
