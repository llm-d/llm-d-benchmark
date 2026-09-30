"""Tests for the ``update`` subcommand argparse wiring.

Validates that:
- the subcommand registers and accepts its own flags
- --set is inherited from the shared benchmark parser
- every standup flag is on update too
- on/off flags can be left unset, or turned off
- --reuse-invocation defaults to on
"""

from __future__ import annotations

import pytest

from llmdbenchmark.cli import build_parser
from llmdbenchmark.interface.commands import Command


def _parse(*argv):
    return build_parser().parse_args(["update", *argv])


def _dests(command):
    parser = build_parser()
    subparsers = next(
        action
        for action in parser._actions
        if hasattr(action, "choices") and action.choices
    )
    return {action.dest for action in subparsers.choices[command]._actions}


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_update_is_registered():
    assert _parse().command == Command.UPDATE.value


# ---------------------------------------------------------------------------
# Inherited flags
# ---------------------------------------------------------------------------


def test_set_is_inherited_and_repeatable():
    args = _parse("--set", "decode.replicas=2", "--set", "router.epp.replicas=1")
    assert args.set_overrides == ["decode.replicas=2", "router.epp.replicas=1"]


def test_cluster_config_is_inherited():
    assert _parse("--cc", "/tmp/cc.yaml").cluster_config == "/tmp/cc.yaml"


def test_every_standup_flag_is_on_update():
    """A flag missing on update would silently fall back to a default."""
    missing = _dests(Command.STANDUP.value) - _dests(Command.UPDATE.value)
    assert not missing, f"update is missing: {sorted(missing)}"


# ---------------------------------------------------------------------------
# update-specific flags
# ---------------------------------------------------------------------------


def test_force_defaults_off():
    assert _parse().force is False
    assert _parse("--force").force is True


def test_component_accepts_a_list():
    assert _parse("--component", "epp,vllm").component == "epp,vllm"


def test_step_override():
    assert _parse("-s", "6,8").step == "6,8"


def test_reuse_invocation_defaults_on():
    assert _parse().reuse_invocation is True


def test_reuse_invocation_can_be_disabled():
    assert _parse("--no-reuse-invocation").reuse_invocation is False


def test_skip_smoketest_defaults_off():
    assert _parse().skip_smoketest is False
    assert _parse("--skip-smoketest").skip_smoketest is True


def test_monitoring_is_tristate():
    assert _parse().monitoring is None
    assert _parse("--monitoring").monitoring is True
    assert _parse("--no-monitoring").monitoring is False


def test_prism_is_tristate():
    assert _parse().prism is None
    assert _parse("--no-prism").prism is False


# ---------------------------------------------------------------------------
# On/off flags: unset is not the same as off
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("dest", ["wva", "epp_keda_saturation", "full_infra", "no_pvc"])
def test_on_off_flags_default_to_unset(dest):
    assert getattr(_parse(), dest) is None


@pytest.mark.parametrize(
    "flag, dest",
    [
        ("--no-wva", "wva"),
        ("--no-epp-keda-saturation", "epp_keda_saturation"),
        ("--no-full-infra", "full_infra"),
        ("--pvc", "no_pvc"),
    ],
)
def test_on_off_flags_can_be_turned_off(flag, dest):
    assert getattr(_parse(flag), dest) is False


class _Parsed(Exception):
    pass


def _cli_args(monkeypatch, *argv):
    """The args as cli() has them once the env vars are merged in."""
    from llmdbenchmark import cli

    def stop(args):
        raise _Parsed(args)

    monkeypatch.setattr(cli, "_resolve_quiet_plan", stop)
    monkeypatch.setattr("sys.argv", ["llmdbenchmark", "--spec", "x", *argv])
    with pytest.raises(_Parsed) as parsed:
        cli.cli()
    return parsed.value.args[0]


@pytest.mark.parametrize(
    "dest, env",
    [
        ("wva", "LLMDBENCH_WVA"),
        ("epp_keda_saturation", "LLMDBENCH_EPP_KEDA_SATURATION"),
    ],
)
def test_update_on_off_flags_stay_unset_without_env(monkeypatch, dest, env):
    """False would count as typed and turn off what the standup enabled."""
    monkeypatch.delenv(env, raising=False)
    assert getattr(_cli_args(monkeypatch, "update"), dest) is None


@pytest.mark.parametrize(
    "dest, env",
    [
        ("wva", "LLMDBENCH_WVA"),
        ("epp_keda_saturation", "LLMDBENCH_EPP_KEDA_SATURATION"),
    ],
)
def test_on_off_flags_still_read_the_env(monkeypatch, dest, env):
    monkeypatch.setenv(env, "true")
    assert getattr(_cli_args(monkeypatch, "update"), dest) is True
    assert getattr(_cli_args(monkeypatch, "standup"), dest) is True


@pytest.mark.parametrize(
    "flag, env",
    [
        ("--no-wva", "LLMDBENCH_WVA"),
        ("--no-epp-keda-saturation", "LLMDBENCH_EPP_KEDA_SATURATION"),
    ],
)
def test_an_off_flag_against_the_env_does_nothing(monkeypatch, capsys, flag, env):
    from llmdbenchmark import cli

    monkeypatch.setenv(env, "true")
    monkeypatch.setattr(
        cli, "_resolve_quiet_plan", lambda _a: pytest.fail("must stop before")
    )
    monkeypatch.setattr("sys.argv", ["llmdbenchmark", "--spec", "x", "update", flag])
    with pytest.raises(SystemExit) as exited:
        cli.cli()
    assert exited.value.code == 1
    assert env in capsys.readouterr().err


def test_standup_on_off_flags_still_default_off():
    args = build_parser().parse_args(["standup"])
    assert (args.wva, args.no_pvc, args.full_infra) == (False, False, False)


def test_timeouts_are_ints_when_given():
    args = _parse("--modelservice-deploy-timeout", "42")
    assert args.modelservice_deploy_timeout == 42
