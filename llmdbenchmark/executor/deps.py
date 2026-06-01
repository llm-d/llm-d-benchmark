"""System dependency checker for required CLI tools (kubectl, helm, etc.)."""

import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field

# Minimum host tool versions for the Helm 4 toolchain. helmfile < 1.5 probes
# helm with the Helm-3-only `helm version --client`, which Helm 4 removed --
# helmfile then panics deep inside `helmfile template` with an opaque error.
# These let standup fail fast with an actionable message instead.
MIN_HELM_MAJOR = 4
MIN_HELMFILE_VERSION = (1, 5, 0)


REQUIRED_TOOLS = ["kubectl", "helm", "helmfile", "jq", "yq"]
OPTIONAL_TOOLS = ["oc", "kustomize", "skopeo", "rsync", "make"]


@dataclass
class DependencyCheckResult:
    """Result of a system dependency check."""

    available: list[str] = field(default_factory=list)
    missing_required: list[str] = field(default_factory=list)
    missing_optional: list[str] = field(default_factory=list)

    @property
    def has_missing_required(self) -> bool:
        """Return True if any required tools are missing."""
        return len(self.missing_required) > 0

    def summary(self) -> str:
        """Return a human-readable summary of check results."""
        lines = []
        if self.available:
            lines.append(f"Available: {', '.join(self.available)}")
        if self.missing_required:
            lines.append(f"Missing (REQUIRED): {', '.join(self.missing_required)}")
        if self.missing_optional:
            lines.append(f"Missing (optional): {', '.join(self.missing_optional)}")
        return "\n".join(lines)


def check_tool_available(tool_name: str) -> bool:
    """Check if a CLI tool is available on PATH."""
    return shutil.which(tool_name) is not None


def check_system_dependencies(
    required_only: bool = False,
    extra_required: list[str] | None = None,
) -> DependencyCheckResult:
    """Check required (and optionally optional) tools on PATH."""
    result = DependencyCheckResult()

    required = list(REQUIRED_TOOLS)
    if extra_required:
        required.extend(extra_required)

    for tool in required:
        if check_tool_available(tool):
            result.available.append(tool)
        else:
            result.missing_required.append(tool)

    if not required_only:
        for tool in OPTIONAL_TOOLS:
            if tool in required:
                continue
            if check_tool_available(tool):
                result.available.append(tool)
            else:
                result.missing_optional.append(tool)

    return result


def check_python_version() -> tuple[bool, str]:
    """Return (meets_requirement, version_string) for Python >= 3.11."""
    version = (
        f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    )
    meets = sys.version_info >= (3, 11)
    return meets, version


def _tool_version_output(cmd: list[str]) -> str | None:
    """Run a `--version`-style command and return its trimmed output, or None.

    Safe to call for helm/helmfile: ``helmfile --version`` only prints
    helmfile's own version and (unlike ``helmfile template``) does not probe
    helm, so it works even with the stale helmfile this guard exists to catch.
    """
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return (out.stdout or out.stderr).strip()


def check_helm_version(min_major: int = MIN_HELM_MAJOR) -> tuple[bool, str]:
    """Return (ok, version_string). ok is False if helm is missing, its
    output is unparseable, or its major version is below *min_major*."""
    raw = _tool_version_output(["helm", "version", "--short"])
    if not raw:
        return False, "not found"
    m = re.search(r"v?(\d+)\.(\d+)\.(\d+)", raw)
    if not m:
        return False, raw
    return int(m.group(1)) >= min_major, raw


def check_helmfile_version(
    min_version: tuple[int, int, int] = MIN_HELMFILE_VERSION,
) -> tuple[bool, str]:
    """Return (ok, version_string). ok is False if helmfile is missing,
    unparseable, or older than *min_version*."""
    raw = _tool_version_output(["helmfile", "--version"])
    if not raw:
        return False, "not found"
    m = re.search(r"v?(\d+)\.(\d+)\.(\d+)", raw)
    if not m:
        return False, raw
    found = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return found >= min_version, raw
