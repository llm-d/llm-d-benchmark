"""Shell command executor with dry-run, retry, and output capture."""

import json
import logging
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from llmdbenchmark.exceptions.exceptions import ExecutionError
from llmdbenchmark.utilities.podstate import (
    PodState,
    RestartBudget,
    RestartBudgetPolicy,
    Verdict,
    WaitContext,
    capture_pod_evidence,
    evidence_dir,
    parse_pod_list,
)


@dataclass
class CommandResult:
    """Result of a shell command execution."""

    command: str
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    dry_run: bool = False
    attempts: int = 1
    # True when a readiness wait was deliberately not performed, so the
    # caller owns the deferred budget (see CommandExecutor.wait_for_pvc).
    wait_skipped: bool = False

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
        """No-op -- indent is only supported by the full logger."""

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

    def __init__(
        self,
        work_dir: Path,
        dry_run: bool,
        verbose: bool,
        logger=None,
        kubeconfig: str | None = None,
        kube_context: str | None = None,
        openshift: bool = False,
        pod_restart_budget: RestartBudget | None = None,
        pod_restart_grace: float = 300.0,
    ):
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
        # Owned by the caller, not built here: this executor is rebuilt
        # mid-phase (after cluster/OpenShift detection), and a budget created
        # here would silently reset its counter on every rebuild.
        self.pod_restart_budget = pod_restart_budget
        self._pod_policies: list = []
        if pod_restart_budget is not None and pod_restart_budget.enabled:
            self._pod_policies.append(
                RestartBudgetPolicy(
                    budget=pod_restart_budget,
                    grace_seconds=pod_restart_grace,
                )
            )

    def execute(  # pylint: disable=too-many-arguments
        self,
        cmd: str | list[str],
        attempts: int = 1,
        *,
        fatal: bool = False,
        silent: bool = True,
        delay: int = 10,
        check: bool = True,
        force: bool = False,
        stdin: str = "",
    ) -> CommandResult:
        """Run a shell command with optional retry. Raises ExecutionError if fatal and failed.

        When *force* is True the command runs even in dry-run mode.
        Use this for local-only read operations (e.g. ``kubectl config view``)
        whose results are needed to build later commands correctly.

        *stdin* is written to the process's standard input and, unlike the
        command itself, is **never logged** -- it is the channel for values that
        must not be persisted (a Hugging Face token forwarded to a remote
        runtime). It is not replayed on retries other than as given.
        """
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else cmd
        timestamp = int(time.time() * 1e9)

        if self.dry_run and not force:
            return self._handle_dry_run(cmd_str, timestamp)

        self._write_log(f"{timestamp}_command.log", f'---> will execute: "{cmd_str}"')

        exit_code, stdout, stderr = self._run_with_retries(
            cmd_str, attempts, silent, delay, stdin
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
        self, cmd_str: str, attempts: int, silent: bool, delay: int, stdin: str = ""
    ) -> tuple[int, str, str]:
        """Execute a command with retry logic, returning (exit_code, stdout, stderr)."""
        exit_code = 1
        stdout = ""
        stderr = ""

        for attempt in range(1, attempts + 1):
            exit_code, stdout, stderr = self._run_once(cmd_str, silent, stdin)

            if exit_code == 0:
                break

            if attempt < attempts:
                self.logger.log_warning(
                    f"Command failed (attempt {attempt}/{attempts}), "
                    f"retrying in {delay}s..."
                )
                time.sleep(delay)

        return exit_code, stdout, stderr

    def _run_once(
        self, cmd_str: str, silent: bool, stdin: str = ""
    ) -> tuple[int, str, str]:
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
                # None, not "": an empty string would close stdin on commands
                # that inherit it today, changing behaviour for every caller.
                input=stdin or None,
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
        self, cmd_str: str, exit_code: int, stdout: str, stderr: str, *, fatal: bool
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
        self,
        *args: str,
        namespace: str | None = None,
        check: bool = True,
        force: bool = False,
    ) -> CommandResult:
        """Execute a kubectl/oc command with auto-injected kubeconfig flags.

        When *force* is True the command runs even in dry-run mode.
        Use for local-only reads like ``config view``.
        """
        parts = [self._kube_bin]
        parts.extend(self._kubeconfig_args())
        if namespace:
            parts.extend(["--namespace", namespace])
        parts.extend(args)
        return self.execute(" ".join(parts), check=check, force=force)

    def helm(self, *args: str, check: bool = True) -> CommandResult:
        """Execute a helm command with auto-injected kubeconfig flags."""
        parts = ["helm"]
        parts.extend(self._kubeconfig_args())
        parts.extend(args)
        return self.execute(" ".join(parts), check=check)

    def helmfile(self, *args: str, use_kubeconfig: bool = True) -> CommandResult:
        """Execute a helmfile command.

        Args:
            *args: helmfile arguments
            use_kubeconfig: When True (default), injects --kubeconfig from
                the stored context. Set to False for gateway provider
                installs that need helmfile to resolve release namespaces
                from the helmfile itself (e.g., istio-system), not from
                the kubeconfig context namespace. When False, the stored
                kubeconfig path is exported as KUBECONFIG env var so helm
                can still reach the cluster.
        """
        parts = []
        if not use_kubeconfig and self.kubeconfig:
            # Export KUBECONFIG env var so helm/helmfile can find the
            # cluster without injecting --kubeconfig (which would set
            # the namespace context and break helmfile 'needs:' resolution).
            parts.append(f"KUBECONFIG={self.kubeconfig}")
        parts.append("helmfile")
        if use_kubeconfig:
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
        """Poll pods matching a label selector until all are Ready, showing live progress.

        When a restart budget is configured (``--pod-restart-budget``), pods
        that fail in a way a restart may clear are deleted and given another
        chance instead of aborting the wait outright. Failures a restart
        cannot clear (a bad image reference, a broken config) still fail fast.
        """
        desc = description or label
        kc_args = " ".join(self._kubeconfig_args())
        cmd_repr = (
            f"{self._kube_bin} {kc_args} wait --for=condition=Ready pod -l {label} "
            f"--namespace {namespace} --timeout={timeout}s"
        ).replace("  ", " ")

        if self.dry_run:
            return self._handle_dry_run(cmd_repr, int(time.time() * 1e9))

        start = time.time()
        last_status_line = ""
        ever_found_pods = False
        # Extended by the restart policy: a replacement pod re-pulls its image
        # and reloads the model from zero, so it needs budget of its own.
        deadline = float(timeout)

        while True:
            elapsed = time.time() - start

            if elapsed > deadline:
                self._clear_progress_line(last_status_line)
                budget_note = self._restart_budget_note()
                if not ever_found_pods:
                    self.logger.log_warning(
                        f"⏱️  No pods found for {desc} after {int(deadline)}s"
                    )
                    return CommandResult(
                        command=cmd_repr,
                        exit_code=1,
                        stderr=(
                            f"Timed out after {int(deadline)}s waiting for {desc} "
                            f"-- no pods found{budget_note}"
                        ),
                    )
                self.logger.log_error(
                    f"⏱️  Timed out waiting for {desc} after {int(deadline)}s"
                )
                return CommandResult(
                    command=cmd_repr,
                    exit_code=1,
                    stderr=f"Timed out after {int(deadline)}s waiting for {desc}{budget_note}",
                )

            pods = self._observe_pods(label, namespace)

            if pods is None:
                time.sleep(poll_interval)
                continue

            if len(pods) == 0:
                status_line = self._format_progress(
                    desc,
                    elapsed,
                    deadline,
                    "no pods found yet",
                    0,
                    0,
                )
                self._print_progress(status_line, last_status_line)
                last_status_line = status_line
                time.sleep(poll_interval)
                continue

            ever_found_pods = True

            ready_count = sum(1 for p in pods if p.ready)
            total = len(pods)
            pod_summaries = [f"{p.name[:30]}:{p.summary}" for p in pods]

            status_line = self._format_progress(
                desc,
                elapsed,
                deadline,
                " | ".join(pod_summaries),
                ready_count,
                total,
            )
            self._print_progress(status_line, last_status_line)
            last_status_line = status_line

            remedy = self._apply_pod_policies(
                pods,
                WaitContext(
                    description=desc,
                    namespace=namespace,
                    elapsed=elapsed,
                    timeout=deadline,
                ),
                last_status_line,
            )
            if remedy is not None:
                if remedy.verdict is Verdict.ABORT:
                    self._clear_progress_line(last_status_line)
                    return CommandResult(
                        command=cmd_repr,
                        exit_code=1,
                        stderr=remedy.message or f"Aborting wait for {desc}",
                    )
                if remedy.extend_deadline:
                    deadline += remedy.extend_deadline
                if remedy.message or remedy.delete_pods:
                    # The policy logged something, so the progress line was
                    # cleared; a silent remedy leaves it on screen to be
                    # overwritten by the next tick.
                    last_status_line = ""
                time.sleep(poll_interval)
                continue

            crashing = [p for p in pods if p.crashing]
            if crashing:
                self._clear_progress_line(last_status_line)
                crash_details = ", ".join(
                    f"{p.name[:30]}={p.summary}" for p in crashing
                )
                self.logger.log_error(
                    f"❌ {desc}: pod(s) in terminal failure state: {crash_details}"
                )
                return CommandResult(
                    command=cmd_repr,
                    exit_code=1,
                    stderr=(
                        f"Pod(s) in terminal failure state: {crash_details}. "
                        f"Aborting wait for {desc}.{self._restart_budget_note()}"
                    ),
                )

            if ready_count == total and total > 0:
                self._clear_progress_line(last_status_line)
                self.logger.log_info(
                    f"✅ {desc}: {total}/{total} Ready ({self._fmt_elapsed(elapsed)})"
                )
                return CommandResult(command=cmd_repr, exit_code=0)

            time.sleep(poll_interval)

    def _restart_budget_note(self) -> str:
        """Suffix explaining budget state, for failure messages ('' when unused)."""
        budget = self.pod_restart_budget
        if budget is None or not budget.enabled:
            return ""
        if budget.exhausted:
            return (
                f" Pod restart budget exhausted ({budget.status()}); "
                "raise --pod-restart-budget to allow more restart attempts."
            )
        return f" Pod restart budget used: {budget.status()}."

    def _apply_pod_policies(
        self,
        pods: list[PodState],
        ctx: WaitContext,
        last_status_line: str,
    ):
        """Run the configured policies and perform the side effects they ask for.

        Returns the Remedy that was acted on, or ``None`` when no policy had
        anything to say and the caller should apply its normal rules.
        """
        for policy in self._pod_policies:
            remedy = policy.observe(pods, ctx)
            if remedy is None:
                continue

            granted = getattr(policy, "take_granted", lambda: [])()
            if not remedy.message and not granted:
                # A silent "keep waiting" (e.g. a replacement pod that has not
                # appeared yet): leave the progress line alone.
                return remedy

            self._clear_progress_line(last_status_line)
            if remedy.message:
                self.logger.log_warning(f"♻️  {ctx.description}: {remedy.message}")

            for grant in granted:
                self._capture_and_delete(grant.pod, ctx, grant.event.sequence)

            if remedy.extend_deadline:
                self.logger.log_info(
                    f"   Extending wait for {ctx.description} by "
                    f"{int(remedy.extend_deadline)}s to cover the restart"
                )
            return remedy
        return None

    def _capture_and_delete(
        self, pod: PodState, ctx: WaitContext, sequence: int
    ) -> None:
        """Snapshot a failing pod's diagnostics, then delete it.

        Evidence first: once the pod object is gone its logs and events go with
        it, and a standup that silently restarted pods would be undebuggable.
        """
        try:
            capture_pod_evidence(
                self,
                pod,
                evidence_dir(self.work_dir),
                prefix=f"{sequence:02d}-",
                logger=self.logger,
            )
        except Exception as exc:  # pylint: disable=broad-except
            self.logger.log_warning(
                f"Could not capture diagnostics for pod '{pod.name}': {exc}"
            )

        result = self.kube(
            "delete",
            "pod",
            pod.name,
            "--namespace",
            pod.namespace or ctx.namespace,
            "--ignore-not-found",
            "--wait=false",
            check=False,
        )
        if result.success:
            self.logger.log_info(f"   Deleted pod '{pod.name}' -- awaiting replacement")
        else:
            self.logger.log_warning(
                f"Could not delete pod '{pod.name}': {result.stderr}"
            )

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
        kc_args = " ".join(self._kubeconfig_args())
        cmd_repr = (
            f"{self._kube_bin} {kc_args} wait --for=condition=complete job/{job_name} "
            f"--namespace {namespace} --timeout={timeout}s"
        ).replace("  ", " ")

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
                    command=cmd_repr,
                    exit_code=1,
                    stderr=f"Timed out after {timeout}s waiting for {desc}",
                )

            job = self._get_job_status(job_name, namespace)

            if job is None:
                status_line = self._format_progress(
                    desc,
                    elapsed,
                    timeout,
                    "job not found -- waiting...",
                    0,
                    1,
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
                    self.logger.log_error(f"❌ {desc}: Failed -- {reason}")
                    return CommandResult(
                        command=cmd_repr,
                        exit_code=1,
                        stderr=f"Job failed: {reason}",
                    )

            pods = self._get_pod_statuses(f"job-name={job_name}", namespace)
            pod_info = ""
            if pods:
                pod_info = " | ".join(f"{p['name'][-20:]}:{p['status']}" for p in pods)

            parts = f"active={active} succeeded={succeeded} failed={failed}"
            if pod_info:
                parts += f" | {pod_info}"

            status_line = self._format_progress(
                desc,
                elapsed,
                timeout,
                parts,
                succeeded,
                max(1, succeeded + active),
            )
            self._print_progress(status_line, last_status_line)
            last_status_line = status_line

            time.sleep(poll_interval)

    def wait_for_daemonset(
        self,
        ds_name: str,
        namespace: str,
        timeout: int = 3600,
        poll_interval: int = 15,
        description: str = "",
    ) -> CommandResult:
        """Poll a DaemonSet until all pods are ready, showing live progress.

        Succeeds when status.numberReady >= status.desiredNumberScheduled
        and desiredNumberScheduled > 0.
        """
        desc = description or f"daemonset/{ds_name}"
        kc_args = " ".join(self._kubeconfig_args())
        cmd_repr = (
            f"{self._kube_bin} {kc_args} rollout status daemonset/{ds_name} "
            f"--namespace {namespace} --timeout={timeout}s"
        ).replace("  ", " ")

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
                    command=cmd_repr,
                    exit_code=1,
                    stderr=f"Timed out after {timeout}s waiting for {desc}",
                )

            ds_status = self._get_daemonset_status(ds_name, namespace)

            if ds_status is None:
                status_line = self._format_progress(
                    desc,
                    elapsed,
                    timeout,
                    "daemonset not found -- waiting...",
                    0,
                    1,
                )
                self._print_progress(status_line, last_status_line)
                last_status_line = status_line
                time.sleep(poll_interval)
                continue

            desired = ds_status.get("desiredNumberScheduled", 0)
            ready = ds_status.get("numberReady", 0)
            available = ds_status.get("numberAvailable", 0)
            updated = ds_status.get("updatedNumberScheduled", 0)

            if desired > 0 and ready >= desired:
                self._clear_progress_line(last_status_line)
                self.logger.log_info(
                    f"✅ {desc}: {ready}/{desired} Ready ({self._fmt_elapsed(elapsed)})"
                )
                return CommandResult(command=cmd_repr, exit_code=0)

            parts = (
                f"desired={desired} ready={ready} "
                f"available={available} updated={updated}"
            )
            status_line = self._format_progress(
                desc,
                elapsed,
                timeout,
                parts,
                ready,
                max(1, desired),
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
        """Poll a PVC until it reaches Bound phase, showing live progress.

        Short-circuits to success when the resolved StorageClass uses
        ``volumeBindingMode: WaitForFirstConsumer`` (e.g. Kind's local-path
        provisioner). Such PVCs intentionally stay ``Pending`` until a
        consumer pod is scheduled, so blocking on Bound here would deadlock
        standup before the consumer pod ever gets a chance to apply. Real
        provisioning failures still surface as a pod-readiness or
        download-job timeout downstream. When that happens the returned
        result has ``wait_skipped=True``: the caller's next wait now covers
        volume provisioning on top of whatever it originally waited for, so
        a caller whose next wait is tighter than this ``timeout`` should add
        this ``timeout`` to it (step 04's callers already wait far longer,
        so they need no adjustment; step 05's data-access pod wait does).
        """
        desc = description or f"pvc/{pvc_name}"
        kc_args = " ".join(self._kubeconfig_args())
        cmd_repr = f"{self._kube_bin} {kc_args} wait --for=jsonpath={{.status.phase}}=Bound pvc/{pvc_name} --namespace {namespace} --timeout={timeout}s".replace(
            "  ", " "
        )

        if self.dry_run:
            return self._handle_dry_run(cmd_repr, int(time.time() * 1e9))

        binding_mode = self._resolve_pvc_binding_mode(pvc_name, namespace)
        if binding_mode == "WaitForFirstConsumer":
            self.logger.log_info(
                f"⏭️  {desc}: StorageClass uses WaitForFirstConsumer "
                "-- PVC will bind when its consumer pod schedules; "
                "skipping bind wait."
            )
            return CommandResult(command=cmd_repr, exit_code=0, wait_skipped=True)

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
                    command=cmd_repr,
                    exit_code=1,
                    stderr=f"Timed out after {timeout}s waiting for {desc}",
                )

            parts = [self._kube_bin]
            parts.extend(self._kubeconfig_args())
            parts.extend(
                [
                    "get",
                    "pvc",
                    pvc_name,
                    "--namespace",
                    namespace,
                    "-o",
                    "jsonpath={.status.phase}:{.spec.storageClassName}",
                ]
            )
            try:
                result = subprocess.run(
                    " ".join(parts),
                    shell=True,
                    capture_output=True,
                    text=True,
                    check=False,
                    executable="/bin/bash",
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
                    desc,
                    elapsed,
                    timeout,
                    f"{phase}{sc_info}",
                    0,
                    1,
                )
            except (OSError, KeyError, ValueError):
                status_line = self._format_progress(
                    desc,
                    elapsed,
                    timeout,
                    "querying...",
                    0,
                    1,
                )

            self._print_progress(status_line, last_status_line)
            last_status_line = status_line
            time.sleep(poll_interval)

    def _resolve_pvc_binding_mode(self, pvc_name: str, namespace: str) -> str | None:
        """Return the volumeBindingMode of the StorageClass that backs *pvc_name*.

        Reads the PVC's ``spec.storageClassName`` (i.e. exactly what the
        scenario config rendered into the manifest) and queries that
        class's ``.volumeBindingMode``. Returns ``None`` when the PVC has
        no explicit storageClassName -- in that case the caller falls
        through to a normal Bound wait, which will fail with a clear hint
        telling the user to set storageClassName explicitly rather than
        rely on cluster defaults.
        """
        sc_name = self._jsonpath(
            ["get", "pvc", pvc_name, "--namespace", namespace],
            "{.spec.storageClassName}",
        )
        if not sc_name:
            return None

        mode = self._jsonpath(
            ["get", "storageclass", sc_name],
            "{.volumeBindingMode}",
        )
        return mode or "Immediate"

    def _jsonpath(self, kube_args: list[str], jsonpath: str) -> str:
        """Run a kubectl/oc query and return the trimmed jsonpath output."""
        parts = [self._kube_bin]
        parts.extend(self._kubeconfig_args())
        parts.extend(kube_args)
        # Single-quote the jsonpath so shell=True doesn't eat the
        # backslashes used to escape dots in annotation keys.
        parts.extend(["-o", f"'jsonpath={jsonpath}'"])
        try:
            result = subprocess.run(
                " ".join(parts),
                shell=True,
                capture_output=True,
                text=True,
                check=False,
                executable="/bin/bash",
            )
            if result.returncode != 0:
                return ""
            return result.stdout.strip()
        except OSError:
            return ""

    def _observe_pods(self, label: str, namespace: str) -> list[PodState] | None:
        """Query pods matching *label* and return their structured state.

        Returns ``None`` when the query itself failed, so a poll loop can tell
        an apiserver hiccup apart from "the deployment has no pods".

        Deliberately bypasses :meth:`execute`: this runs on every poll tick and
        would otherwise write a log file per query.
        """
        parts = [self._kube_bin]
        parts.extend(self._kubeconfig_args())
        parts.extend(
            [
                "get",
                "pods",
                "-l",
                label,
                "--namespace",
                namespace,
                "-o",
                "json",
            ]
        )
        try:
            result = subprocess.run(
                " ".join(parts),
                shell=True,
                capture_output=True,
                text=True,
                check=False,
                executable="/bin/bash",
            )
            if result.returncode != 0:
                return None
            return parse_pod_list(result.stdout, namespace=namespace)
        except OSError:
            return None

    def _get_pod_statuses(self, label: str, namespace: str) -> list[dict] | None:
        """Query pod statuses as plain dicts (name/status/ready/phase)."""
        pods = self._observe_pods(label, namespace)
        if pods is None:
            return None
        return [
            {
                "name": pod.name,
                "status": pod.summary,
                "ready": pod.ready,
                "phase": pod.phase,
            }
            for pod in pods
        ]

    def _get_job_status(self, job_name: str, namespace: str) -> dict | None:
        """Query job status via kubectl/oc get job -o json."""
        parts = [self._kube_bin]
        parts.extend(self._kubeconfig_args())
        parts.extend(
            [
                "get",
                "job",
                job_name,
                "--namespace",
                namespace,
                "-o",
                "json",
            ]
        )
        try:
            result = subprocess.run(
                " ".join(parts),
                shell=True,
                capture_output=True,
                text=True,
                check=False,
                executable="/bin/bash",
            )
            if result.returncode != 0:
                return None
            data = json.loads(result.stdout)
            return data.get("status", {})
        except (json.JSONDecodeError, OSError):
            return None

    def _get_daemonset_status(self, ds_name: str, namespace: str) -> dict | None:
        """Query DaemonSet status via kubectl/oc get daemonset -o json."""
        parts = [self._kube_bin]
        parts.extend(self._kubeconfig_args())
        parts.extend(
            [
                "get",
                "daemonset",
                ds_name,
                "--namespace",
                namespace,
                "-o",
                "json",
            ]
        )
        try:
            result = subprocess.run(
                " ".join(parts),
                shell=True,
                capture_output=True,
                text=True,
                check=False,
                executable="/bin/bash",
            )
            if result.returncode != 0:
                return None
            data = json.loads(result.stdout)
            return data.get("status", {})
        except (json.JSONDecodeError, OSError):
            return None

    @staticmethod
    def _format_progress(
        desc: str,
        elapsed: float,
        timeout: float,
        detail: str,
        done: int,
        total: int,
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
            count_str = "--"

        return (
            f"  ⏳ [{elapsed_str}/{timeout_str}] {desc}: [{bar}] {count_str} | {detail}"
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
