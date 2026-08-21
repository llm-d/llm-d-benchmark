# llmdbenchmark.utilities.podstate

Structured pod state, observation, remediation policy, and diagnostics.

Before this package, "what is this pod doing?" was re-derived from raw
`kubectl get pods -o json` payloads in four places, each covering a slightly
different slice and disagreeing at the edges. This is the one parser and the
one model.

## Modules

| Module | Responsibility |
|---|---|
| `state.py` | `PodState` / `ContainerState` / `Health` -- the data model and its classification rules |
| `observer.py` | `parse_pod_list()` / `observe_pods()` -- the single `get pods -o json` parser |
| `policy.py` | `PodPolicy` protocol, `Remedy`, `RestartBudget`, `RestartBudgetPolicy` |
| `diagnostics.py` | Evidence capture before destructive remediation, plus end-of-phase reporting |

## The `Health` grading

The key distinction the model adds over a flat "is it crashing?" set:

| Health | Meaning | Example |
|---|---|---|
| `HEALTHY` | Every container Ready | -- |
| `STARTING` | Not Ready, no failure signal | `PodInitializing`, `Unschedulable` |
| `DEGRADED` | Failing in a way a restart **may** clear | `CrashLoopBackOff`, `Error`, `OOMKilled`, pod phase `Failed` |
| `TERMINAL` | Failing in a way a restart **cannot** clear | `ImagePullBackOff`, `InvalidImageName`, `CreateContainerConfigError` |

`CRASH_STATES` (`DEGRADED_STATES | TERMINAL_STATES`) is preserved as the
historical flat set and re-exported from `kube_helpers` for existing callers.

## The policy seam

A waiter observes pods; a *policy* decides what to do about what it saw.

```python
class PodPolicy(Protocol):
    def observe(self, pods: Sequence[PodState], ctx: WaitContext) -> Remedy | None: ...
```

Policies are **pure**: they return a `Remedy` describing what should happen
(`verdict`, `delete_pods`, `extend_deadline`, `message`) and the caller
performs the side effects. That keeps them testable without a cluster and
keeps pod deletion in one audited place.

`RestartBudgetPolicy` is the first implementation. New reactions -- capture
diagnostics on first failure, react to node pressure, give up early on a
stalled rollout -- plug into the same seam without touching the wait loop.

## `RestartBudget`

A **total, phase-wide** allowance of pod deletions, deliberately not per-pod:
the cap is on how much churn a phase is allowed, so one pathologically broken
pod cannot consume unbounded restarts by staying under its own limit.

Thread-safe, because standup deploys stacks in parallel through a single
shared `CommandExecutor`. Charges are keyed on pod **UID**, so a pod lingering
in `Terminating` is never re-charged while its replacement still can be.

## Adding a policy

1. Implement `observe(pods, ctx) -> Remedy | None`.
2. Return `None` when you have nothing to say -- the caller then applies its
   normal rules.
3. Return `Remedy(verdict=Verdict.CONTINUE, ...)` to keep waiting, optionally
   asking for deletions or a deadline extension; `Verdict.ABORT` to stop.
4. Append it to `CommandExecutor._pod_policies`.

If a policy asks for a deletion, capture evidence first --
`diagnostics.capture_pod_evidence()`. Deleting a pod destroys its logs and
events, and an unexplained restart is worse than a clean failure.
