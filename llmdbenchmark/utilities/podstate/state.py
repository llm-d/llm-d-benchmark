"""Structured pod state derived from the Kubernetes API.

Single source of truth for the question "what is this pod doing, and can it
recover?".  Before this module the answer was re-derived in four places from
raw ``kubectl get pods -o json`` payloads (the executor's poll loop, the
harness wait helper, the WVA validator, the smoketest phase check), each
covering a slightly different slice and disagreeing on the edges.

The important distinction this model adds over a flat "is it crashing?" set is
:class:`Health`: a pod stuck on ``ImagePullBackOff`` and a pod stuck on
``CrashLoopBackOff`` are both broken, but only the second one can plausibly be
fixed by deleting it and letting its controller build a new one.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

# Container/pod states that a restart may plausibly clear: a bad roll of the
# dice on startup, a transient OOM, a dependency that was not up yet.
DEGRADED_STATES = frozenset(
    {
        "CrashLoopBackOff",
        "Error",
        "OOMKilled",
    }
)

# States that describe a broken *specification* rather than a broken run.
# Deleting the pod produces an identical pod that fails identically, so these
# fail fast instead of consuming a restart budget.
TERMINAL_STATES = frozenset(
    {
        "CreateContainerConfigError",
        "ImagePullBackOff",
        "ErrImagePull",
        "InvalidImageName",
    }
)

# Kept as the historical flat set: every state that means "this pod will not
# become Ready on its own". Imported by kube_helpers for backwards
# compatibility with existing callers.
CRASH_STATES = frozenset(DEGRADED_STATES | TERMINAL_STATES)


class Health(Enum):
    """Graded verdict on a pod, ordered from good to unrecoverable."""

    HEALTHY = "healthy"  # every container Ready
    STARTING = "starting"  # not Ready, but no failure signal yet
    DEGRADED = "degraded"  # failing in a way a restart may clear
    TERMINAL = "terminal"  # failing in a way a restart cannot clear


class ContainerKind(Enum):
    """Which of a pod's three container lists a status came from."""

    INIT = "init"
    APP = "app"
    EPHEMERAL = "ephemeral"


@dataclass(frozen=True)
class OwnerRef:
    """The controller that owns a pod (Deployment/ReplicaSet/StatefulSet/...)."""

    kind: str
    name: str


@dataclass(frozen=True)
class Termination:
    """A container's terminated state, current or previous."""

    reason: str = ""
    exit_code: int | None = None

    def describe(self, prefix: str = "") -> str:
        """Format for a user-facing failure line."""
        detail = f"{prefix}{self.reason or 'unknown reason'}"
        if self.exit_code is not None:
            detail += f", exit_code={self.exit_code}"
        return detail


@dataclass(frozen=True)
class ContainerState:
    """One container's status, flattened from the API's nested state union."""

    name: str
    kind: ContainerKind = ContainerKind.APP
    ready: bool = False
    restart_count: int = 0
    waiting_reason: str = ""
    terminated: Termination | None = None
    last_terminated: Termination | None = None

    @property
    def terminated_reason(self) -> str:
        """Reason from the current terminated state ('' when not terminated)."""
        return self.terminated.reason if self.terminated else ""

    @property
    def reason(self) -> str:
        """Best single-token description of why this container is not Ready."""
        return self.waiting_reason or self.terminated_reason

    @property
    def failure_details(self) -> list[str]:
        """Crash descriptions for this container, empty when it looks fine.

        A container counts as failing when it is waiting on a crash state, or
        when it terminated either with a crash reason or a non-zero exit code.
        """
        details: list[str] = []

        if self.waiting_reason in CRASH_STATES:
            details.append(self.waiting_reason)

        if self.terminated is not None and (
            self.terminated.reason in CRASH_STATES
            or (
                self.terminated.exit_code is not None and self.terminated.exit_code != 0
            )
        ):
            details.append(self.terminated.describe("terminated: "))

        if not details:
            return []

        if self.last_terminated is not None:
            details.append(self.last_terminated.describe("last terminated: "))

        return details


def _parse_termination(state: dict, key: str) -> Termination | None:
    """Build a Termination from ``state[key]`` when present."""
    raw = state.get(key)
    if not raw:
        return None
    return Termination(
        reason=raw.get("reason") or "",
        exit_code=raw.get("exitCode"),
    )


def _parse_container(status: dict, kind: ContainerKind) -> ContainerState:
    """Flatten one entry of a *ContainerStatuses list."""
    state = status.get("state") or {}
    waiting = state.get("waiting") or {}
    last_state = status.get("lastState") or {}

    return ContainerState(
        name=status.get("name", "unknown-container"),
        kind=kind,
        ready=bool(status.get("ready", False)),
        restart_count=int(status.get("restartCount", 0) or 0),
        waiting_reason=waiting.get("reason") or "",
        terminated=_parse_termination(state, "terminated"),
        last_terminated=_parse_termination(last_state, "terminated"),
    )


def summarize_container_states(not_ready: list[ContainerState]) -> str:
    """Return the 'worst' state among *not_ready* containers.

    Priority: terminated with a crash reason > waiting with a crash reason >
    any reason at all > ``NotReady``.  Pushing crash reasons to the front means
    a CrashLoopBackOff on one container of a multi-container pod surfaces
    immediately instead of being masked by a merely-Waiting sibling.
    """
    if not not_ready:
        return "NotReady"

    for container in not_ready:
        if container.terminated_reason in CRASH_STATES:
            return container.terminated_reason

    for container in not_ready:
        if container.waiting_reason in CRASH_STATES:
            return container.waiting_reason

    for container in not_ready:
        if container.reason:
            return container.reason

    return "NotReady"


@dataclass(frozen=True)
class PodState:
    """A pod's observable state at one point in time."""

    name: str
    namespace: str = ""
    uid: str = ""
    phase: str = "Unknown"
    deleting: bool = False
    owner: OwnerRef | None = None
    containers: tuple[ContainerState, ...] = ()
    init_containers: tuple[ContainerState, ...] = ()
    ephemeral_containers: tuple[ContainerState, ...] = ()
    status_reason: str = ""
    scheduling_reason: str = ""

    @classmethod
    def from_api(cls, item: dict, namespace: str = "") -> PodState:
        """Build a PodState from one item of a ``kubectl get pods -o json`` list."""
        metadata = item.get("metadata", {}) or {}
        status = item.get("status", {}) or {}

        owner = None
        for ref in metadata.get("ownerReferences", []) or []:
            if ref.get("controller"):
                owner = OwnerRef(kind=ref.get("kind", ""), name=ref.get("name", ""))
                break

        scheduling_reason = ""
        for cond in status.get("conditions", []) or []:
            if cond.get("type") == "PodScheduled" and cond.get("status") == "False":
                scheduling_reason = cond.get("reason", "Unschedulable")
                break

        def _group(key: str, kind: ContainerKind) -> tuple[ContainerState, ...]:
            return tuple(_parse_container(cs, kind) for cs in status.get(key, []) or [])

        return cls(
            name=metadata.get("name", "?"),
            namespace=metadata.get("namespace", "") or namespace,
            uid=metadata.get("uid", "") or "",
            phase=status.get("phase", "Unknown"),
            deleting=bool(metadata.get("deletionTimestamp")),
            owner=owner,
            containers=_group("containerStatuses", ContainerKind.APP),
            init_containers=_group("initContainerStatuses", ContainerKind.INIT),
            ephemeral_containers=_group(
                "ephemeralContainerStatuses", ContainerKind.EPHEMERAL
            ),
            status_reason=status.get("reason", "") or "",
            scheduling_reason=scheduling_reason,
        )

    @property
    def all_containers(self) -> tuple[ContainerState, ...]:
        """Every container status, ordered init -> app -> ephemeral."""
        return self.init_containers + self.containers + self.ephemeral_containers

    @property
    def ready(self) -> bool:
        """True when the pod reports containers and all of them are Ready.

        All of them: a multi-container pod (e.g. a decode pod with its routing
        sidecar) must not read as Ready while its serving container is
        crash-looping.
        """
        return bool(self.containers) and all(c.ready for c in self.containers)

    @property
    def summary(self) -> str:
        """One-token status suitable for progress lines and crash matching."""
        if not self.containers:
            if self.phase == "Pending" and self.scheduling_reason:
                return self.scheduling_reason
            return self.phase

        if self.ready:
            return "Ready"

        return summarize_container_states([c for c in self.containers if not c.ready])

    @property
    def crashing(self) -> bool:
        """True when this pod's container-level summary is a known crash state."""
        return self.summary in CRASH_STATES

    @property
    def health(self) -> Health:
        """Graded verdict, including pod-level failures with no container status.

        A pod whose containers never started (phase ``Failed``, reason
        ``Error``) reports no containerStatuses at all, so ``summary`` cannot
        see it -- but it is exactly the case a restart clears.
        """
        if self.ready:
            return Health.HEALTHY

        summary = self.summary
        if summary in TERMINAL_STATES:
            return Health.TERMINAL
        if summary in DEGRADED_STATES:
            return Health.DEGRADED

        if not self.containers:
            if self.status_reason in TERMINAL_STATES:
                return Health.TERMINAL
            if self.status_reason in DEGRADED_STATES or self.phase == "Failed":
                return Health.DEGRADED

        return Health.STARTING

    @property
    def controlled(self) -> bool:
        """True when a controller owns this pod and would recreate it."""
        return self.owner is not None

    @property
    def total_restarts(self) -> int:
        """Sum of restartCount across every container in the pod."""
        return sum(c.restart_count for c in self.all_containers)

    def restarts_for(self, container_name: str) -> int:
        """restartCount for one named container (0 when absent)."""
        return sum(
            c.restart_count for c in self.all_containers if c.name == container_name
        )

    @property
    def crash_details(self) -> list[str]:
        """Concrete per-container crash descriptions for user-facing errors."""
        failures = [
            f"{self.name}/{container.name} ({', '.join(details)})"
            for container in self.all_containers
            if (details := container.failure_details)
        ]

        if not failures and self.status_reason in CRASH_STATES:
            failures.append(f"{self.name} ({self.status_reason})")

        return failures

    @property
    def reason(self) -> str:
        """Short reason for the pod's current state, for logs and ledgers."""
        return self.summary if self.containers else (self.status_reason or self.phase)
