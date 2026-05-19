"""Step 02 -- Prepare the harness namespace."""

from llmdbenchmark.standup.steps.step_05_harness_namespace import (
    HarnessNamespaceStep as _HarnessNamespaceStep,
)
from llmdbenchmark.executor.step import Phase
from llmdbenchmark.executor.context import ExecutionContext


class HarnessNamespaceStep(_HarnessNamespaceStep):
    """Prepare the namespace for the benchmark harness.

    Inherits the standup phase's step_05 logic (idempotent: it reuses an
    existing workload PVC / data-access pod, tolerates ``AlreadyExists``),
    but overrides the step number and skip logic for the run pipeline.
    """

    def __init__(self):
        super().__init__()
        self.number = 2
        self.phase = Phase.RUN

    def should_skip(self, context: ExecutionContext) -> bool:
        """Run always needs the harness workload PVC + data-access pod.

        The base step_05 skips this for kustomize standups
        (``methods == ["kustomize"] and kustomize_skip_infra``) because the
        guide owns *its* infra -- but the benchmark harness's own workload
        PVC is NOT part of the guide, so a kustomize-deployed stack would
        otherwise leave the inference-perf pod unschedulable on an unbound
        PVC. Prepare it here regardless of deploy method (idempotent if a
        non-kustomize standup already created it). Only skip in collect-only
        (skip-run) mode, where no harness pod is deployed.
        """
        return context.harness_skip_run
