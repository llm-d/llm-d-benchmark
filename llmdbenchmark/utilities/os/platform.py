"""Platform detection utilities for system name, architecture, and OS flags."""

from dataclasses import dataclass, fields
import platform
import getpass


@dataclass(frozen=True)
class PlatformInfo:
    """Normalized platform information for the host system."""

    system: str
    machine: str

    @property
    def is_mac(self) -> bool:
        """True if the system is macOS (darwin)."""
        return self.system == "darwin"

    @property
    def is_linux(self) -> bool:
        """True if the system is Linux."""
        return self.system.startswith("linux")

    def to_dict(self) -> dict[str, object]:
        """Return a dict including fields and computed properties."""
        result = {f.name: getattr(self, f.name) for f in fields(self)}
        result["is_mac"] = self.is_mac
        result["is_linux"] = self.is_linux
        return result

    def __str__(self) -> str:
        """Return a human-readable printable string."""
        return (
            f"System   : {self.system}\n"
            f"Machine  : {self.machine}\n"
            f"Is Mac   : {self.is_mac}\n"
            f"Is Linux : {self.is_linux}"
        )


def get_platform_info() -> PlatformInfo:
    """Return a PlatformInfo for the current system."""
    return PlatformInfo(system=platform.system().lower(), machine=platform.machine())


def get_platform_dict() -> dict[str, object]:
    """Return platform information as a dictionary."""
    return get_platform_info().to_dict()


def get_user_id() -> str:
    """:return: String identifying the currently active system user as ``name``"""
    return getpass.getuser()
