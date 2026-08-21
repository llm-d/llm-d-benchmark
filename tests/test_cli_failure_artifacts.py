"""Tests for the workspace pointer printed when a phase fails.

A failed phase exits through ``sys.exit(1)`` before its success summary
runs, so without this the run that needs its logs is the only one that
never says where they are.
"""

from unittest.mock import MagicMock

import pytest

from llmdbenchmark.cli import (
    PhaseError,
    _execute_standup,
    _log_failure_artifacts,
)
from llmdbenchmark.config import config


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """Point the config singleton at a throwaway workspace."""
    monkeypatch.setattr(config, "workspace", tmp_path)
    return tmp_path


def _messages(logger) -> str:
    return "\n".join(str(c.args[0]) for c in logger.log_error.call_args_list)


def test_the_workspace_is_named_when_a_phase_fails(workspace):
    logger = MagicMock()

    _log_failure_artifacts(logger)

    assert str(workspace) in _messages(logger)


def test_the_captured_container_logs_are_pointed_at_when_they_exist(workspace):
    logs = workspace / "setup" / "logs"
    logs.mkdir(parents=True)
    (logs / "nok8s-envoy-chat.log").write_text("boom", encoding="utf-8")
    logger = MagicMock()

    _log_failure_artifacts(logger)

    assert str(logs) in _messages(logger)


def test_a_workspace_without_captured_logs_names_only_itself(workspace):
    """A phase can fail before any step writes a log; don't cite a missing dir."""
    logger = MagicMock()

    _log_failure_artifacts(logger)

    joined = _messages(logger)
    assert str(workspace) in joined
    assert "setup/logs" not in joined


def test_nothing_is_claimed_before_the_workspace_is_configured(monkeypatch):
    """Argument parsing can fail ahead of ``setup_workspace``."""
    monkeypatch.setattr(config, "workspace", None)
    logger = MagicMock()

    _log_failure_artifacts(logger)

    assert logger.log_error.call_args_list == []


def test_a_failed_standup_reports_the_workspace(workspace, monkeypatch):
    """The path users actually hit: standup raises, and must still point home."""
    logs = workspace / "setup" / "logs"
    logs.mkdir(parents=True)
    monkeypatch.setattr(
        "llmdbenchmark.cli._do_standup",
        MagicMock(side_effect=PhaseError("Standup failed:\nEnvoy never answered")),
    )
    logger = MagicMock()

    with pytest.raises(SystemExit) as exc:
        _execute_standup(MagicMock(), logger, MagicMock())

    assert exc.value.code == 1
    joined = _messages(logger)
    assert "Envoy never answered" in joined
    assert str(logs) in joined
