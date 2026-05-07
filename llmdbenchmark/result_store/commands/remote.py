"""Command to manage remotes."""

import sys
from llmdbenchmark.result_store.config import ConfigManager
from llmdbenchmark.result_store.commands import register_command

@register_command("remote")
def execute(args, logger):
    config = ConfigManager()
    action = args.remote_action
    if action == "add":
        config.add_remote(args.name, args.uri)
        logger.log_info(f"Added remote '{args.name}' -> {args.uri}")
    elif action == "rm":
        try:
            config.remove_remote(args.name)
            logger.log_info(f"Removed remote '{args.name}'.")
        except ValueError as exception:
            logger.log_error(str(exception))
            sys.exit(1)
    elif action == "ls":
        remotes = config.list_remotes()
        for name, uri in remotes.items():
            logger.log_info(f"{name}\t{uri}")
