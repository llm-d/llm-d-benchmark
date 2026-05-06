"""Unified cloud storage upload for benchmark results.

Provides a single upload implementation used by both per-pod upload
(step_06, during result collection) and bulk upload (step_09, final
safety-net).  Normalises on ``gcloud storage cp`` for GCS and
``aws s3 cp`` for S3, matching the original bash ``upload_results``
function.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from llmdbenchmark.executor.context import ExecutionContext


def upload_results_dir(
    cmd,
    local_path: Path,
    output: str,
    context: ExecutionContext,
    relative_path: str | None = None,
) -> str | None:
    """Upload a single local directory to cloud storage.

    Args:
        cmd: CommandExecutor instance.
        local_path: Local directory to upload.
        output: Destination URI (``gs://...``, ``s3://...``, or ``"local"``).
        context: Execution context (for dry-run and logging).
        relative_path: Relative path appended to *output*.  If ``None``,
            computed by stripping ``context.run_results_dir()`` from
            *local_path*.

    Returns:
        Error message string on failure, or ``None`` on success.
    """
    if output == "local":
        return None

    if relative_path is None:
        results_base = context.run_results_dir()
        try:
            relative_path = str(local_path.relative_to(results_base))
        except ValueError:
            relative_path = local_path.name

    if context.dry_run:
        context.logger.log_info(
            f"[DRY RUN] Would upload {relative_path} \u2192 "
            f"{output}/{relative_path}/"
        )
        return None

    if output.startswith("gs://"):
        result = cmd.execute(
            f"gcloud storage cp --recursive "
            f"{local_path}/ {output}/{relative_path}/",
            check=False,
        )
        if not result.success:
            return (
                f"GCS upload failed for {relative_path}: "
                f"{result.stderr[:200]}"
            )
        context.logger.log_info(
            f"Uploaded {relative_path} \u2192 {output}/{relative_path}/",
            emoji="\u2601\ufe0f",
        )
    elif output.startswith("s3://"):
        result = cmd.execute(
            f"aws s3 cp --recursive "
            f"{local_path}/ {output}/{relative_path}/",
            check=False,
        )
        if not result.success:
            return (
                f"S3 upload failed for {relative_path}: "
                f"{result.stderr[:200]}"
            )
        context.logger.log_info(
            f"Uploaded {relative_path} \u2192 {output}/{relative_path}/",
            emoji="\u2601\ufe0f",
        )
    else:
        context.logger.log_warning(
            f"Unknown output destination '{output}' \u2014 skipping upload "
            f"for {relative_path}"
        )

    return None


def upload_all_results(
    cmd,
    results_dir: Path,
    output: str,
    context: ExecutionContext,
) -> str | None:
    """Upload the entire results directory to cloud storage.

    Used as a final safety-net / bulk upload in step_09.  Uploads
    all sub-directories under *results_dir* to *output*.

    Returns:
        Error message string on failure, or ``None`` on success.
    """
    if output == "local":
        return None

    if not results_dir.exists() or not any(results_dir.iterdir()):
        return None  # Nothing to upload

    if context.dry_run:
        context.logger.log_info(
            f"[DRY RUN] Would upload results from {results_dir} to {output}"
        )
        return None

    context.logger.log_info(f"Uploading all results to {output}...")

    if output.startswith("gs://"):
        result = cmd.execute(
            f"gcloud storage cp --recursive {results_dir}/* {output}/",
            check=False,
        )
        if not result.success:
            return f"GCS upload failed: {result.stderr[:200]}"
        context.logger.log_info(f"Results uploaded to {output}")

    elif output.startswith("s3://"):
        result = cmd.execute(
            f"aws s3 cp --recursive {results_dir}/ {output}/",
            check=False,
        )
        if not result.success:
            return f"S3 upload failed: {result.stderr[:200]}"
        context.logger.log_info(f"Results uploaded to {output}")

    else:
        return f"Unknown output destination: {output}"

    return None
