"""standup --no-pvc: flag plumbing, overrides, and step gates."""

from __future__ import annotations

import argparse

from llmdbenchmark.interface import standup as standup_interface


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    standup_interface.add_subcommands(subparsers, parents=[])
    return parser.parse_args(argv)


def test_standup_no_pvc_defaults_false() -> None:
    assert _parse(["standup"]).no_pvc is False


def test_standup_no_pvc_parses_true() -> None:
    assert _parse(["standup", "--no-pvc"]).no_pvc is True


def test_standup_no_pvc_env_var(monkeypatch) -> None:
    monkeypatch.setenv("LLMDBENCH_NO_PVC", "1")
    assert _parse(["standup"]).no_pvc is True


def test_no_pvc_overrides_for_standup() -> None:
    from llmdbenchmark.cli import _no_pvc_standup_overrides

    args = argparse.Namespace(command="standup", no_pvc=True)
    assert _no_pvc_standup_overrides(args) == {
        "modelservice": {"uriProtocol": "hf"},
        "standalone": {"mountModelVolume": False},
    }


def test_no_pvc_overrides_empty_without_flag() -> None:
    from llmdbenchmark.cli import _no_pvc_standup_overrides

    assert (
        _no_pvc_standup_overrides(argparse.Namespace(command="standup", no_pvc=False))
        == {}
    )


def test_no_pvc_overrides_empty_for_run() -> None:
    """run --no-pvc must NOT redirect model storage -- run deploys nothing."""
    from llmdbenchmark.cli import _no_pvc_standup_overrides

    assert (
        _no_pvc_standup_overrides(argparse.Namespace(command="run", no_pvc=True)) == {}
    )
