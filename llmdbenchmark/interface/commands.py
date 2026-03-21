"""Valid CLI commands for llmdbenchmark."""

from enum import Enum


class Command(Enum):
    """Valid CLI commands for llmdbenchmark."""

    PLAN = "plan"
    STANDUP = "standup"
    SMOKETEST = "smoketest"
    RUN = "run"
    TEARDOWN = "teardown"
    EXPERIMENT = "experiment"
