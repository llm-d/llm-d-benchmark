"""Step 02a-hotstart -- FMA hot-start warmup: load all models, then scale down.

Hot-start variant of step_02a_fma_warmup.py. Instead of just waiting for one
replica to load and then running the benchmark, this step:

  1. Waits for the FULL rollout — all N requester replicas available (all
     launchers loaded), via `kubectl rollout status` (not the weaker Available
     condition, which only needs the minAvailable threshold)
  2. Scales requester Deployment down to minReplicas, then waits for the
     scale-down to settle (rollout status), which unbinds N-1 launchers so the
     controller puts each unbound launcher's vLLM to sleep (model retained)
  3. STRICT gate (polled until convergence or timeout): fails unless
     <= minReplicas launchers are awake and the whole scaled-down surplus is
     sleeping AND Running+Ready (controller-owned label
     dual-pods.llm-d.ai/sleeping=true) — hot-start requires sleeping instances
  4. Benchmark then scales back up (1->N), measuring pure HPA/WVA behavior

Goal: Measure the same 1->N scaling as warm-start but without model load
latency confounding the results. All replicas have pre-loaded models in memory,
so scale-up is instantaneous.

Used by workload-autoscaling-hotstart scenario.
"""

import time
from pathlib import Path

from llmdbenchmark.executor.step import Step, StepResult, Phase
from llmdbenchmark.executor.context import ExecutionContext


class FMAWarmupHotStartStep(Step):
    """Hot-start warmup: load all models, scale down to 1, verify sleeping vLLM."""

    def __init__(self):
        super().__init__(
            number=2,
            name="fma_warmup_hotstart",
            description="Hot-start: load all models, scale down, verify sleeping vLLM",
            phase=Phase.RUN,
            per_stack=True,
        )

    def should_skip(self, context: ExecutionContext) -> bool:
        return context.harness_skip_run

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

        plan_config = self._load_stack_config(stack_path)
        stack_name = stack_path.name

        if not self._resolve(plan_config, "fma.enabled", default=False):
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=True,
                message="fma.enabled is false; skipping hotstart warmup",
                stack_name=stack_name,
            )

        replicas = int(
            self._resolve(plan_config, "fma.requester.replicas", default=0) or 0
        )
        if replicas == 0:
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=True,
                message=(
                    "fma.requester.replicas=0 (no requester deployment "
                    "rendered); skipping hotstart warmup"
                ),
                stack_name=stack_name,
            )

        cmd = context.require_cmd()
        namespace = context.require_namespace()
        model_id_label = plan_config.get("model_id_label", "")
        if not model_id_label:
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=False,
                message="model_id_label missing from plan_config",
                errors=["model_id_label is required for FMA hotstart warmup"],
                stack_name=stack_name,
            )

        # Model name for user-facing log messages only. Derived from config so
        # this step stays reusable across scenarios/models; falls back to a
        # generic label if unset.
        model_name = self._resolve(plan_config, "model.name", default="the model")
        deploy_name = f"fma-requester-{model_id_label}"
        timeout = int(self._resolve(plan_config, "fma.warmupTimeout", default=1200))
        # Scale-down floor: read the HPA's minReplicas, since the HPA is what
        # actually enforces the requester Deployment's replica count during the
        # benchmark. Scaling below it would just get bounced back up on the next
        # HPA sync, defeating the "sleep until the benchmark scales up" intent.
        # variantAutoscaling.minReplicas only bounds what the WVA controller
        # *computes*; fall back to it (then 1) when the HPA floor is unset.
        min_replicas = int(
            self._resolve(
                plan_config,
                "wva.hpa.minReplicas",
                "wva.variantAutoscaling.minReplicas",
                default=1,
            )
        )

        # Stage 1: Wait for the FULL rollout — ALL N requester replicas must be
        # available, i.e. all N launchers are bound and vLLM has finished
        # loading the model.
        #
        # We use `kubectl rollout status`, NOT `wait --for=condition=Available`:
        # a Deployment's Available condition only requires the minimum-
        # availability threshold (readyReplicas >= replicas - maxUnavailable),
        # so with replicas=10 it can flip to Available at 8/10. That would let
        # hot-start proceed before every launcher has loaded the model,
        # invalidating the "all models pre-loaded" precondition. `rollout status`
        # returns success only once updatedReplicas == readyReplicas ==
        # availableReplicas == desired, i.e. the complete 10/10 rollout.
        context.logger.log_info(
            f"⏳ Hot-start warmup Stage 1: waiting up to {timeout}s for the full "
            f"rollout of Deployment/{deploy_name} (all {replicas} launchers "
            f"loaded) in ns/{namespace}"
        )
        result = cmd.kube(
            "rollout",
            "status",
            f"deployment/{deploy_name}",
            f"--timeout={timeout}s",
            namespace=namespace,
            check=False,
        )
        if not result.success:
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=False,
                message=(
                    f"Hot-start warmup Stage 1: Deployment/{deploy_name} rollout "
                    f"did not complete ({replicas}/{replicas} available) within "
                    f"{timeout}s (some launchers failed to load the model)"
                ),
                errors=[
                    result.stderr.strip()[:400]
                    or result.stdout.strip()[:400]
                    or "rollout status timed out"
                ],
                stack_name=stack_name,
            )

        context.logger.log_info(
            f"✓ Hot-start warmup Stage 1 complete: full rollout done — all "
            f"{replicas} launchers have {model_name} loaded"
        )

        # Stage 2: Scale requester Deployment down to minReplicas.
        # This unbinds N-1 requesters, putting their launcher's vLLM in sleep
        # state while keeping the model in memory. Benchmark will then scale
        # back up (1->N), measuring pure HPA/WVA behavior.
        context.logger.log_info(
            f"⏳ Hot-start warmup Stage 2: scaling Deployment/{deploy_name} "
            f"from {replicas} -> {min_replicas} to put vLLM in sleep state"
        )
        result = cmd.kube(
            "scale",
            f"deployment/{deploy_name}",
            f"--replicas={min_replicas}",
            "--namespace",
            namespace,
            check=False,
        )
        if not result.success:
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=False,
                message=(
                    f"Hot-start warmup Stage 2: failed to scale "
                    f"Deployment/{deploy_name} to {min_replicas} replicas"
                ),
                errors=[result.stderr.strip()[:400] or "scale failed"],
                stack_name=stack_name,
            )

        # Wait for the scale-DOWN to settle before gating on sleep state, rather
        # than sleeping a brittle fixed interval: pod termination + unbinding +
        # the controller flipping the sleeping label can take well over a few
        # seconds, so a fixed sleep makes Stage 3 flake on a cluster that would
        # otherwise converge. `rollout status` on the down-scaled Deployment
        # returns once available == desired == min_replicas (surplus requester
        # pods fully terminated), the deterministic settle point after which the
        # controller unbinds their launchers. Stage 3 then polls for the sleep
        # label so the remaining async (label flip) is waited on, not raced.
        context.logger.log_info(
            f"⏳ Hot-start warmup Stage 2b: waiting up to {timeout}s for "
            f"Deployment/{deploy_name} to settle at {min_replicas} replica(s) "
            f"after scale-down"
        )
        result = cmd.kube(
            "rollout",
            "status",
            f"deployment/{deploy_name}",
            f"--timeout={timeout}s",
            namespace=namespace,
            check=False,
        )
        if not result.success:
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=False,
                message=(
                    f"Hot-start warmup Stage 2b: Deployment/{deploy_name} did not "
                    f"settle at {min_replicas} replica(s) within {timeout}s after "
                    f"scale-down"
                ),
                errors=[
                    result.stderr.strip()[:400]
                    or result.stdout.strip()[:400]
                    or "rollout status timed out"
                ],
                stack_name=stack_name,
            )

        # Stage 3: STRICT gate on the hot-start precondition — the pool must be
        # mostly ASLEEP, with the sleeping instances actually resident.
        #
        # The dual-pods controller stamps each launcher pod with
        # `dual-pods.llm-d.ai/sleeping=<true|false>`: after our scale-down it
        # unbinds the surplus requesters and flips their launchers to
        # `sleeping=true` (model tensors retained in CPU memory, vLLM level-1
        # sleep), while the launcher(s) still bound to the remaining requester(s)
        # stay `sleeping=false`. That label is the authoritative, controller-
        # owned signal, so we gate on it directly.
        #
        # Hot-start REQUIRES resident sleeping instances, so we FAIL the run
        # unless (see the three gates below):
        #   1. every sleeping launcher we need is Running AND Ready (a Pending /
        #      not-Ready pod is not a resident vLLM),
        #   2. at most min_replicas launchers are awake (sleeping=false) — more
        #      than that means the surplus never went to sleep, so there is no
        #      hot-start to measure, and
        #   3. at least (replicas - min_replicas) launchers are sleeping AND
        #      Running+Ready — the whole surplus we scaled down is asleep.
        expected_sleeping = max(replicas - min_replicas, 0)
        context.logger.log_info(
            f"⏳ Hot-start warmup Stage 3: polling sleeping launcher pool "
            f"(require <= {min_replicas} awake and >= {expected_sleeping} "
            f"Running+Ready sleeping launchers) up to {timeout}s before benchmark"
        )

        # One custom-columns query gives, per launcher pod and aligned by row,
        # the sleeping label, phase, and Ready-condition status. cmd.kube()
        # shell-joins its args (and runs under shell=True), so the whole
        # custom-columns spec is passed as ONE single-quoted token — otherwise
        # the shell glob-expands the `[?(@.type=="Ready")]` filter and strips
        # the quotes ("no matches found").
        pool_selector = (
            f"stood-up-via=fma,"
            f"dual-pods.llm-d.ai/launcher-config-name=fma-{model_id_label}"
        )
        columns = (
            "'custom-columns="
            "SLEEP:.metadata.labels.dual-pods\\.llm-d\\.ai/sleeping,"
            "PHASE:.status.phase,"
            'READY:.status.conditions[?(@.type=="Ready")].status\''
        )

        def _sleep(cols):
            return cols[0] if cols else ""

        def _running_ready(cols):
            # cols == [sleeping, phase, ready]; ready is "True"/"False"/"<none>"
            return len(cols) >= 3 and cols[1] == "Running" and cols[2] == "True"

        # Poll the sleep gate rather than checking once: the controller flips the
        # sleeping label asynchronously after the surplus requesters terminate,
        # so the pool may still be settling when Stage 2b returns. We re-query
        # until both gates pass or the timeout elapses, and only then fail —
        # with the last-observed counts — so we never flake on a cluster that
        # would have converged given a little more time.
        poll_interval = 10
        deadline = timeout
        elapsed = 0
        awake = 0
        sleeping_ready = 0
        gate_error = "launcher pool never converged to the sleeping precondition"
        while True:
            result = cmd.kube(
                "get",
                "pods",
                "-l",
                pool_selector,
                "-o",
                columns,
                "--no-headers",
                namespace=namespace,
                check=False,
            )
            if not result.success:
                return StepResult(
                    step_number=self.number,
                    step_name=self.name,
                    success=False,
                    message="Hot-start warmup Stage 3: failed to query launcher pool",
                    errors=[result.stderr.strip()[:400] or "get pods failed"],
                    stack_name=stack_name,
                )

            # Each row: "<sleeping> <phase> <ready>" (e.g. "true Running True").
            pool = [row.split() for row in result.stdout.splitlines() if row.split()]
            awake = sum(1 for c in pool if _sleep(c) == "false")
            sleeping_ready = sum(
                1 for c in pool if _sleep(c) == "true" and _running_ready(c)
            )

            # Gate 2 (awake) + Gate 3 (sleeping surplus resident). Both must hold.
            if not pool:
                gate_error = f"no launcher pods found for fma-{model_id_label}"
            elif awake > min_replicas:
                gate_error = (
                    f"{awake} awake launcher(s) (sleeping=false) exceeds "
                    f"min_replicas={min_replicas} — surplus not put to sleep"
                )
            elif sleeping_ready < expected_sleeping:
                gate_error = (
                    f"{sleeping_ready} of {expected_sleeping} surplus launchers "
                    "sleeping AND Running+Ready — still converging"
                )
            else:
                break  # both gates satisfied

            if elapsed >= deadline:
                return StepResult(
                    step_number=self.number,
                    step_name=self.name,
                    success=False,
                    message=(
                        f"Hot-start warmup Stage 3: launcher pool did not reach "
                        f"the sleeping precondition within {timeout}s "
                        f"(observed {awake} awake, {sleeping_ready}/"
                        f"{expected_sleeping} sleeping+Running+Ready) — "
                        "hot-start requires the scaled-down surplus to be "
                        "resident sleeping instances"
                    ),
                    errors=[gate_error],
                    stack_name=stack_name,
                )

            context.logger.log_info(
                f"    | Stage 3: not converged yet ({awake} awake, "
                f"{sleeping_ready}/{expected_sleeping} sleeping+ready); "
                f"re-checking in {poll_interval}s"
            )
            time.sleep(poll_interval)
            elapsed += poll_interval

        context.logger.log_info(
            f"✓ Hot-start warmup Stage 3 complete: {sleeping_ready} launcher(s) "
            f"sleeping and Running+Ready (>= {expected_sleeping}), {awake} awake "
            f"(<= {min_replicas}). Ready for benchmark scale-up (1->{replicas})"
        )

        return StepResult(
            step_number=self.number,
            step_name=self.name,
            success=True,
            message=(
                f"Hot-start warmup complete: {replicas} launchers pre-loaded "
                f"{model_name}, requester scaled down to {min_replicas}; "
                f"{sleeping_ready} launcher(s) sleeping and Running+Ready, "
                f"{awake} awake (model resident). Benchmark will scale up "
                f"(1->{replicas}) measuring pure HPA/WVA behavior"
            ),
            stack_name=stack_name,
        )
