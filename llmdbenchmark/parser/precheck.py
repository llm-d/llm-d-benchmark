"""
precheck.py -- RenderPreCheck module for llmdbenchmark specification validation and rendering.

This module provides the RenderPreCheck class which handles validation of specification
files, templates, and scenarios directories before rendering benchmark configurations.
"""

import sys
from pathlib import Path
from typing import Tuple
from llmdbenchmark.utilities.os.filesystem import (
    file_exists_and_nonzero,
    directory_exists_and_nonempty,
)
from llmdbenchmark.exceptions.exceptions import ConfigurationError
from llmdbenchmark.config import config
from llmdbenchmark.logging.logger import LLMDBenchmarkLogger, get_logger


class RenderPreCheck:
    """
    Handles validation and rendering of specification files for LLMDBenchmark.
    """

    @staticmethod
    def _get_logger() -> LLMDBenchmarkLogger:
        """
        Returns the logger for rendering operations.
        Must be called after config.set_paths() in CLI.
        """
        if config.log_dir is None:
            raise RuntimeError("Workspace log_dir is not set yet. Run CLI first.")
        return get_logger(config.log_dir, verbose=config.verbose, log_name=__name__)

    @staticmethod
    def _validate_path(
        path: Path, validator: callable, error_msg: str, logger: LLMDBenchmarkLogger
    ) -> None:
        """
        Validate a path using the provided validator function.

        Args:
            path: Path to validate
            validator: Function to check path validity
            error_msg: Error message to log if validation fails
            logger: Logger instance for error logging
        """
        try:
            if not validator(path):
                raise ConfigurationError(
                    message=error_msg,
                    config_file=str(path),
                    context={"path": str(path)},
                )
        except ConfigurationError:
            logger.log_error(f"{error_msg}: {path}", exc_info=True)
            raise

    @staticmethod
    def _validate_all_paths(
        base_dir: Path, specification_file: Path, logger: LLMDBenchmarkLogger
    ) -> Tuple[Path, Path]:
        """
        Validate all required paths for rendering.

        Returns:
            Tuple of (templates_dir, scenarios_dir)
        """

        # TODO: Place these someplace else...but pull them into this...
        templates_dir = base_dir / "templates"
        scenarios_dir = base_dir / "scenarios"

        # Define validation checks
        validations = [
            (
                specification_file,
                file_exists_and_nonzero,
                "Specification file is missing or empty",
            ),
            (
                base_dir,
                directory_exists_and_nonempty,
                "Base directory is missing or empty",
            ),
            (
                templates_dir,
                directory_exists_and_nonempty,
                "Template directory is missing or empty",
            ),
            (
                scenarios_dir,
                directory_exists_and_nonempty,
                "Scenarios directory is missing or empty",
            ),
        ]

        for path, validator, error_msg in validations:
            RenderPreCheck._validate_path(path, validator, error_msg, logger)

        return templates_dir, scenarios_dir

    @staticmethod
    def eval(base_dir: Path, specification_file: Path):
        """
        Validate required paths and log setup info before rendering a specification.

        Args:
            base_dir: Base directory containing templates and scenarios.
            specification_file: Path to the specification file.
        """
        logger = RenderPreCheck._get_logger()

        try:
            templates_dir, scenarios_dir = RenderPreCheck._validate_all_paths(
                base_dir, specification_file, logger
            )

            logger.log_info(
                f'Will use base directory found at "{base_dir}" for templates and scenarios.'
            )
            logger.log_info(
                f'Will render specification file found at "{specification_file}"'
            )

        except ConfigurationError:
            sys.exit(1)
