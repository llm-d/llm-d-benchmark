import argparse

import os

from llmdbenchmark.logging import (
    log_info,
    log_warning,
    log_error,
    log_debug,
    log_blank,
    setup_logger,
    ConfigurationError,
)

from llmdbenchmark.utilities.os.filesystem import create_tmp_directory
from llmdbenchmark.interface import standup


AUTO_TMP_DIR = "AUTO_TMP"


def parse_cli(args):
    if args.workspace == f"{AUTO_TMP_DIR}":
        args.workspace = create_tmp_directory(prefix="vezio")
        print(args.workspace)


def cli():
    parser = argparse.ArgumentParser(
        prog="llmdbenchmark",
        description="Provision and Drive Experiments for LLM workloads focused on analyzing"
        "performance of llm-d and vllm inference platforms.",
    )

    parser.add_argument(
        "--workspace",
        "--ws",
        default=f"{AUTO_TMP_DIR}",
        help="Supply a workspace directory for placing "
        "generated items and logs, otherwise the default action is to create a "
        "temporary directory on your system.",
    )

    parser.add_argument(
        "--specification",
        "--spec",
        required=True,
        help="File specifying the experiment (if any), template location, and scenario location. "
        "This file will be used to generate a plan that will be used as part of provisioning, "
        "running experiments, and other actions for this library.",
    )

    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable debug logging to console"
    )

    subparsers = parser.add_subparsers(dest="command")
    standup.add_subcommands(subparsers)

    args = parser.parse_args()
    parse_cli(args)

    logger = setup_logger(verbose=args.verbose)

    if args.command == "standup":
        log_info(
            "Running 🔧 standup",
            logger=logger,
        )
        log_debug(
            "Preparing environment...",
            extra={"emoji": "🚀"},
            logger=logger,
        )
        log_blank(
            logger=logger,
        )
        log_info(
            "Preparing environment...",
            extra={"emoji": "⏳"},
            logger=logger,
        )
        log_warning(
            "Preparing environment...",
            extra={"emoji": "📜"},
            logger=logger,
        )

        # log_error(
        #     "Preparing environment...",
        #     extra={"emoji": "📜"},
        #     logger=logger,
        # )


if __name__ == "__main__":
    cli()


# Need to specify the WORKSPACE for logging directory
