"""Smoketest module -- health checks, inference tests, and per-scenario validators."""

from llmdbenchmark.smoketests.base import BaseSmoketest


def get_validator(
    stack_name: str, is_kustomize: bool = False, is_fma: bool = False
) -> BaseSmoketest:
    """Return the scenario-specific validator, or the base if none exists."""
    from llmdbenchmark.smoketests.validators import VALIDATORS

    if is_fma:
        stack_name = "fast-model-actuation"
    elif is_kustomize:
        stack_name = "ignore"

    cls = VALIDATORS.get(stack_name, BaseSmoketest)

    return cls()
