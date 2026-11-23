"""A release record in a wedged state makes the next helmfile apply a
no-op, so objects a failed install left out are never retried. Dropping
only the newest record leaves the previous revision the latest, so the
apply becomes a real upgrade from it. The removed record is handed back
so the caller can put it back when the apply fails before writing a new
one.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from llmdbenchmark.utilities.kube_helpers import (
    WEDGED_HELM_STATES,
    clear_wedged_release,
    restore_release_record,
)


class _StubLogger:
    def __init__(self) -> None:
        self.warnings: list[str] = []

    def log_warning(self, msg: str) -> None:
        self.warnings.append(msg)


@dataclass
class _StubResult:
    success: bool = True
    stdout: str = ""
    stderr: str = ""


@dataclass
class _StubCmd:
    status: str = ""
    status_fails: bool = False
    status_stderr: str = ""
    delete_fails: bool = False
    record_fails: bool = False
    record_yaml: str = "apiVersion: v1\nkind: Secret\n"
    restore_stderr: str = ""
    deleted: list[tuple[tuple[str, ...], str]] = field(default_factory=list)
    created: list[tuple[tuple[str, ...], str, str]] = field(default_factory=list)

    def helm(self, *args: str, **_: Any) -> _StubResult:
        if args[0] == "status":
            if self.status_fails:
                return _StubResult(success=False, stderr=self.status_stderr)
            return _StubResult(stdout=self.status)
        return _StubResult()

    def kube(self, *args: str, **kw: Any) -> _StubResult:
        if args[0] == "get" and args[1] == "secret":
            if self.record_fails:
                return _StubResult(success=False, stderr="boom")
            return _StubResult(stdout=self.record_yaml)
        if args[0] == "delete":
            self.deleted.append((args, kw.get("namespace")))
            if self.delete_fails:
                return _StubResult(success=False, stderr="boom")
        if args[0] == "create":
            record_path = Path(args[args.index("-f") + 1])
            self.created.append(
                (args, kw.get("namespace"), record_path.read_text(encoding="utf-8"))
            )
            if self.restore_stderr:
                return _StubResult(success=False, stderr=self.restore_stderr)
        return _StubResult()


def _status_json(state: str, version: int | None = 1) -> str:
    body = {"info": {"status": state}}
    if version is not None:
        body["version"] = version
    return json.dumps(body)


def _newest_delete(revision: int) -> tuple[tuple[str, ...], str]:
    return (
        (
            "delete",
            "secret",
            f"sh.helm.release.v1.rel-router.v{revision}",
            "--ignore-not-found=true",
        ),
        "nikita-dev",
    )


def _all_records_delete() -> tuple[tuple[str, ...], str]:
    return (
        (
            "delete",
            "secret",
            "-l",
            "owner=helm,name=rel-router",
            "--ignore-not-found=true",
        ),
        "nikita-dev",
    )


@pytest.mark.parametrize("state,revision", [("failed", 2), ("pending-install", 1)])
def test_wedged_state_drops_only_the_newest_record(state, revision):
    assert state in WEDGED_HELM_STATES
    cmd = _StubCmd(status=_status_json(state, version=revision))

    cleared, record = clear_wedged_release(
        cmd, "nikita-dev", "rel-router", _StubLogger()
    )

    assert cleared
    assert record == cmd.record_yaml
    assert cmd.deleted == [_newest_delete(revision)]


@pytest.mark.parametrize(
    "state,version",
    [("uninstalling", 3), ("failed", None)],
)
def test_unrecoverable_state_clears_every_record(state, version):
    # an interrupted uninstall may have removed any subset of the
    # objects; no record left behind can be trusted
    cmd = _StubCmd(status=_status_json(state, version=version))

    cleared, record = clear_wedged_release(
        cmd, "nikita-dev", "rel-router", _StubLogger()
    )

    assert cleared
    assert record is None
    assert cmd.deleted == [_all_records_delete()]


def test_unreadable_record_still_clears_without_one():
    cmd = _StubCmd(status=_status_json("failed", version=2), record_fails=True)

    cleared, record = clear_wedged_release(
        cmd, "nikita-dev", "rel-router", _StubLogger()
    )

    assert cleared
    assert record is None
    assert cmd.deleted


def test_healthy_states_and_malformed_status_are_no_ops():
    for status in (
        _status_json("deployed"),
        "not json",
        '{"info": {}}',
        "null",
    ):
        cmd = _StubCmd(status=status)

        cleared, record = clear_wedged_release(
            cmd, "nikita-dev", "rel-router", _StubLogger()
        )

        assert not cleared
        assert record is None
        assert cmd.deleted == []


def test_status_failure_is_silent_only_for_a_missing_release():
    missing = _StubCmd(status_fails=True, status_stderr="Error: release: not found")
    refused = _StubCmd(status_fails=True, status_stderr="forbidden")
    logger = _StubLogger()

    cleared, _record = clear_wedged_release(missing, "nikita-dev", "rel-router", logger)
    cleared_refused, _record = clear_wedged_release(
        refused, "nikita-dev", "rel-router", logger
    )

    assert not cleared and not cleared_refused
    assert missing.deleted == [] and refused.deleted == []
    # only the refusal is worth a warning
    assert len(logger.warnings) == 1
    assert "Could not check" in logger.warnings[0]


def test_failed_delete_is_logged():
    cmd = _StubCmd(status=_status_json("uninstalling"), delete_fails=True)
    logger = _StubLogger()

    cleared, _record = clear_wedged_release(cmd, "nikita-dev", "rel-router", logger)

    assert not cleared
    assert any("Could not clear" in w for w in logger.warnings)


def test_restore_creates_the_record_in_the_namespace():
    cmd = _StubCmd()
    logger = _StubLogger()
    record = "apiVersion: v1\nkind: Secret\nmetadata:\n  name: kept\n"

    restore_release_record(cmd, "nikita-dev", "rel-router", record, logger)

    # create, not apply: the payload must not land in a last-applied
    # annotation, and a record helm wrote back must read as done
    assert len(cmd.created) == 1
    args, namespace, content = cmd.created[0]
    assert args[0] == "create"
    assert namespace == "nikita-dev"
    assert content == record
    assert any("restored" in w for w in logger.warnings)


def test_restore_outcomes():
    record = "apiVersion: v1\nkind: Secret\n"

    # a record helm already wrote back is the desired end state
    exists = _StubCmd(
        restore_stderr="Error from server (AlreadyExists): secrets "
        '"sh.helm.release.v1.rel-router.v2" already exists'
    )
    exists_logger = _StubLogger()
    restore_release_record(exists, "nikita-dev", "rel-router", record, exists_logger)
    assert any("restored" in w for w in exists_logger.warnings)

    failed = _StubCmd(restore_stderr="boom")
    failed_logger = _StubLogger()
    restore_release_record(failed, "nikita-dev", "rel-router", record, failed_logger)
    assert any("Could not restore" in w for w in failed_logger.warnings)
