"""Tests for accelerator-based router command selection in step_05_kustomize_deploy."""

from __future__ import annotations


import pytest

from llmdbenchmark.kustomize.readme_parser import (
    CommandPhase,
    GuideCommand,
)
from llmdbenchmark.standup.steps.step_05_kustomize_deploy import (
    KustomizeDeployStep,
)


def _router_cmd(values_file: str) -> GuideCommand:
    return GuideCommand(
        raw=(
            "helm upgrade --install wide-ep router "
            f"-f guides/wide-ep/router/{values_file}"
        ),
        phase=CommandPhase.ROUTER,
    )


@pytest.fixture
def router_commands() -> list[GuideCommand]:
    return [
        _router_cmd("base.values.yaml"),
        _router_cmd("xpu.values.yaml"),
        _router_cmd("amd.values.yaml"),
    ]


def _values_files(commands: list[GuideCommand]) -> list[str]:
    return [gc.raw.rsplit("/", 1)[-1] for gc in commands]


@pytest.mark.parametrize(
    ("accel_backend", "expected"),
    [
        ("gpu", ["base.values.yaml"]),
        ("xpu", ["base.values.yaml", "xpu.values.yaml"]),
        ("amd", ["base.values.yaml", "amd.values.yaml"]),
        ("amd/mi300x", ["base.values.yaml", "amd.values.yaml"]),
    ],
)
def test_select_router_commands_filters_other_accels(
    router_commands, accel_backend, expected
):
    selected = KustomizeDeployStep._select_router_commands(
        router_commands, accel_backend
    )
    assert _values_files(selected) == expected
