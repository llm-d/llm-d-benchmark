"""install.sh and deps.py must agree on which tools are required.

install.sh is the installer; deps.py is the runtime pre-flight check. When the
two disagree, install.sh hard-fails on a tool the runtime considers optional
(or one it has never heard of), so the install dies on a tool nothing needs.
"""

from __future__ import annotations

import re
from pathlib import Path

from llmdbenchmark.executor.deps import OPTIONAL_TOOLS, REQUIRED_TOOLS

PROJECT_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = PROJECT_ROOT / "install.sh"

# Needed by install.sh itself (curl fetches the pinned binaries, git clones the
# planner), never invoked by the Python at runtime, so deps.py does not list
# them.
INSTALLER_ONLY = {"curl", "git"}

# `tools="..."`, ignoring the conditional `tools="$tools oc"` append that adds
# a kube client when neither kubectl nor oc is on PATH.
_TOOLS_RE = re.compile(r'^(?P<var>(?:optional_)?tools)="(?P<list>[^"$]+)"$')
_AUTOINSTALL_RE = re.compile(r'^autoinstall_optional="(?P<list>[^"$]+)"$')
_EVAL_RE = re.compile(r'eval\s+"\$\{?install_func\}?"')
_GUARDED_EVAL_RE = re.compile(r'eval\s+"\$\{?install_func\}?"[^|]*\|\|')


def _tool_lists() -> tuple[list[str], list[str]]:
    required: list[str] = []
    optional: list[str] = []
    for line in INSTALL_SH.read_text(encoding="utf-8").splitlines():
        match = _TOOLS_RE.match(line)
        if not match:
            continue
        names = match.group("list").split()
        if match.group("var") == "optional_tools":
            optional.extend(names)
        else:
            required.extend(names)
    assert required, f"No tools= assignment found in {INSTALL_SH}"
    assert optional, f"No optional_tools= assignment found in {INSTALL_SH}"
    return required, optional


def _autoinstall_optional() -> list[str]:
    """The subset of optional_tools install.sh installs rather than reports."""
    for line in INSTALL_SH.read_text(encoding="utf-8").splitlines():
        match = _AUTOINSTALL_RE.match(line)
        if match:
            return match.group("list").split()
    raise AssertionError(f"No autoinstall_optional= assignment in {INSTALL_SH}")


def test_install_sh_required_tools_are_required_in_deps() -> None:
    required, _ = _tool_lists()
    demoted = sorted(
        t for t in required if t in OPTIONAL_TOOLS and t not in INSTALLER_ONLY
    )
    unknown = sorted(
        t
        for t in required
        if t not in OPTIONAL_TOOLS
        and t not in REQUIRED_TOOLS
        and t not in INSTALLER_ONLY
    )
    assert not demoted, f"required by install.sh but OPTIONAL in deps.py: {demoted}"
    assert not unknown, f"required by install.sh but UNKNOWN to deps.py: {unknown}"


def test_install_sh_optional_tools_are_known_to_deps() -> None:
    _, optional = _tool_lists()
    assert not sorted(t for t in optional if t not in OPTIONAL_TOOLS), (
        "optional in install.sh but not in deps.OPTIONAL_TOOLS: "
        f"{sorted(set(optional) - set(OPTIONAL_TOOLS))}"
    )


def test_install_func_eval_is_best_effort() -> None:
    # install.sh runs under `set -euo pipefail`, so an unguarded
    # `eval "$install_func"` aborts the whole install the moment a package
    # manager fails, before the `command -v` re-check that decides whether the
    # failure is fatal at all. Any `||` handler counts as guarded; matching on
    # the eval itself (not on `|| true`) keeps a rename or an added redirect
    # from slipping past, and the emptiness check keeps the test from passing
    # vacuously if the call sites disappear.
    evals = [
        (i, line)
        for i, line in enumerate(
            INSTALL_SH.read_text(encoding="utf-8").splitlines(), start=1
        )
        if _EVAL_RE.search(line)
    ]
    assert evals, f'No `eval "$install_func"` call site found in {INSTALL_SH}'
    offenders = [(i, line) for i, line in evals if not _GUARDED_EVAL_RE.search(line)]
    assert not offenders, f"unguarded eval of install_func: {offenders}"


def test_oc_is_never_auto_installed() -> None:
    # install_oc_* unpacks the OpenShift client tarball and moves its bundled
    # kubectl into /usr/local/bin, replacing the user's. install.sh may only
    # do that when no kube client exists at all (the `tools="$tools oc"`
    # append), never from the optional loop, which runs precisely when kubectl
    # IS present.
    _, optional = _tool_lists()
    autoinstall = _autoinstall_optional()
    assert "oc" in optional, "oc should still be reported by the optional loop"
    assert "oc" not in autoinstall, "install.sh must not auto-install oc"
    assert not sorted(set(autoinstall) - set(optional)), (
        "autoinstall_optional is not a subset of optional_tools: "
        f"{sorted(set(autoinstall) - set(optional))}"
    )
