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
from typing import Optional, Union
from pathlib import Path


def create_tmp_directory(
    prefix: str = None, suffix: str = None, base_dir: Optional[Path] = None
) -> Path:
    """
    Create a temporary directory.

    Args:
        prefix: Prefix for the temporary directory name.
        suffix: Suffix for the temporary directory name.
        base_dir: Optional base directory in which to create the temp directory.

    Returns:
        The path to the created temporary directory.

    Raises:
        OSError: If the temporary directory cannot be created.
    """
    try:
        return Path(tempfile.mkdtemp(prefix=prefix, suffix=suffix, dir=base_dir))
    except OSError as exc:
        raise OSError(f"Failed to create temporary directory: {exc}") from exc


def create_directory(path: Path, exist_ok: bool = True) -> None:
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


def copy_directory(source: str, destination: str, overwrite: bool = False) -> None:
    """
    Copy a directory from source to destination.

    Args:
        source: Path to the source directory.
        destination: Path to the destination directory.
        overwrite: If True, allows copying into an existing destination.

    Raises:
        OSError: If the directory cannot be copied.
    """
    try:
        shutil.copytree(source, destination, dirs_exist_ok=overwrite)
    except OSError as exc:
        raise OSError(
            f"Failed to copy directory from '{source}' to '{destination}': {exc}"
        ) from exc


def get_absolute_path(path: Union[str, Path]) -> Path:
    """
    Convert a relative or absolute path to an absolute Path object.

    Args:
        path: A string or Path representing the file/directory path.

    Returns:
        A Path object representing the absolute path.

    Raises:
        ValueError: If the path cannot be resolved.
    """
    try:
        p = Path(path)
        abs_path = p.resolve(strict=True)
        return abs_path
    except Exception as exc:
        raise ValueError(
            f"Failed to resolve absolute path for '{path}': {exc}"
        ) from exc


def remove_directory(path: str) -> None:
    """
    Remove a directory and all of its contents.

    Args:
        path: Path of the directory to remove.

    Raises:
        OSError: If the directory cannot be removed.
    """
    try:
        shutil.rmtree(path)
    except OSError as exc:
        raise OSError(f"Failed to remove directory '{path}': {exc}") from exc
