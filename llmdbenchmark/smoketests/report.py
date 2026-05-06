"""Smoketest check results and report types."""

from dataclasses import dataclass, field


@dataclass
class CheckResult:
    """Outcome of a single validation check."""

    name: str
    passed: bool
    expected: str = ""
    actual: str = ""
    message: str = ""
    group: str = ""  # e.g. "prefill" or "decode" -- used for grouped log output
    is_header: bool = False  # True for group header lines (not a real check)

    def __str__(self) -> str:
        if self.is_header:
            return f"📋 {self.message}"
        icon = "✅" if self.passed else "❌"
        return f"{icon} {self.name}: {self.message}"


@dataclass
class SmoketestReport:
    """Aggregated results from one or more validation checks."""

    checks: list[CheckResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """True if every check passed."""
        return all(c.passed for c in self.checks)

    @property
    def total(self) -> int:
        """Return the total number of checks in the report."""
        return len(self.checks)

    @property
    def passed_count(self) -> int:
        """Return the number of checks that passed."""
        return sum(1 for c in self.checks if c.passed)

    def summary(self) -> str:
        """Return a one-line pass/total summary string."""
        return f"{self.passed_count}/{self.total} checks passed"

    def add(self, check: CheckResult) -> None:
        """Append a single check result to the report."""
        self.checks.append(check)

    def merge(self, other: "SmoketestReport") -> None:
        """Merge another report's checks into this one."""
        self.checks.extend(other.checks)

    def errors(self) -> list[str]:
        """Return human-readable error strings for failed checks."""
        return [str(c) for c in self.checks if not c.passed]
