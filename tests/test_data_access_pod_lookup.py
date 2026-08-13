"""Tests for the data-access pod lookup retry.

This lookup gates result collection: when it returns None the caller abandons a
completed wave's results, so a transient API failure must not be fatal.
"""

from __future__ import annotations

from dataclasses import dataclass

from llmdbenchmark.utilities.kube_helpers import find_data_access_pod


@dataclass
class _Result:
    success: bool
    stdout: str = ""
    stderr: str = ""


class _FakeCmd:
    """Returns queued results in order, recording how many calls were made."""

    def __init__(self, results):
        self._results = list(results)
        self.calls = 0

    def kube(self, *args, **kwargs):
        self.calls += 1
        if self._results:
            return self._results.pop(0)
        return _Result(False, "", "exhausted")


def test_returns_pod_on_first_success() -> None:
    cmd = _FakeCmd([_Result(True, "access-to-harness-data-workload-pvc")])
    assert (
        find_data_access_pod(cmd, "ns", attempts=5, delay=0)
        == "access-to-harness-data-workload-pvc"
    )
    assert cmd.calls == 1  # no needless retries on the happy path


def test_retries_then_succeeds() -> None:
    """A transient failure must not discard the run's results."""
    cmd = _FakeCmd(
        [
            _Result(False, "", "dial tcp: lookup api...: no such host"),
            _Result(False, "", "connection refused"),
            _Result(True, "access-to-harness-data-workload-pvc"),
        ]
    )
    assert find_data_access_pod(cmd, "ns", attempts=5, delay=0) is not None
    assert cmd.calls == 3


def test_gives_up_after_attempts() -> None:
    cmd = _FakeCmd([_Result(False, "", "boom")] * 10)
    assert find_data_access_pod(cmd, "ns", attempts=4, delay=0) is None
    assert cmd.calls == 4  # bounded, not infinite


def test_jsonpath_out_of_bounds_is_not_a_pod_name() -> None:
    """kubectl prints a multi-line diagnostic on stdout when the label matches
    nothing; treating that as a pod name would produce a bogus `kubectl cp`."""
    noise = (
        'error: error executing jsonpath "{.items[0].metadata.name}": '
        "array index out of bounds: index 0, length 0\n"
        "template was:\n\t{.items[0].metadata.name}\n"
    )
    cmd = _FakeCmd([_Result(True, noise)] * 3)
    assert find_data_access_pod(cmd, "ns", attempts=3, delay=0) is None


def test_whitespace_only_stdout_is_not_a_pod_name() -> None:
    cmd = _FakeCmd([_Result(True, "   \n  ")] * 2)
    assert find_data_access_pod(cmd, "ns", attempts=2, delay=0) is None


def test_env_float_parses_and_falls_back() -> None:
    """The delay flag's default comes from the environment."""
    import os
    from llmdbenchmark.interface.env import env_float

    os.environ["LLMDBENCH_TEST_DELAY"] = "2.5"
    try:
        assert env_float("LLMDBENCH_TEST_DELAY") == 2.5
    finally:
        del os.environ["LLMDBENCH_TEST_DELAY"]
    assert env_float("LLMDBENCH_TEST_DELAY_UNSET", 3.0) == 3.0
    os.environ["LLMDBENCH_TEST_DELAY"] = "not-a-number"
    try:
        assert env_float("LLMDBENCH_TEST_DELAY", 3.0) == 3.0
    finally:
        del os.environ["LLMDBENCH_TEST_DELAY"]


def test_context_defaults_match_the_helper() -> None:
    """A default drift here would silently change collection robustness."""
    from llmdbenchmark.executor.context import ExecutionContext
    from llmdbenchmark.utilities import kube_helpers as kh

    ctx = ExecutionContext.__dataclass_fields__
    assert ctx["data_access_lookup_attempts"].default == kh.DATA_ACCESS_LOOKUP_ATTEMPTS
    assert (
        ctx["data_access_lookup_delay"].default == kh.DATA_ACCESS_LOOKUP_DELAY_SECONDS
    )


def test_cli_exposes_the_lookup_flags() -> None:
    """Vezio's review point: these must be reachable without editing source."""
    import argparse
    from llmdbenchmark.interface import run as run_iface

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    run_iface.add_subcommands(sub)
    args = parser.parse_args(
        [
            "run",
            "--data-access-lookup-attempts",
            "9",
            "--data-access-lookup-delay",
            "1.5",
        ]
    )
    assert args.data_access_lookup_attempts == 9
    assert args.data_access_lookup_delay == 1.5
