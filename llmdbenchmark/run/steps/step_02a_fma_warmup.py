"""Step 02a -- Wait for FMA launcher pool to warm up before benchmarking.

Mirrors Braulio's pattern in llm-d-fast-model-actuation PR #579: after
standup completes, deliberately wait for the fma-requester Deployment to
become ``Available`` (i.e. at least one launcher is bound and its vLLM
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
"""

import time
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

        if not self._resolve(plan_config, "fma.enabled", default=False):
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=True,
                message="fma.enabled is false; skipping warmup",
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
                    "rendered); skipping warmup"
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
                errors=["model_id_label is required for FMA warmup"],
                stack_name=stack_name,
            )

        deploy_name = f"fma-requester-{model_id_label}"
        timeout = int(self._resolve(plan_config, "fma.warmupTimeout", default=600))
        buffer_seconds = int(
            self._resolve(plan_config, "fma.warmupBufferSeconds", default=0)
        )

        # Stage 1: kubectl wait Deployment Available. With replicas=N, this
        # succeeds when N requester pods are Ready -- i.e. N launchers have
        # vLLM serving. Same gate Braulio uses in PR #579's demo script.
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

        # Stage 2: optional buffer sleep so off-axis launchers (the ones
        # NOT bound to the initial requester replicas) finish loading model
        # weights before HPA scale-up fires. With launcher-populator
        # creating one launcher per node and only `replicas` of them bound
        # initially, the rest are still in vLLM-startup at end of stage 1
        # and scale-up to maxReplicas would race them. 0 = opt-out (matches
        # Braulio's demo, which doesn't add a buffer).
        if buffer_seconds > 0:
            context.logger.log_info(
                f"⏳ FMA warmup: sleeping {buffer_seconds}s buffer for "
                f"off-axis launcher vLLM init to complete"
            )
            time.sleep(buffer_seconds)

        return StepResult(
            step_number=self.number,
            step_name=self.name,
            success=True,
            message=(
                f"FMA warmup: Deployment/{deploy_name} Available"
                + (
                    f" (+ {buffer_seconds}s off-axis-launcher buffer)"
                    if buffer_seconds > 0
                    else ""
                )
            ),
            stack_name=stack_name,
        )
