# Telemetry module

import atexit
from typing import Optional
from llmdbenchmark.config import config
from llmdbenchmark.logging.logger import get_logger

_telemetry_instance = None

def init_telemetry(logger=None):
    """Initialize the telemetry singleton."""
    global _telemetry_instance
    if _telemetry_instance is not None:
        return _telemetry_instance

    if not config.telemetry_enabled:
        return None

    if logger is None:
        logger = get_logger(config.log_dir, config.verbose, __name__)

    if config.telemetry_provider == "http":
        if config.telemetry_endpoint:
            from llmdbenchmark.telemetry.http import HttpTelemetryProvider
            _telemetry_instance = HttpTelemetryProvider(
                config.telemetry_endpoint, 
                config.telemetry_api_key, 
                logger
            )
            
            def wait_for_telemetry():
                logger.log_info("Waiting for telemetry to finish sending...", emoji="⏳")
                _telemetry_instance.stop()
                
            atexit.register(wait_for_telemetry)
        else:
            logger.log_info("Telemetry enabled but provider 'http' not fully configured (missing endpoint).", emoji="⚠️")
    else:
        logger.log_info(f"Telemetry enabled but provider '{config.telemetry_provider}' not supported.", emoji="⚠️")
        
    return _telemetry_instance

def get_telemetry():
    """Get the telemetry singleton instance."""
    return _telemetry_instance
