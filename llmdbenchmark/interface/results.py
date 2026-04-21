"""CLI definition for the ``results`` subcommand."""

import argparse
from pathlib import Path

from llmdbenchmark.interface.commands import Command
from llmdbenchmark.result_store.commands import COMMAND_MAP

def add_subcommands(parser: argparse._SubParsersAction, parents: list[argparse.ArgumentParser] = None):
    """Register the ``results`` subcommand and its arguments."""
    parents = parents or []
    results_parser = parser.add_parser(
        Command.RESULTS.value,
        parents=parents,
        description="Interact with the llm-d central Results Store.",
        help="Store, query, diff, and pull benchmark results.",
    )

    subparsers = results_parser.add_subparsers(
        dest="results_command",
        required=True,
        title="Results Commands",
    )

    # `init` subcommand
    subparsers.add_parser("init", help="Initialize a local .result_store in the current directory.")

    # `remote` subcommand
    remote_parser = subparsers.add_parser("remote", help="Manage remotes.")
    remote_subparsers = remote_parser.add_subparsers(dest="remote_action", required=True)
    
    remote_add = remote_subparsers.add_parser("add", help="Add a remote.")
    remote_add.add_argument("name", help="Name of the remote (e.g. private)")
    remote_add.add_argument("uri", help="GCS URI (e.g. gs://my-bucket/prefix)")
    
    remote_rm = remote_subparsers.add_parser("rm", help="Remove a remote.")
    remote_rm.add_argument("name", help="Name of the remote to remove")
    
    remote_subparsers.add_parser("ls", help="List all remotes.")

    # `add` subcommand
    add_parser = subparsers.add_parser("add", help="Stage untracked benchmark runs to be pushed.")
    add_parser.add_argument("paths", nargs="+", help="Local directory paths or Run UIDs to stage")

    # `rm` subcommand
    rm_parser = subparsers.add_parser("rm", help="Unstage tracked benchmark runs.")
    rm_parser.add_argument("paths", nargs="+", help="Local directory paths or Run UIDs to untrack")

    # `status` subcommand
    subparsers.add_parser("status", help="List staged and untracked benchmark runs in the local store.")

    # `ls` subcommand (remote)
    ls_parser = subparsers.add_parser("ls", help="List benchmark runs in a remote.")
    ls_parser.add_argument("remote", help="Name of the remote (e.g. prod, staging)")
    ls_parser.add_argument("-m", "--model", help="Filter by model")
    ls_parser.add_argument("-w", "--hardware", help="Filter by hardware")

    # `push` subcommand
    push_parser = subparsers.add_parser("push", help="Push a staged run to a remote.")
    push_parser.add_argument("remote", nargs="?", default="staging", help="Remote to push to (default: staging)")
    push_parser.add_argument("path", nargs="?", help="Optional local directory path to push directly")
    push_parser.add_argument("-g", "--group", default="default", help="Group to append to remote path (default: default)")

    # `pull` subcommand
    pull_parser = subparsers.add_parser("pull", help="Pull a benchmark run from a remote.")
    pull_parser.add_argument("remote", nargs="*", default=["prod"], help="Remote to pull from (default: prod)")
    pull_parser.add_argument("--run-uid", required=True, help="Specific run UUID to pull")
    

def execute(args, logger):
    """Dispatcher for results command logic."""
    cmd = args.results_command
    executor = COMMAND_MAP.get(cmd)
    
    if executor:
        executor(args, logger)
    else:
        logger.log_error(f"Unknown results command: {cmd}")
