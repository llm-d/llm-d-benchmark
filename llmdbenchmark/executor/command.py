"""Shell command executor with dry-run, retry, and output capture."""

import json
import logging
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from llmdbenchmark.exceptions.exceptions import ExecutionError
from llmdbenchmark.utilities.kube_helpers import CRASH_STATES


@dataclass
class CommandResult:
    """Result of a shell command execution."""

    command: str
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    dry_run: bool = False
    attempts: int = 1

    @property
    def success(self) -> bool:
        """Return True if the command exited with code 0."""
        return self.exit_code == 0

    def __str__(self) -> str:
        status = "OK" if self.success else f"FAILED (exit={self.exit_code})"
        if self.dry_run:
            status = "DRY-RUN"
        return f"CommandResult({status}): {self.command[:80]}"


class _MinimalLogger:
    """Fallback logger when no external logger is provided."""

    def __init__(self):
        self._log = logging.getLogger("llmdbenchmark.executor.command")

    def set_indent(self, level: int) -> None:  # noqa: D401
        """No-op — indent is only supported by the full logger."""

    def log_info(self, msg, **_kwargs):
        """Log an info message."""
        self._log.info(msg)

    def log_debug(self, msg, **_kwargs):
        """Log a debug message."""
        self._log.debug(msg)

    def log_warning(self, msg, **_kwargs):
        """Log a warning message."""
        self._log.warning(msg)

    def log_error(self, msg, **_kwargs):
        """Log an error message."""
        self._log.error(msg)


class CommandExecutor:
    """Execute kubectl/helm/helmfile with logging, retry, dry-run, and output capture.

    Uses ``oc`` instead of ``kubectl`` when ``openshift=True``.
    """

    def __init__(self, work_dir: Path, dry_run: bool, verbose: bool,
                 logger=None, kubeconfig: str | None = None,
                 kube_context: str | None = None,
                 openshift: bool = False):
        self.work_dir = work_dir
        self.dry_run = dry_run
        self.verbose = verbose
        self.logger = logger or _MinimalLogger()
        self.kubeconfig = kubeconfig
        self.kube_context = kube_context
        self.openshift = openshift
        self._kube_bin = "oc" if openshift else "kubectl"
        self._commands_dir = work_dir / "setup" / "commands"
        self._commands_dir.mkdir(parents=True, exist_ok=True)

    def execute(  # pylint: disable=too-many-arguments
        self,
        cmd: str | list[str],
        attempts: int = 1,
        *,
        fatal: bool = False,
        silent: bool = True,
        delay: int = 10,
        check: bool = True,
    ) -> CommandResult:
        """Run a shell command with optional retry. Raises ExecutionError if fatal and failed."""
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else cmd
        timestamp = int(time.time() * 1e9)

        if self.dry_run:
            return self._handle_dry_run(cmd_str, timestamp)

        self._write_log(f"{timestamp}_command.log",
                        f'---> will execute: "{cmd_str}"')

        exit_code, stdout, stderr = self._run_with_retries(
            cmd_str, attempts, silent, delay
        )

        if exit_code != 0 and check:
            self._handle_failure(cmd_str, exit_code, stdout, stderr, fatal=fatal)

        return CommandResult(
            command=cmd_str,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            attempts=attempts,
        )

    def _handle_dry_run(self, cmd_str: str, timestamp: int) -> CommandResult:
        """Log the command without executing and return a dry-run result."""
        msg = f'---> would have executed the command "{cmd_str}"'
        self.logger.log_info(msg)
        self._write_log(f"{timestamp}_command.log", msg)
        return CommandResult(command=cmd_str, exit_code=0, dry_run=True)

    def _run_with_retries(
        self, cmd_str: str, attempts: int, silent: bool, delay: int
    ) -> tuple[int, str, str]:
        """Execute a command with retry logic, returning (exit_code, stdout, stderr)."""
        exit_code = 1
        stdout = ""
        stderr = ""

        for attempt in range(1, attempts + 1):
            exit_code, stdout, stderr = self._run_once(cmd_str, silent)

            if exit_code == 0:
                break

            if attempt < attempts:
                self.logger.log_warning(
                    f"Command failed (attempt {attempt}/{attempts}), "
                    f"retrying in {delay}s..."
                )
                time.sleep(delay)

        return exit_code, stdout, stderr

    def _run_once(self, cmd_str: str, silent: bool) -> tuple[int, str, str]:
        """Run a single command attempt, returning (exit_code, stdout, stderr)."""
        timestamp = int(time.time() * 1e9)
        try:
            result = subprocess.run(
                cmd_str,
                shell=True,
                capture_output=True,
                text=True,
                check=False,
                executable="/bin/bash",
            )
            self._write_log(f"{timestamp}_stdout.log", result.stdout)
            self._write_log(f"{timestamp}_stderr.log", result.stderr)

            if self.verbose or not silent:
                self._log_output(result.stdout, result.stderr)

            return result.returncode, result.stdout, result.stderr
        except OSError as exc:
            self.logger.log_error(f"Exception executing command: {exc}")
            return 1, "", str(exc)

    def _log_output(self, stdout: str, stderr: str) -> None:
        """Log stdout/stderr if non-empty."""
        if stdout.strip():
            self.logger.log_debug(f"stdout: {stdout.strip()}")
        if stderr.strip():
            self.logger.log_debug(f"stderr: {stderr.strip()}")

    def _write_log(self, filename: str, content: str) -> None:
        """Write content to a log file in the commands directory."""
        (self._commands_dir / filename).write_text(content)

    def _handle_failure(  # pylint: disable=too-many-arguments
        self, cmd_str: str, exit_code: int,
        stdout: str, stderr: str, *, fatal: bool
    ) -> None:
        """Log failure details and optionally raise ExecutionError."""
        self.logger.log_error(f'Command failed: "{cmd_str}"')
        if stdout.strip():
            self.logger.log_error(f"stdout: {stdout.strip()[:500]}")
        if stderr.strip():
            self.logger.log_error(f"stderr: {stderr.strip()[:500]}")

        if fatal:
            raise ExecutionError(
                message=f"Command failed with exit code {exit_code}",
                step="CommandExecutor",
                context={
                    "command": cmd_str,
                    "exit_code": exit_code,
                    "stderr": stderr[:500],
                },
            )

    def _kubeconfig_args(self) -> list[str]:
        """Return ``--kubeconfig`` and ``--context`` flags when configured."""
        parts: list[str] = []
        if self.kubeconfig:
            parts.extend(["--kubeconfig", self.kubeconfig])
        if self.kube_context:
            parts.extend(["--context", self.kube_context])
        return parts

    def kube(
        self, *args: str, namespace: str | None = None, check: bool = True
    ) -> CommandResult:
        """Execute a kubectl/oc command with auto-injected kubeconfig flags."""
        parts = [self._kube_bin]
        parts.extend(self._kubeconfig_args())
        if namespace:
            parts.extend(["--namespace", namespace])
        parts.extend(args)
        return self.execute(" ".join(parts), check=check)

    def helm(self, *args: str, check: bool = True) -> CommandResult:
        """Execute a helm command with auto-injected kubeconfig flags."""
        parts = ["helm"]
        parts.extend(self._kubeconfig_args())
        parts.extend(args)
        return self.execute(" ".join(parts), check=check)

    def helmfile(self, *args: str) -> CommandResult:
        """Execute a helmfile command."""
        parts = ["helmfile"]
        parts.extend(self._kubeconfig_args())
        parts.extend(args)
        return self.execute(" ".join(parts))

    def wait_for_pods(
        self,
        label: str,
        namespace: str,
        timeout: int = 300,
        poll_interval: int = 10,
        description: str = "",
    ) -> CommandResult:
        """Poll pods matching a label selector until all are Ready, showing live progress."""
        desc = description or label
        cmd_repr = (
            f'kubectl wait --for=condition=Ready pod -l {label} '
            f'--namespace {namespace} --timeout={timeout}s'
        )

        if self.dry_run:
            return self._handle_dry_run(cmd_repr, int(time.time() * 1e9))

        start = time.time()
        last_status_line = ""
        ever_found_pods = False

        while True:
            elapsed = time.time() - start
            remaining = max(0, timeout - elapsed)

            if elapsed > timeout:
                self._clear_progress_line(last_status_line)
                if not ever_found_pods:
                    self.logger.log_warning(
                        f"⏱️  No pods found for {desc} after {timeout}s"
                    )
                    return CommandResult(
                        command=cmd_repr, exit_code=1,
                        stderr=f"Timed out after {timeout}s waiting for {desc} — no pods found",
                    )
                self.logger.log_error(
                    f"⏱️  Timed out waiting for {desc} after {timeout}s"
                )
                return CommandResult(
                    command=cmd_repr, exit_code=1,
                    stderr=f"Timed out after {timeout}s waiting for {desc}",
                )

            pods = self._get_pod_statuses(label, namespace)

            if pods is None:
                time.sleep(poll_interval)
                continue

            if len(pods) == 0:
                status_line = self._format_progress(
                    desc, elapsed, timeout,
                    "no pods found yet", 0, 0,
                )
                self._print_progress(status_line, last_status_line)
                last_status_line = status_line
                time.sleep(poll_interval)
                continue

            ever_found_pods = True

            ready_count = sum(1 for p in pods if p["ready"])
            total = len(pods)
            pod_summaries = [
                f'{p["name"][:30]}:{p["status"]}' for p in pods
            ]

            status_line = self._format_progress(
                desc, elapsed, timeout,
                " | ".join(pod_summaries),
                ready_count, total,
            )
            self._print_progress(status_line, last_status_line)
            last_status_line = status_line

            crashing = [
                p for p in pods if p["status"] in CRASH_STATES
            ]
            if crashing:
                self._clear_progress_line(last_status_line)
                crash_details = ", ".join(
                    f'{p["name"][:30]}={p["status"]}' for p in crashing
                )
                self.logger.log_error(
                    f"❌ {desc}: pod(s) in terminal failure state: "
                    f"{crash_details}"
                )
                return CommandResult(
                    command=cmd_repr, exit_code=1,
                    stderr=(
                        f"Pod(s) in terminal failure state: {crash_details}. "
                        f"Aborting wait for {desc}."
                    ),
                )

            if ready_count == total and total > 0:
                self._clear_progress_line(last_status_line)
                self.logger.log_info(
                    f"✅ {desc}: {total}/{total} Ready "
                    f"({self._fmt_elapsed(elapsed)})"
                )
                return CommandResult(command=cmd_repr, exit_code=0)

            time.sleep(poll_interval)

    def wait_for_job(
        self,
        job_name: str,
        namespace: str,
        timeout: int = 3600,
        poll_interval: int = 15,
        description: str = "",
    ) -> CommandResult:
        """Poll a Job until it completes or fails, showing live progress."""
        desc = description or f"job/{job_name}"
        cmd_repr = (
            f'kubectl wait --for=condition=complete job/{job_name} '
            f'--namespace {namespace} --timeout={timeout}s'
        )

        if self.dry_run:
            return self._handle_dry_run(cmd_repr, int(time.time() * 1e9))

        start = time.time()
        last_status_line = ""

        while True:
            elapsed = time.time() - start

            if elapsed > timeout:
                self._clear_progress_line(last_status_line)
                self.logger.log_error(
                    f"⏱️  Timed out waiting for {desc} after {timeout}s"
                )
                return CommandResult(
                    command=cmd_repr, exit_code=1,
                    stderr=f"Timed out after {timeout}s waiting for {desc}",
                )

            job = self._get_job_status(job_name, namespace)

            if job is None:
                status_line = self._format_progress(
                    desc, elapsed, timeout,
                    "job not found — waiting...", 0, 1,
                )
                self._print_progress(status_line, last_status_line)
                last_status_line = status_line
                time.sleep(poll_interval)
                continue

            active = job.get("active", 0)
            succeeded = job.get("succeeded", 0)
            failed = job.get("failed", 0)

            conditions = job.get("conditions", [])
            for cond in conditions:
                if cond.get("type") == "Complete" and cond.get("status") == "True":
                    self._clear_progress_line(last_status_line)
                    self.logger.log_info(
                        f"✅ {desc}: Completed ({self._fmt_elapsed(elapsed)})"
                    )
                    return CommandResult(command=cmd_repr, exit_code=0)
                if cond.get("type") == "Failed" and cond.get("status") == "True":
                    reason = cond.get("reason", "Unknown")
                    self._clear_progress_line(last_status_line)
                    self.logger.log_error(f"❌ {desc}: Failed — {reason}")
                    return CommandResult(
                        command=cmd_repr, exit_code=1,
                        stderr=f"Job failed: {reason}",
                    )

            pods = self._get_pod_statuses(f"job-name={job_name}", namespace)
            pod_info = ""
            if pods:
                pod_info = " | ".join(
                    f'{p["name"][-20:]}:{p["status"]}' for p in pods
                )

            parts = f"active={active} succeeded={succeeded} failed={failed}"
            if pod_info:
                parts += f" | {pod_info}"

            status_line = self._format_progress(
                desc, elapsed, timeout, parts, succeeded, max(1, succeeded + active),
            )
            self._print_progress(status_line, last_status_line)
            last_status_line = status_line

            time.sleep(poll_interval)

    def wait_for_pvc(
        self,
        pvc_name: str,
        namespace: str,
        timeout: int = 300,
        poll_interval: int = 10,
        description: str = "",
    ) -> CommandResult:
        """Poll a PVC until it reaches Bound phase, showing live progress."""
        desc = description or f"pvc/{pvc_name}"
        cmd_repr = f'kubectl wait --for=jsonpath={{.status.phase}}=Bound pvc/{pvc_name} --namespace {namespace} --timeout={timeout}s'

        if self.dry_run:
            return self._handle_dry_run(cmd_repr, int(time.time() * 1e9))

        start = time.time()
        last_status_line = ""

        while True:
            elapsed = time.time() - start

            if elapsed > timeout:
                self._clear_progress_line(last_status_line)
                self.logger.log_error(
                    f"⏱️  Timed out waiting for {desc} after {timeout}s"
                )
                return CommandResult(
                    command=cmd_repr, exit_code=1,
                    stderr=f"Timed out after {timeout}s waiting for {desc}",
                )

            parts = [self._kube_bin]
            parts.extend(self._kubeconfig_args())
            parts.extend([
                "get", "pvc", pvc_name,
                "--namespace", namespace,
                "-o", "jsonpath={.status.phase}:{.spec.storageClassName}",
            ])
            try:
                result = subprocess.run(
                    " ".join(parts), shell=True, capture_output=True,
                    text=True, check=False, executable="/bin/bash",
                )
                output = result.stdout.strip()
                pvc_parts = output.split(":", 1)
                phase = pvc_parts[0] if pvc_parts else "Unknown"
                sc = pvc_parts[1] if len(pvc_parts) > 1 else ""

                if phase == "Bound":
                    self._clear_progress_line(last_status_line)
                    sc_info = f" (storageClass={sc})" if sc else ""
                    self.logger.log_info(
                        f"✅ {desc}: Bound{sc_info} ({self._fmt_elapsed(elapsed)})"
                    )
                    return CommandResult(command=cmd_repr, exit_code=0)

                sc_info = f" sc={sc}" if sc else " sc=cluster-default"
                status_line = self._format_progress(
                    desc, elapsed, timeout,
                    f"{phase}{sc_info}", 0, 1,
                )
            except (OSError, KeyError, ValueError):
                status_line = self._format_progress(
                    desc, elapsed, timeout, "querying...", 0, 1,
                )

            self._print_progress(status_line, last_status_line)
            last_status_line = status_line
            time.sleep(poll_interval)

    def _get_pod_statuses(
        self, label: str, namespace: str
    ) -> list[dict] | None:
        """Query pod statuses via kubectl/oc get pods -o json."""
        parts = [self._kube_bin]
        parts.extend(self._kubeconfig_args())
        parts.extend([
            "get", "pods", "-l", label,
            "--namespace", namespace,
            "-o", "json",
        ])
        try:
            result = subprocess.run(
                " ".join(parts), shell=True, capture_output=True,
                text=True, check=False, executable="/bin/bash",
            )
            if result.returncode != 0:
                return None

            data = json.loads(result.stdout)
            pods = []
            for item in data.get("items", []):
                name = item.get("metadata", {}).get("name", "?")
                phase = item.get("status", {}).get("phase", "Unknown")

                status = phase
                ready = False
                container_statuses = item.get("status", {}).get(
                    "containerStatuses", []
                )
                if container_statuses:
                    cs = container_statuses[0]
                    if cs.get("ready"):
                        ready = True
                        status = "Ready"
                    elif cs.get("state", {}).get("waiting"):
                        status = cs["state"]["waiting"].get(
                            "reason", "Waiting"
                        )
                    elif cs.get("state", {}).get("terminated"):
                        status = cs["state"]["terminated"].get(
                            "reason", "Terminated"
                        )
                elif phase == "Pending":
                    conditions = item.get("status", {}).get("conditions", [])
                    for cond in conditions:
                        if (
                            cond.get("type") == "PodScheduled"
                            and cond.get("status") == "False"
                        ):
                            reason = cond.get("reason", "Unschedulable")
                            status = reason
                            break

                pods.append({
                    "name": name,
                    "status": status,
                    "ready": ready,
                    "phase": phase,
                })
            return pods
        except (json.JSONDecodeError, OSError):
            return None

    def _get_job_status(self, job_name: str, namespace: str) -> dict | None:
        """Query job status via kubectl/oc get job -o json."""
        parts = [self._kube_bin]
        parts.extend(self._kubeconfig_args())
        parts.extend([
            "get", "job", job_name,
            "--namespace", namespace,
            "-o", "json",
        ])
        try:
            result = subprocess.run(
                " ".join(parts), shell=True, capture_output=True,
                text=True, check=False, executable="/bin/bash",
            )
            if result.returncode != 0:
                return None
            data = json.loads(result.stdout)
            return data.get("status", {})
        except (json.JSONDecodeError, OSError):
            return None

    @staticmethod
    def _format_progress(
        desc: str, elapsed: float, timeout: float,
        detail: str, done: int, total: int,
    ) -> str:
        """Format a progress status line."""
        elapsed_str = CommandExecutor._fmt_elapsed(elapsed)
        timeout_str = CommandExecutor._fmt_elapsed(timeout)

        bar_width = 20
        if total > 0:
            filled = int(bar_width * done / total)
        else:
            filled = 0
        bar = "█" * filled + "░" * (bar_width - filled)

        if total > 0:
            count_str = f"{done}/{total}"
        else:
            count_str = "—"

        return (
            f"  ⏳ [{elapsed_str}/{timeout_str}] {desc}: "
            f"[{bar}] {count_str} | {detail}"
        )

    @staticmethod
    def _fmt_elapsed(seconds: float) -> str:
        """Format seconds as MM:SS."""
        m, s = divmod(int(seconds), 60)
        return f"{m:02d}:{s:02d}"

    @staticmethod
    def _print_progress(line: str, prev_line: str) -> None:
        """Print a progress line, overwriting the previous one."""
        if prev_line:
            sys.stderr.write("\r\033[2K")
        sys.stderr.write(line)
        sys.stderr.flush()

    @staticmethod
    def _clear_progress_line(prev_line: str) -> None:
        """Clear the progress line from the terminal."""
        if prev_line:
            sys.stderr.write("\r\033[2K")
            sys.stderr.flush()
