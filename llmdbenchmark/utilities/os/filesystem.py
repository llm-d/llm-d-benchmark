"""
filesystem.py - Filesystem utility helpers.

This module provides a small, focused set of helper functions for common
filesystem operations involving directories. The functions are thin,
Pythonic wrappers around the standard library.

The utilities in this module intentionally avoid maintaining global state
and operate only on the paths provided by the caller.

Functions included:
- create_tmp_directory: Create a temporary directory and return its path
- create_directory: Create a directory (mkdir -p semantics by default)
- check_directory_exists: Test whether a directory exists
- copy_directory: Recursively copy a directory tree
- remove_directory: Recursively delete a directory tree

Error handling:
- Errors from the underlying filesystem are surfaced as standard Python
  exceptions (``OSError`` or ``FileNotFoundError``) with additional context.
- No exceptions are silently swallowed unless explicitly requested via
  function parameters (e.g., ``ignore_missing=True``).

Thread safety:
- These utilities do not provide synchronization and are not safe for
  concurrent mutation of the same filesystem paths. Although much of our
  options are not concurrent regarding filesystems in llmdbenchmark.
"""

import os
import shutil
import tempfile
from typing import Optional


def create_tmp_directory(prefix: str = "tmp_", base_dir: Optional[str] = None) -> str:
    """
    Create a temporary directory.

    Args:
        prefix: Prefix for the temporary directory name.
        base_dir: Optional base directory in which to create the temp directory.

    Returns:
        The path to the created temporary directory.

    Raises:
        OSError: If the temporary directory cannot be created.
    """
    try:
        return tempfile.mkdtemp(prefix=prefix, dir=base_dir)
    except OSError as exc:
        raise OSError(f"Failed to create temporary directory: {exc}") from exc


def create_directory(path: str, exist_ok: bool = True) -> None:
    """
    Create a directory at the given path.

    Args:
        path: Path of the directory to create.
        exist_ok: If True, no exception is raised if the directory already exists.

    Raises:
        OSError: If the directory cannot be created.
    """
    try:
        os.makedirs(path, exist_ok=exist_ok)
    except OSError as exc:
        raise OSError(f"Failed to create directory '{path}': {exc}") from exc


def check_directory_exists(path: str) -> bool:
    """
    Check whether a directory exists at the given path.

    Args:
        path: Path to check.

    Returns:
        True if the directory exists, False otherwise.
    """
    return os.path.isdir(path)


def copy_directory(source: str, destination: str, overwrite: bool = False) -> None:
    """
    Copy a directory from source to destination.

    Args:
        source: Path to the source directory.
        destination: Path to the destination directory.
        overwrite: If True, allows copying into an existing destination.

    Raises:
        FileNotFoundError: If the source directory does not exist.
        OSError: If the directory cannot be copied.
    """
    if not os.path.isdir(source):
        raise FileNotFoundError(f"Source directory '{source}' does not exist")

    try:
        shutil.copytree(source, destination, dirs_exist_ok=overwrite)
    except OSError as exc:
        raise OSError(
            f"Failed to copy directory from '{source}' to '{destination}': {exc}"
        ) from exc


def remove_directory(path: str, ignore_missing: bool = True) -> None:
    """
    Remove a directory and all of its contents.

    Args:
        path: Path of the directory to remove.
        ignore_missing: If True, no exception is raised if the directory does not exist.

    Raises:
        OSError: If the directory cannot be removed.
    """
    if not os.path.exists(path):
        if ignore_missing:
            return
        raise FileNotFoundError(f"Directory '{path}' does not exist")

    try:
        shutil.rmtree(path)
    except OSError as exc:
        raise OSError(f"Failed to remove directory '{path}': {exc}") from exc
