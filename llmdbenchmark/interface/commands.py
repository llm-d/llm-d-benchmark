"""Valid CLI commands for llmdbenchmark."""

from enum import Enum


class Command(Enum):
    """Valid CLI commands for llmdbenchmark."""

    PLAN = "plan"
    STANDUP = "standup"
    RUN = "run"
    TEARDOWN = "teardown"
