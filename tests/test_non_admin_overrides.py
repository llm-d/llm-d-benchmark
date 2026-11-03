"""--non-admin: router Prometheus auth is disabled via a synthetic override.

Router Prometheus auth (``router.monitoring.prometheus.auth.enabled``) makes
the router chart create a cluster-scoped ClusterRoleBinding, which a
namespace-level user cannot create. ``--non-admin`` therefore injects a
global override that turns it off for every command that renders plans,
while an explicit ``--set`` of the same key still wins.
"""

from __future__ import annotations

from argparse import Namespace
from unittest.mock import MagicMock

import pytest

from llmdbenchmark.parser.cli_overrides import GLOBAL_SELECTOR

AUTH_DISABLED = {"router": {"monitoring": {"prometheus": {"auth": {"enabled": False}}}}}


def _auth_enabled(built: dict):
    """Return router.monitoring.prometheus.auth.enabled from the global bucket."""
    return (
        built.get(GLOBAL_SELECTOR, {})
        .get("router", {})
        .get("monitoring", {})
        .get("prometheus", {})
        .get("auth", {})
        .get("enabled")
    )


def _build(
    command="standup", non_admin=True, set_overrides=None, cluster=None, logger=None
):
    from llmdbenchmark.cli import _build_setup_overrides_by_stack

    args = Namespace(
        command=command,
        non_admin=non_admin,
        set_overrides=set_overrides,
        cluster_config_overrides=cluster,
    )
    return _build_setup_overrides_by_stack(args, logger or MagicMock())


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("LLMDBENCH_SET", raising=False)


# ---------------------------------------------------------------------------
# cli._non_admin_overrides -- the synthetic override itself
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("command", ["standup", "run", "experiment", "update"])
def test_non_admin_disables_prometheus_auth_for_every_command(command) -> None:
    from llmdbenchmark.cli import _non_admin_overrides

    args = Namespace(command=command, non_admin=True)
    assert _non_admin_overrides(args) == AUTH_DISABLED


def test_no_override_without_flag() -> None:
    from llmdbenchmark.cli import _non_admin_overrides

    assert _non_admin_overrides(Namespace(command="standup", non_admin=False)) == {}


def test_no_override_when_attribute_missing() -> None:
    """Commands that never register --non-admin must not break."""
    from llmdbenchmark.cli import _non_admin_overrides

    assert _non_admin_overrides(Namespace(command="standup")) == {}


# ---------------------------------------------------------------------------
# cli._build_setup_overrides_by_stack -- precedence with --set / cluster-config
# ---------------------------------------------------------------------------


def test_build_puts_auth_disabled_in_global_bucket() -> None:
    assert _auth_enabled(_build()) is False


def test_build_without_flag_leaves_auth_untouched() -> None:
    assert _build(non_admin=False) == {}


def test_explicit_set_beats_non_admin() -> None:
    built = _build(set_overrides=["router.monitoring.prometheus.auth.enabled=true"])
    assert _auth_enabled(built) is True


def test_unrelated_set_survives_alongside_non_admin() -> None:
    built = _build(set_overrides=["decode.replicas=1"])
    assert built[GLOBAL_SELECTOR]["decode"]["replicas"] == 1
    assert _auth_enabled(built) is False


def test_non_admin_keeps_sibling_monitoring_keys_from_set() -> None:
    """The override is a deep merge: it must not drop other monitoring keys."""
    built = _build(set_overrides=["router.monitoring.prometheus.enabled=true"])
    prometheus = built[GLOBAL_SELECTOR]["router"]["monitoring"]["prometheus"]
    assert prometheus["enabled"] is True
    assert prometheus["auth"]["enabled"] is False


def test_non_admin_logs_once() -> None:
    logger = MagicMock()
    _build(logger=logger)
    messages = [call.args[0] for call in logger.log_info.call_args_list]
    assert sum("--non-admin" in m for m in messages) == 1
