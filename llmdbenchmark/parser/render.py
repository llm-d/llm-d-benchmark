import sys

from pathlib import Path
from llmdbenchmark.parser.precheck import validate_base_dir, validate_specification
from llmdbenchmark.utilities.os.filesystem import (
    file_exists_and_nonzero,
    directory_exists_and_nonempty,
)

from llmdbenchmark.exceptions.exceptions import ConfigurationError
from llmdbenchmark.config import config
from llmdbenchmark.logging.logger import LLMDBenchmarkLogger, get_logger


def _get_logger() -> LLMDBenchmarkLogger:
    """
    Returns the logger for this module.
    Must be called after config.set_paths() in cli().
    """
    if config.log_dir is None:
        raise RuntimeError("Workspace log_dir is not set yet. Run CLI first.")
    return get_logger(config.log_dir, verbose=config.verbose, log_name=__name__)


def render(base_dir: Path, specification_file: Path):

    logger = _get_logger()

    templates_dir = base_dir / "templates"
    scenarios_dir = base_dir / "scenarios"

    try:
        if not file_exists_and_nonzero(specification_file):
            raise ConfigurationError(
                message="Specification file is missing or empty",
                config_file=str(specification_file),
                context={"specification_file": str(specification_file)},
            )
    except ConfigurationError:
        logger.log_error(
            f"Specification file does not exist or is empty: {specification_file}",
            exc_info=True,
        )
        sys.exit(1)

    try:
        if not directory_exists_and_nonempty(base_dir):
            raise ConfigurationError(
                message="Base directory is missing or empty",
                config_file=str(specification_file),
                context={"base_dir": str(base_dir)},
            )
    except ConfigurationError:
        logger.log_error(
            f"Base directory is missing or empty: {specification_file}",
            exc_info=True,
        )
        sys.exit(1)

    try:
        if not directory_exists_and_nonempty(templates_dir):
            raise ConfigurationError(
                message="Template directory is missing or empty",
                config_file=str(templates_dir),
                context={"templates_dir": str(templates_dir)},
            )
    except ConfigurationError:
        logger.log_error(
            f"Template directory is missing or empty: {templates_dir}",
            exc_info=True,
        )
        sys.exit(1)

    try:
        if not directory_exists_and_nonempty(scenarios_dir):
            raise ConfigurationError(
                message="Scenarios directory is missing or empty",
                config_file=str(templates_dir),
                context={"scenarios_dir": str(scenarios_dir)},
            )
    except ConfigurationError:
        logger.log_error(
            f"Scenarios directory is missing or empty: {scenarios_dir}",
            exc_info=True,
        )
        sys.exit(1)

    logger.log_info(
        f'Will use base directory found at "{base_dir}" for templates and scenarios.'
    )
    logger.log_info(f'Will render specification file found at "{specification_file}"')
