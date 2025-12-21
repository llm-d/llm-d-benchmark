import argparse

from llmdbenchmark.logging.logger import get_logger, set_stage
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

    subparsers = parser.add_subparsers(dest="command")
    standup.add_subcommands(subparsers)

    args = parser.parse_args()
    parse_cli(args)

    log = get_logger("llmdbenchmark")

    if args.command == "standup":
        set_stage(log, "🔧 standup")
        log.info("Preparing environment...")

        print(args)


if __name__ == "__main__":
    cli()
