"""
filesystem.py — Filesystem utility helpers.

This module provides a small, focused collection of helper functions for
common filesystem operations involving directories. The functions are thin,
Pythonic wrappers around the standard library and are designed to work
consistently with both string paths and ``pathlib.Path`` objects.

The utilities in this module:
- Avoid maintaining global state
- Operate only on paths explicitly provided by the caller
- Normalize inputs internally to ``Path`` objects
- Return ``Path`` objects where applicable

In addition to generic filesystem helpers, this module includes lightweight
project-specific helpers for creating and managing llmdbenchmark workspace
directories.

Error handling:
- Filesystem errors are surfaced as standard Python exceptions (primarily
  ``OSError``) with additional contextual information.
- Exceptions are not silently swallowed.

Thread safety:
- These utilities do not provide synchronization and are not safe for
  concurrent mutation of the same filesystem paths.
- The llmdbenchmark runtime does not currently perform concurrent filesystem
  mutations using these helpers.
"""

import os
import shutil
import tempfile

from typing import Optional, Union
from pathlib import Path
from datetime import datetime


from llmdbenchmark.utilities.os.platform import get_user_id
from llmdbenchmark import __package_name__


def directory_exists_and_nonempty(path: Union[str, Path]) -> bool:
    """
    Check if a directory exists and contains at least one file or subdirectory.

    Args:
        path (Union[str, Path]): Path to the directory.

    Returns:
        bool: True if the directory exists and is non-empty, False otherwise.
    """
    p = Path(path)
    return p.is_dir() and any(p.iterdir())


def file_exists_and_nonzero(path: Union[str, Path]) -> bool:
    """
    Check if a file exists and has a non-zero size.

    Args:
        path (Union[str, Path]): Path to the file.

    Returns:
        bool: True if the file exists and is non-empty, False otherwise.
    """
    p = Path(path)
    return p.is_file() and p.stat().st_size > 0


def create_tmp_directory(
    prefix: str = None, suffix: str = None, base_dir: Optional[Union[str, Path]] = None
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
        p = Path(base_dir) if base_dir is not None else None
        return Path(tempfile.mkdtemp(prefix=prefix, suffix=suffix, dir=p))
    except OSError as exc:
        raise OSError(f"Failed to create temporary directory: {exc}") from exc


def create_directory(path: Union[str, Path], exist_ok: bool = True) -> Path:
    """
    Create a directory at the given path.

    Args:
        path: Path of the directory to create.
        exist_ok: If True, no exception is raised if the directory already exists.

    Returns:
        The path to the created directory.

    Raises:
        OSError: If the directory cannot be created.
    """
    try:
        p = Path(path)
        os.makedirs(p, exist_ok=exist_ok)
        return p
    except OSError as exc:
        raise OSError(f"Failed to create directory '{path}': {exc}") from exc


def copy_directory(
    source: Union[str, Path], destination: Union[str, Path], overwrite: bool = False
) -> None:
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
        src = Path(source)
        dst = Path(destination)
        shutil.copytree(src, dst, dirs_exist_ok=overwrite)
    except OSError as exc:
        raise OSError(
            f"Failed to copy directory from '{source}' to '{destination}': {exc}"
        ) from exc


def get_absolute_path(path: Union[str, Path]) -> Path:
    """
    Convert a relative or absolute path to an absolute Path object.

    This will NOT care if the path exists or not, this is merely to construct
    the actual absolute path.

    Args:
        path: A string or Path representing the file/directory path.

    Returns:
        A Path object representing the absolute path.

    Raises:
        ValueError: If the path cannot be resolved.
    """
    try:
        p = Path(path).expanduser()
        abs_path = p.resolve(strict=False)
        return abs_path
    except Exception as exc:
        raise ValueError(
            f"Failed to resolve absolute path for '{path}': {exc}"
        ) from exc


def remove_directory(path: Union[str, Path]) -> None:
    """
    Remove a directory and all of its contents.

    Args:
        path: Path of the directory to remove.

    Raises:
        OSError: If the directory cannot be removed.
    """
    try:
        p = Path(path)
        shutil.rmtree(p)
    except OSError as exc:
        raise OSError(f"Failed to remove directory '{path}': {exc}") from exc


def create_workspace(workspace_dir: Optional[Union[str, Path]]) -> Path:
    """
    Create a workspace directory.
    If `workspace_dir`is None then a temporary directory is created.
    Otherwise, ensures the directory exists. We'll need to do this since we allow
    the user to NOT specify a workspace, and we need to actually create one, somewhere.
    """

    if not workspace_dir:
        return create_tmp_directory(suffix=__package_name__)
    p = Path(workspace_dir)
    return create_directory(p)


def create_sub_dir_workload(
    workspace_dir: Union[str, Path], sub_dir: Optional[str] = None
) -> Path:
    """
    Create a subdirectory within the workspace.
    If `sub_dir` is not provided, generates a unique name using user ID and package name.

    This sub_dir within the overall workspace is where the "current" run of llmdbenchmark
    will place materials generated, such as logs and generated values, and reports.
    """
    p = Path(workspace_dir)
    if not sub_dir:
        prefix = get_user_id()
        suffix = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
        sub_workspace = p / f"{prefix}-{suffix}"
    else:
        sub_workspace = p / sub_dir

    return create_directory(sub_workspace)
