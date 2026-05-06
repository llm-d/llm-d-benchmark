"""Environment variable helpers for CLI argument defaults."""

import os


def env(name: str, default=None):
    """Return env var value or default. For use as argparse ``default=``."""
    return os.environ.get(name, default)


def env_bool(name: str, default: bool = False) -> bool:
    """Return env var as boolean. Truthy: '1', 'true', 'yes' (case-insensitive)."""
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes")


def env_int(name: str, default: int | None = None) -> int | None:
    """Return env var as int, or default if not set / not parseable."""
    val = os.environ.get(name)
    if val is None:
        return default
    try:
        return int(val)
    except ValueError:
        return default
