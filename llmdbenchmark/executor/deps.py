"""System dependency checker for required CLI tools (kubectl, helm, etc.)."""

import shutil
import sys
from dataclasses import dataclass, field


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
    version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    meets = sys.version_info >= (3, 11)
    return meets, version
