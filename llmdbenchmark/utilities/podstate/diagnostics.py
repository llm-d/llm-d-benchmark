"""Evidence capture and reporting for unhealthy pods.

Restarting a pod destroys the evidence of why it failed: once the pod object
is gone, so are its logs and its events.  Anything that deletes a pod as a
remediation should capture that evidence first, or the standup that needed
three restarts becomes undebuggable after the fact.

Every function here is best-effort by construction: diagnostics must never be
the reason a phase fails.
"""

from __future__ import annotations

from pathlib import Path

from llmdbenchmark.utilities.podstate.policy import RestartBudget
from llmdbenchmark.utilities.podstate.state import PodState

# Bounded so a chatty crash-looping container cannot fill the workspace.
DEFAULT_LOG_TAIL = 2000

EVIDENCE_DIRNAME = "pod-restarts"


def _write(dest: Path, content: str) -> Path | None:
    """Write *content*, swallowing filesystem errors."""
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
        return dest
    except OSError:
        return None


def capture_pod_evidence(
    cmd,
    pod: PodState,
    dest_dir: Path,
    *,
    prefix: str = "",
    log_tail: int = DEFAULT_LOG_TAIL,
    logger=None,
) -> list[Path]:
    """Snapshot a pod's description, logs, and events before it is deleted.

    Returns the files actually written.  Never raises.
    """
    written: list[Path] = []
    stem = f"{prefix}{pod.name}" if prefix else pod.name
    namespace = pod.namespace

    probes = (
        ("describe", ("describe", "pod", pod.name)),
        (
            "logs",
            ("logs", pod.name, "--all-containers=true", f"--tail={log_tail}"),
        ),
        (
            "previous",
            (
                "logs",
                pod.name,
                "--all-containers=true",
                "--previous",
                f"--tail={log_tail}",
            ),
        ),
        (
            "events",
            (
                "get",
                "events",
                "--field-selector",
                f"involvedObject.name={pod.name}",
                "--sort-by=.lastTimestamp",
            ),
        ),
    )

    for kind, args in probes:
        try:
            result = cmd.kube(*args, namespace=namespace, check=False)
        except Exception:  # pylint: disable=broad-except
            continue
        if not getattr(result, "success", False):
            continue
        output = (result.stdout or "").strip()
        if not output:
            continue
        path = _write(dest_dir / f"{stem}-{kind}.txt", output)
        if path is not None:
            written.append(path)

    if written and logger is not None:
        logger.log_info(
            f"   Captured {len(written)} diagnostic file(s) for {pod.name} "
            f"-> {dest_dir}"
        )

    return written


def evidence_dir(work_dir: Path) -> Path:
    """Directory where restart evidence is stored for a workspace."""
    return Path(work_dir) / "setup" / "logs" / EVIDENCE_DIRNAME


def render_restart_summary(budget: RestartBudget) -> list[str]:
    """Lines describing what the budget was spent on ([] when untouched).

    A standup that only converged after deleting pods must not read the same
    as one that came up clean, so this is surfaced even on success.
    """
    if not budget.events:
        return []

    lines = [
        f"Pod restart budget: {budget.status()} consumed during this phase",
    ]
    lines.extend(budget.summary_lines())
    if budget.exhausted:
        lines.append(
            "  Budget is now exhausted -- further pod failures will fail the phase."
        )
    return lines
