"""Availability of the external CLIs the compress-script tests shell out to.

``remote_compress_script`` runs in a pod, and its delete-safety flags
(``--anchored``, ``--no-wildcards``) are GNU-only; macOS ships bsdtar, which
refuses them. Tests that run that script on the driver therefore need a GNU tar
and zstd on PATH, and must carry ``requires_compress_tools`` so they skip rather
than fail where one is missing.

Importing this module installs the PATH shim below, so every module that shells
out to the script has to import it rather than rely on another module having
done so. Import happens during collection, ahead of any test running.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest


def _find_gnu_tar() -> str | None:
    """Absolute path to a GNU tar, or None. Homebrew's gnu-tar installs it as
    ``gtar``, so the PATH's ``tar`` is not the only candidate."""
    for name in ("tar", "gtar", "gnutar"):
        path = shutil.which(name)
        if path is None:
            continue
        try:
            probe = subprocess.run(
                [path, "--version"], capture_output=True, text=True, check=False
            )
        except OSError:
            continue
        if "GNU tar" in probe.stdout:
            return path
    return None


GNU_TAR = _find_gnu_tar()
ZSTD = shutil.which("zstd")

# Skipping locally is fine; skipping in CI would mean every guard on the only copy
# of a result set passes vacuously.
if os.environ.get("CI"):
    if ZSTD is None:
        raise RuntimeError("zstd missing in CI: these tests would skip silently")
    if GNU_TAR is None:
        raise RuntimeError("GNU tar missing in CI: these tests would skip silently")

# When GNU tar exists but is not the PATH's ``tar`` (macOS with gnu-tar installed),
# expose it as ``tar`` through a PATH shim so the script under test picks it up.
# Prepended to os.environ here, at import: the per-test shims build their PATH from
# os.environ too, so their overrides still land in front of this one.
if GNU_TAR is not None and os.path.basename(GNU_TAR) != "tar":
    _gnu_shim = Path(tempfile.mkdtemp(prefix="llmdbench-gnutar-"))
    (_gnu_shim / "tar").symlink_to(GNU_TAR)
    os.environ["PATH"] = f"{_gnu_shim}:{os.environ['PATH']}"

_BREW = {"zstd": "zstd", "GNU tar": "gnu-tar"}
_MISSING = [
    name for name, path in (("zstd", ZSTD), ("GNU tar", GNU_TAR)) if path is None
]

# One mark rather than a list, so the same object works as a decorator, as
# ``pytestmark``, and as ``pytest.param(..., marks=...)``.
requires_compress_tools = pytest.mark.skipif(
    bool(_MISSING),
    reason=(
        f"needs {' and '.join(_MISSING)} on PATH"
        f" (macOS: brew install {' '.join(_BREW[name] for name in _MISSING)})"
    ),
)
