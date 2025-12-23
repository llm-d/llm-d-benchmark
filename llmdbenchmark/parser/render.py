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


class RenderSpecification:
    """
    Renders Specification for Model Infras
    """

    @staticmethod
    def _get_logger() -> LLMDBenchmarkLogger:
        """
        Returns the logger for rendering operations.
        """
        return get_logger(config.log_dir, verbose=config.verbose, log_name=__name__)
