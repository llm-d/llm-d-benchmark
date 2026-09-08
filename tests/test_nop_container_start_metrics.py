"""Tests for the container_start field on BenchmarkVllmMetrics.

container_start is the elapsed-seconds anchor from the main container's start
to Pod-Ready, persisted alongside the existing pod_start anchor and surfaced by
dump() so it lands in the result artifact.
"""

import importlib

nf = importlib.import_module("workload.harnesses.nop_functions")


def test_container_start_defaults_to_zero():
    metrics = nf.BenchmarkVllmMetrics()
    assert metrics.container_start == 0.0


def test_dump_includes_container_start():
    metrics = nf.BenchmarkVllmMetrics()
    metrics.container_start = 12.5
    dumped = metrics.dump()
    assert dumped["container_start"] == 12.5


def test_dump_keeps_pod_start_alongside_container_start():
    metrics = nf.BenchmarkVllmMetrics()
    metrics.pod_start = 30.0
    metrics.container_start = 12.5
    dumped = metrics.dump()
    assert dumped["pod_start"] == 30.0
    assert dumped["container_start"] == 12.5
