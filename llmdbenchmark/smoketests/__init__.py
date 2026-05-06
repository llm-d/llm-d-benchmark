"""Smoketest module -- health checks, inference tests, and per-scenario validators."""

from llmdbenchmark.smoketests.base import BaseSmoketest


def get_validator(stack_name: str) -> BaseSmoketest:
    """Return the scenario-specific validator, or the base if none exists."""
    from llmdbenchmark.smoketests.validators import VALIDATORS

    cls = VALIDATORS.get(stack_name, BaseSmoketest)
    return cls()
