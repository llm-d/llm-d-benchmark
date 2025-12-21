"""
Platform Information Utilities

This module provides tools to retrieve and represent normalized platform
information about the current system. It is designed for scripts, tooling,
or Python programs that need to branch behavior based on operating system
or machine architecture.

Classes:
- PlatformInfo:
    Encapsulates system name, machine architecture, and OS convenience flags.
    Provides:
      - `to_dict()` method for dictionary access
      - `__str__()` for readable printing

Functions:
- get_platform_info() -> PlatformInfo:
    Returns a PlatformInfo object for the current system.

- get_platform_dict() -> dict[str, object]:
    Returns a PlatformInfo object for the current system as a dictionary.

"""

from dataclasses import dataclass, fields
import platform
import getpass


@dataclass(frozen=True)
class PlatformInfo:
    """
    Encapsulates normalized platform information in an immutable object representing
    the host system launching the library.

    Provides both a human-readable string and dictionary access,
    including computed properties for common OS checks.
    """

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
        """
        Return a dictionary representation including fields and computed properties.
        """
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
    """
    Retrieve platform information as a PlatformInfo object.

    Returns:
        PlatformInfo: Contains system name, machine architecture, and OS flags.
    """
    return PlatformInfo(system=platform.system().lower(), machine=platform.machine())


def get_platform_dict() -> dict[str, object]:
    """
    Retrieve the current platform information as a dictionary.

    Returns:
        dict[str, object]: Dictionary including system name, machine architecture,
                           and convenience OS flags ('is_mac', 'is_linux').
    """
    return get_platform_info().to_dict()


def get_user_id() -> str:
    """:return: String identifying the currently active system user as ``name``"""
    return getpass.getuser()
