"""Filesystem utility helpers for directory operations and workspace management."""

import os
import shutil
import tempfile

from typing import Optional, Union
from pathlib import Path
from datetime import datetime


from llmdbenchmark.utilities.os.platform import get_user_id
from llmdbenchmark import __package_name__


def directory_exists_and_nonempty(path: Union[str, Path]) -> bool:
    """Return True if the directory exists and contains at least one entry."""
    p = Path(path)
    return p.is_dir() and any(p.iterdir())


def file_exists_and_nonzero(path: Union[str, Path]) -> bool:
    """Return True if the file exists and has a non-zero size."""
    p = Path(path)
    return p.is_file() and p.stat().st_size > 0


def create_tmp_directory(
    prefix: str = None, suffix: str = None, base_dir: Optional[Union[str, Path]] = None
) -> Path:
    """Create a temporary directory and return its path."""
    try:
        p = Path(base_dir) if base_dir is not None else None
        return Path(tempfile.mkdtemp(prefix=prefix, suffix=suffix, dir=p))
    except OSError as exc:
        raise OSError(f"Failed to create temporary directory: {exc}") from exc


def create_directory(path: Union[str, Path], exist_ok: bool = True) -> Path:
    """Create a directory at the given path and return it."""
    try:
        p = Path(path)
        os.makedirs(p, exist_ok=exist_ok)
        return p
    except OSError as exc:
        raise OSError(f"Failed to create directory '{path}': {exc}") from exc


def copy_directory(
    source: Union[str, Path], destination: Union[str, Path], overwrite: bool = False
) -> None:
    """Copy a directory tree from source to destination."""
    try:
        src = Path(source)
        dst = Path(destination)
        shutil.copytree(src, dst, dirs_exist_ok=overwrite)
    except OSError as exc:
        raise OSError(
            f"Failed to copy directory from '{source}' to '{destination}': {exc}"
        ) from exc


def get_absolute_path(path: Union[str, Path]) -> Path:
    """Resolve a path to an absolute Path, regardless of whether it exists."""
    try:
        p = Path(path).expanduser()
        abs_path = p.resolve(strict=False)
        return abs_path
    except Exception as exc:
        raise ValueError(
            f"Failed to resolve absolute path for '{path}': {exc}"
        ) from exc


_SPEC_DIR = "config/specification"
_SPEC_SUFFIX = ".yaml.j2"


def resolve_specification_file(
    name_or_path: Union[str, Path],
    base_dir: Union[str, Path, None] = None,
) -> Path:
    """Look up a spec by bare name, category/name, or path.

    Resolution order:
      1. Exact file path
      2. ``<base_dir>/config/specification/<name>.yaml.j2``
      3. Recursive glob for ``<name>.yaml.j2`` under spec dirs

    Raises FileNotFoundError (no match) or ValueError (ambiguous).
    """
    input_path = Path(name_or_path).expanduser()

    # Exact path -- just use it
    if input_path.is_file():
        return input_path.resolve()

    # Strip known suffixes so users can pass "gpu.yaml.j2" or just "gpu"
    stem = str(name_or_path)
    for ext in (".yaml.j2", ".yaml", ".j2"):
        if stem.endswith(ext):
            stem = stem[: -len(ext)]
            break

    # base_dir takes priority, fall back to package root
    search_roots: list[Path] = []
    if base_dir:
        bd = Path(base_dir).expanduser().resolve()
        spec_dir = bd / _SPEC_DIR
        if spec_dir.is_dir():
            search_roots.append(spec_dir)
    # utilities/os/filesystem.py to 4 parents up = project root
    pkg_root = Path(__file__).resolve().parent.parent.parent.parent
    pkg_spec = pkg_root / _SPEC_DIR
    if pkg_spec.is_dir() and pkg_spec not in search_roots:
        search_roots.append(pkg_spec)

    # Try category/name match first (e.g. "guides/inference-scheduling")
    for root in search_roots:
        candidate = root / f"{stem}{_SPEC_SUFFIX}"
        if candidate.is_file():
            return candidate.resolve()

    # Bare name -- glob for it
    target = f"{Path(stem).name}{_SPEC_SUFFIX}"
    matches: list[Path] = []
    for root in search_roots:
        matches.extend(root.rglob(target))

    # Dedupe in case base_dir and pkg root overlap
    unique = list({m.resolve(): m for m in matches})

    if len(unique) == 1:
        return unique[0].resolve()

    if len(unique) > 1:
        listing = "\n".join(f"  - {m}" for m in sorted(unique))
        raise ValueError(
            f"Ambiguous specification name '{name_or_path}' matches "
            f"{len(unique)} files:\n{listing}\n"
            f"Use category/name to disambiguate, e.g. "
            f"'--spec guides/{stem}' or '--spec examples/{stem}'."
        )

    # Not found -- list what's available so the user can pick
    available: list[str] = []
    for root in search_roots:
        for f in sorted(root.rglob(f"*{_SPEC_SUFFIX}")):
            rel = f.relative_to(root)
            short = str(rel).removesuffix(_SPEC_SUFFIX)
            available.append(short)

    listing = "\n".join(f"  - {s}" for s in available) if available else "  (none found)"
    raise FileNotFoundError(
        f"Specification '{name_or_path}' not found.\n\n"
        f"Available specifications:\n{listing}\n\n"
        f"Usage:\n"
        f"  --spec gpu                           # bare name\n"
        f"  --spec guides/inference-scheduling   # category/name\n"
        f"  --spec /full/path/to/spec.yaml.j2    # full path"
    )


def remove_directory(path: Union[str, Path]) -> None:
    """Remove a directory and all of its contents."""
    try:
        p = Path(path)
        shutil.rmtree(p)
    except OSError as exc:
        raise OSError(f"Failed to remove directory '{path}': {exc}") from exc


def create_workspace(workspace_dir: Optional[Union[str, Path]]) -> Path:
    """Create or ensure the workspace directory exists. Uses a temp dir if none specified."""

    if not workspace_dir:
        return create_tmp_directory(suffix=__package_name__)
    p = Path(workspace_dir)
    return create_directory(p)


def create_sub_dir_workload(
    workspace_dir: Union[str, Path], sub_dir: Optional[str] = None
) -> Path:
    """Create a run-specific subdirectory within the workspace for logs, configs, and reports."""
    p = Path(workspace_dir)
    if not sub_dir:
        prefix = get_user_id()
        suffix = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
        sub_workspace = p / f"{prefix}-{suffix}"
    else:
        sub_workspace = p / sub_dir

    return create_directory(sub_workspace)
