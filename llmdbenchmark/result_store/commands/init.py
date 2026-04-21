"""Command to initialize a local result store."""

import sys
from llmdbenchmark.result_store.store import StoreManager
from llmdbenchmark.result_store.commands import register_command

@register_command("init")
def execute(args, logger):
    try:
        store_dir, created = StoreManager.init_store()
        if created:
            logger.log_info(f"Initialized empty Result Store in {store_dir}")
        else:
            logger.log_info(f"Result Store already exists at {store_dir}")
    except Exception as exception:
        logger.log_error(f"Failed to initialize store: {exception}")
        sys.exit(1)
