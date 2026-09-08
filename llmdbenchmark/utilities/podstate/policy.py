"""Pluggable responses to unhealthy pods observed during a readiness wait.

A waiter observes pods; a *policy* decides what -- if anything -- to do about
what was observed.  Keeping that decision behind :class:`PodPolicy` means new
reactions (capture diagnostics on first failure, react to node pressure, give
up early on a stalled rollout) can be added without touching the wait loop.

Policies are pure: they return a :class:`Remedy` describing what should
happen, and the caller performs the side effects.  That keeps them testable
without a cluster and keeps deletion in one audited place.
"""

from __future__ import annotations

import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, runtime_checkable

from llmdbenchmark.utilities.podstate.state import Health, PodState


class Verdict(Enum):
    """What the wait loop should do next."""

    CONTINUE = "continue"
    ABORT = "abort"


@dataclass(frozen=True)
class Remedy:
    """A policy's answer: what to do, and why."""

    verdict: Verdict = Verdict.CONTINUE
    delete_pods: tuple[str, ...] = ()
    extend_deadline: float = 0.0
    message: str = ""


@dataclass(frozen=True)
class WaitContext:
    """What the wait loop knows about itself, passed to policies."""

    description: str
    namespace: str
    elapsed: float
    timeout: float

    @property
    def remaining(self) -> float:
        """Seconds left on the current deadline (never negative)."""
        return max(0.0, self.timeout - self.elapsed)


@runtime_checkable
class PodPolicy(Protocol):
    """Reacts to a set of observed pods."""

    def observe(
        self, pods: Sequence[PodState], ctx: WaitContext
    ) -> Remedy | None:  # pragma: no cover - protocol
        """Return a Remedy to act on, or None to leave the wait untouched."""
        ...


@dataclass(frozen=True)
class RestartEvent:
    """One consumed restart, for end-of-phase reporting."""

    pod: str
    namespace: str
    reason: str
    sequence: int
    description: str = ""


@dataclass(frozen=True)
class GrantedRestart:
    """A pod the budget agreed to spend a restart on."""

    pod: PodState
    event: RestartEvent


class RestartBudget:
    """A total, phase-wide allowance of pod deletions.

    Deliberately *not* per-pod: the point of the knob is to cap how much
    churn a standup is allowed in total, so one pathologically broken pod
    cannot consume an unbounded number of restarts just because each
    individual pod stayed under its own limit.

    Shared across threads: standup deploys stacks in parallel through a single
    CommandExecutor, so several waits can claim from this concurrently.
    """

    def __init__(self, limit: int = 0) -> None:
        self._limit = max(0, int(limit))
        self._used = 0
        self._lock = threading.Lock()
        self._events: list[RestartEvent] = []
        self._claimed_keys: set[str] = set()

    @property
    def limit(self) -> int:
        """Total restarts allowed."""
        return self._limit

    @property
    def used(self) -> int:
        """Restarts consumed so far."""
        with self._lock:
            return self._used

    @property
    def remaining(self) -> int:
        """Restarts still available."""
        with self._lock:
            return max(0, self._limit - self._used)

    @property
    def enabled(self) -> bool:
        """True when the budget allows any restart at all."""
        return self._limit > 0

    @property
    def exhausted(self) -> bool:
        """True when the budget is enabled but fully spent."""
        with self._lock:
            return self._limit > 0 and self._used >= self._limit

    @property
    def events(self) -> list[RestartEvent]:
        """Chronological record of consumed restarts."""
        with self._lock:
            return list(self._events)

    @staticmethod
    def _key(pod: PodState) -> str:
        """Identity used to avoid charging the same pod instance twice.

        A deleted pod lingers in ``Terminating`` for several poll cycles; the
        replacement carries a new UID.  Keying on UID means the old instance is
        never re-charged while the new one still can be.  Pods with no UID
        (synthetic payloads) fall back to their name.
        """
        return pod.uid or f"{pod.namespace}/{pod.name}"

    def claim(
        self,
        pods: Sequence[PodState],
        *,
        description: str = "",
    ) -> list[GrantedRestart]:
        """Reserve restarts for *pods*, atomically.

        Returns the subset granted (possibly empty), each with its 1-based
        sequence number.  Pods already charged for are skipped and do not
        count against the budget.
        """
        granted: list[GrantedRestart] = []
        with self._lock:
            for pod in pods:
                if self._used >= self._limit:
                    break
                key = self._key(pod)
                if key in self._claimed_keys:
                    continue
                self._claimed_keys.add(key)
                self._used += 1
                event = RestartEvent(
                    pod=pod.name,
                    namespace=pod.namespace,
                    reason=pod.reason,
                    sequence=self._used,
                    description=description,
                )
                self._events.append(event)
                granted.append(GrantedRestart(pod=pod, event=event))
        return granted

    def claimed(self, pod: PodState) -> bool:
        """True when a restart was already spent on this exact pod instance."""
        with self._lock:
            return self._key(pod) in self._claimed_keys

    def status(self) -> str:
        """Human-readable ``used/limit`` string."""
        return f"{self.used}/{self._limit}"

    def summary_lines(self) -> list[str]:
        """One line per consumed restart, for an end-of-phase report."""
        lines = []
        for event in self.events:
            where = f"{event.namespace}/{event.pod}" if event.namespace else event.pod
            suffix = f" during {event.description}" if event.description else ""
            lines.append(
                f"  [{event.sequence}/{self._limit}] {where} ({event.reason}){suffix}"
            )
        return lines


@dataclass
class RestartBudgetPolicy:
    """Delete degraded, controller-owned pods while the budget allows it.

    Eligibility is deliberately narrow:

    * ``Health.DEGRADED`` only -- a wrong image name is not fixed by a restart.
    * Controller-owned only -- deleting a bare pod (``--restart=Never``, as
      the smoketests create) means it never comes back.
    * Not already terminating -- otherwise a Terminating pod is re-deleted
      every poll.
    """

    budget: RestartBudget
    grace_seconds: float = 300.0
    _pending: list[GrantedRestart] = field(default_factory=list, repr=False)

    def eligible(self, pods: Sequence[PodState]) -> list[PodState]:
        """Pods this policy would restart if the budget allowed."""
        return [
            pod
            for pod in pods
            if pod.health is Health.DEGRADED and pod.controlled and not pod.deleting
        ]

    def observe(self, pods: Sequence[PodState], ctx: WaitContext) -> Remedy | None:
        """Claim restarts for degraded pods; None when nothing to do."""
        if not self.budget.enabled:
            return None

        candidates = self.eligible(pods)
        if not candidates:
            return None

        granted = self.budget.claim(candidates, description=ctx.description)
        if not granted:
            # Nothing new to claim. If the pods that still look broken are ones
            # we already deleted, their replacements simply have not appeared
            # yet -- deletion is asynchronous and a pod lingers in Terminating
            # for several polls. Hold the wait open rather than letting the
            # caller abort on a pod we deliberately restarted.
            if any(not pod.ready and self.budget.claimed(pod) for pod in pods):
                return Remedy(verdict=Verdict.CONTINUE)
            return None

        self._pending = granted
        details = ", ".join(
            f"{g.pod.name} ({g.pod.reason}) "
            f"[restart {g.event.sequence}/{self.budget.limit}]"
            for g in granted
        )
        return Remedy(
            verdict=Verdict.CONTINUE,
            delete_pods=tuple(g.pod.name for g in granted),
            # A replacement pod starts its own image pull and model load from
            # zero. Without extra budget a crash late in the wait guarantees
            # the restart times out, making the restart pointless.
            extend_deadline=self.grace_seconds * len(granted),
            message=f"restarting {details}",
        )

    def take_granted(self) -> list[GrantedRestart]:
        """Consume and return the pods granted by the last observe() call."""
        granted, self._pending = self._pending, []
        return granted
