"""Step 02 -- Prepare the harness namespace."""

from llmdbenchmark.standup.steps.step_05_harness_namespace import (
    HarnessNamespaceStep as _HarnessNamespaceStep,
)
from llmdbenchmark.executor.step import Phase


class HarnessNamespaceStep(_HarnessNamespaceStep):
    """Prepare the namespace for the benchmark harness.
    
    This inherits from the standup phase's step_05, but overrides the step
    number so it correctly sequences into the run pipeline.
    """

    def __init__(self):
        super().__init__()
        self.number = 2
        self.phase = Phase.RUN
