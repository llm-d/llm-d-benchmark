from llmdbenchmark.config import config
from llmdbenchmark.logging.logger import get_logger


def get_logger_for_gateway():
    """
    Returns the logger for the gateway module.
    Must be called after config.set_paths() in cli().
    """
    if config.log_dir is None:
        raise RuntimeError("Workspace log_dir is not set yet. Run CLI first.")
    return get_logger(config.log_dir, verbose=config.verbose, log_name=__name__)


def test_gateway():
    logger = get_logger_for_gateway()
    logger.log_info("GATEWAY")
