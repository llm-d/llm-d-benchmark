"""Step registry for the smoketest phase."""

from llmdbenchmark.executor.step import Step

from llmdbenchmark.smoketests.steps.step_00_health_check import HealthCheckStep
from llmdbenchmark.smoketests.steps.step_01_inference_test import InferenceTestStep
from llmdbenchmark.smoketests.steps.step_02_validate_config import ValidateConfigStep


def get_smoketest_steps() -> list[Step]:
    """Return all smoketest-phase steps in execution order."""
    return [
        HealthCheckStep(),
        InferenceTestStep(),
        ValidateConfigStep(),
    ]
