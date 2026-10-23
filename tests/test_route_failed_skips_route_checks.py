"""Stacks whose OpenShift Route could not be created skip route lookups.

Standup step 09 records such stacks in ``context.route_failed_stacks`` (e.g.
when the generated host exceeds 63 characters). The inference-test demo
command then prints the in-cluster URL instead of looking up a Route.

Behavior under test, for both standup step 11 and the smoketest phase:
- A failed stack never calls ``_detect_external_url``.
- Other stacks still look up the Route and print its URL.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from llmdbenchmark.smoketests.base import BaseSmoketest
from llmdbenchmark.standup.steps.step_11_inference_test import InferenceTestStep


class _StubLogger:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def log_info(self, msg: str, **_: Any) -> None:
        self.messages.append(msg)


@dataclass
class _StubContext:
    route_failed_stacks: set[str] = field(default_factory=set)
    logger: _StubLogger = field(default_factory=_StubLogger)


ROUTE_URL = "https://route.apps.example/m1/v1/completions"
BASE_URL = "http://10.0.0.1:80"


@pytest.mark.parametrize("cls", [InferenceTestStep, BaseSmoketest])
@pytest.mark.parametrize(
    ("failed", "expected_url"),
    [({"stack-a"}, f"{BASE_URL}/v1/completions"), (set(), ROUTE_URL)],
)
def test_demo_command_route_lookup(cls, failed, expected_url):
    obj = cls()
    lookups: list[str] = []

    def detect(_cmd, _ns, _plan, endpoint):
        lookups.append(endpoint)
        return ROUTE_URL

    obj._detect_external_url = detect  # type: ignore[method-assign]
    context = _StubContext(route_failed_stacks=failed)
    obj._print_demo_command(
        context,
        None,
        "ns1",
        {},
        BASE_URL,
        "/v1/completions",
        {"prompt": "hi"},
        "hello",
        stack_name="stack-a",
    )
    assert bool(lookups) is not bool(failed)
    assert any(expected_url in m for m in context.logger.messages)
