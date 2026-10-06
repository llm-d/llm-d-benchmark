"""Tests for hostPath PV cleanup scoping in ``DeleteResourcesStep``.

PVs are cluster-scoped and the ``usage=model-cache`` label carries no namespace,
so on a shared cluster the label alone also selects other tenants' PVs. Deleting
one that is stuck Terminating behind a pv-protection finalizer never returns and
hangs the whole teardown.

Behavior under test:
- A PV whose claimRef namespace is ours is deleted.
- A PV belonging to another namespace is kept.
- An unbound PV (no claimRef) is kept.
- The delete is bounded by --timeout so a stuck finalizer cannot hang teardown.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from llmdbenchmark.teardown.steps.step_03_delete_resources import DeleteResourcesStep


class _StubLogger:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def log_info(self, msg: str, **_: Any) -> None:
        self.messages.append(msg)

    def log_warning(self, msg: str, **_: Any) -> None:
        self.messages.append(f"WARN: {msg}")

    def log_error(self, msg: str, **_: Any) -> None:
        self.messages.append(f"ERR: {msg}")


@dataclass
class _StubResult:
    success: bool = True
    stdout: str = ""
    stderr: str = ""


@dataclass
class _StubCmd:
    """Returns a canned `get pv` listing and records the deletes."""

    listing: str = ""
    kube_calls: list[tuple] = field(default_factory=list)

    def kube(self, *args: str, **_: Any) -> _StubResult:
        self.kube_calls.append(args)
        if args and args[0] == "get":
            return _StubResult(success=True, stdout=self.listing)
        return _StubResult(success=True)

    def deleted(self) -> list[str]:
        """The pv/<name> targets passed to a delete, ignoring flags."""
        out = []
        for call in self.kube_calls:
            if not call or call[0] != "delete":
                continue
            out += [a for a in call[1:] if str(a).startswith("pv/")]
        return out


@dataclass
class _StubContext:
    logger: _StubLogger = field(default_factory=_StubLogger)


def _run(listing: str, namespaces: list[str]) -> tuple[_StubCmd, _StubContext]:
    cmd = _StubCmd(listing=listing)
    context = _StubContext()
    DeleteResourcesStep._delete_host_path_pvs(cmd, context, namespaces)
    return cmd, context


def test_deletes_pv_bound_to_own_namespace():
    cmd, _ = _run("model-pvc-hostpath-pv aruocco\n", ["aruocco"])
    assert cmd.deleted() == ["pv/model-pvc-hostpath-pv"]


def test_keeps_pv_of_another_tenant():
    # The real incident: another namespace's PV, stuck Terminating for days
    # behind kubernetes.io/pv-protection, blocked teardown indefinitely.
    cmd, context = _run("model-pvc-evgensh-8b-hostpath-pv evgensh-8b\n", ["aruocco"])
    assert cmd.deleted() == []
    assert any("Keeping pv/" in m for m in context.logger.messages)


def test_keeps_unbound_pv():
    cmd, _ = _run("orphan-pv\n", ["aruocco"])
    assert cmd.deleted() == []


def test_mixed_listing_only_deletes_ours():
    listing = (
        "mine-pv aruocco\ntheirs-pv evgensh-8b\nharness-pv aruocco-harness\norphan-pv\n"
    )
    cmd, _ = _run(listing, ["aruocco", "aruocco-harness"])
    assert sorted(cmd.deleted()) == ["pv/harness-pv", "pv/mine-pv"]


def test_delete_is_bounded_by_timeout():
    cmd, _ = _run("mine-pv aruocco\n", ["aruocco"])
    delete_call = next(a for a in cmd.kube_calls if a and a[0] == "delete")
    assert any(str(arg).startswith("--timeout=") for arg in delete_call)


def test_no_namespaces_does_nothing():
    cmd, _ = _run("mine-pv aruocco\n", [])
    assert cmd.kube_calls == []
