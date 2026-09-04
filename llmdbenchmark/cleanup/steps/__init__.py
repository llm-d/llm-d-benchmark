"""Step registry for the cleanup phase."""

from llmdbenchmark.executor.step import Step

from llmdbenchmark.cleanup.steps.step_00_cleanup_resources import (
    CleanupResourcesStep,
)


def get_cleanup_steps() -> list[Step]:
    """Return all cleanup-phase steps in execution order."""
    return [
        CleanupResourcesStep(),
    ]
