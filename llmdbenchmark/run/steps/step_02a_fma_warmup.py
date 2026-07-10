"""Step 02a -- Wait for FMA launcher pool to warm up before benchmarking.

Wait for the fma-requester Deployment to become ``Available``
(i.e. at least one launcher is bound and its vLLM
container is fully initialized) before driving load.

Why this lives in the run phase rather than at the end of standup: HPA
scaling is what causes pain when launchers are still cold (the requester
deployment scales 1->N before off-axis launchers finish loading model
weights, so DPC binds new requesters to launchers whose vLLM is still
starting -- and T_actuation includes that wait). Putting the warmup right
before the harness fires is the natural place: standup is "everything is
deployed", run-phase warmup is "everything is *hot enough* for the
benchmark".

Skipped when ``fma.enabled`` is false or no requester deployment was
rendered (``fma.requester.replicas == 0``).

A scenario may select an alternate warmup by setting the top-level
``fmaWarmupStep`` key; ``step_02a_fma_warmup_hotstart`` delegates to
:class:`FMAWarmupHotStartStep` (load all launchers, scale down to a sleeping
vLLM, then benchmark scales back up).
"""

from pathlib import Path

from llmdbenchmark.executor.step import Step, StepResult, Phase
from llmdbenchmark.executor.context import ExecutionContext


class FMAWarmupStep(Step):
    """Wait for FMA launchers to be hot before the benchmark drives load."""

    def __init__(self):
        super().__init__(
            number=2,
            name="fma_warmup",
            description="Wait for FMA launcher pool to warm up",
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

        # Per-stack warmup-variant dispatch. A scenario can opt into an
        # alternate warmup by setting the top-level `fmaWarmupStep` key (it
        # survives rendering into config.yaml via the plain deep-merge in
        # render_plans.py, and the root config schema is extra="allow").
        # `get_run_steps()` registers a single step-02a for the whole run, so
        # the selection has to happen here at execute time where we have the
        # per-stack config in hand. Hot-start loads all launchers, then scales
        # the requester Deployment down to minReplicas so the extra launchers
        # hold a sleeping vLLM (model in memory) before the benchmark drives
        # scale-up 1->N.
        warmup_step = self._resolve(plan_config, "fmaWarmupStep", default="")
        if warmup_step == "step_02a_fma_warmup_hotstart":
            from llmdbenchmark.run.steps.step_02a_fma_warmup_hotstart import (
                FMAWarmupHotStartStep,
            )

            return FMAWarmupHotStartStep().execute(context, stack_path)

        if not self._resolve(plan_config, "fma.enabled", default=False):
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=True,
                message="fma.enabled is false; skipping warmup",
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
                errors=["model_id_label is required for FMA warmup"],
                stack_name=stack_name,
            )

        timeout = int(self._resolve(plan_config, "fma.warmupTimeout", default=300))

        # Stage 0: Wait for the launcher pods' to reach the
        # Ready condition so the requester scale-up doesn't race launcher
        # container startup (image pull + launcher.py bind).
        launcher_selector = (
            f"stood-up-via=fma,"
            f"dual-pods.llm-d.ai/launcher-config-name=fma-{model_id_label}"
        )
        context.logger.log_info(
            f"⏳ FMA warmup: waiting up to {timeout}s for launcher pods "
            f"({launcher_selector}) to be Ready in ns/{namespace}"
        )
        launcher_wait = cmd.wait_for_pods(
            label=launcher_selector,
            namespace=namespace,
            timeout=timeout,
            poll_interval=10,
            description=f"FMA launchers ready (model={model_id_label})",
        )
        if not launcher_wait.success:
            context.logger.log_warning(
                f"FMA warmup: launcher pods not all Ready within {timeout}s in "
                f"ns/{namespace}. {launcher_wait.stderr.strip()[:200]}"
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
                    "rendered); skipping warmup"
                ),
                stack_name=stack_name,
            )

        # Stage 1: kubectl wait Deployment Available. With replicas=N, this
        # succeeds when N requester pods are Ready -- i.e. N launchers have
        # vLLM serving.
        deploy_name = f"fma-requester-{model_id_label}"
        context.logger.log_info(
            f"⏳ FMA warmup: waiting up to {timeout}s for Deployment/"
            f"{deploy_name} to become Available in ns/{namespace}"
        )
        result = cmd.kube(
            "wait",
            "--for=condition=Available",
            f"deployment/{deploy_name}",
            "--namespace",
            namespace,
            f"--timeout={timeout}s",
            check=False,
        )
        if not result.success:
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=False,
                message=(
                    f"FMA warmup: Deployment/{deploy_name} did not become "
                    f"Available within {timeout}s"
                ),
                errors=[result.stderr.strip()[:400] or "wait timed out"],
                stack_name=stack_name,
            )

        return StepResult(
            step_number=self.number,
            step_name=self.name,
            success=True,
            message=(f"FMA warmup: Deployment/{deploy_name} Available"),
            stack_name=stack_name,
        )
