from llmdbench.parser.systemparser import SystemParser
from llmdbench.logging.logger import get_logger, set_stage

import json
import os
import argparse
import yaml


def cli():
    """
    Command-line interface for llmdbench.

    Subcommands:
      - plan: Merge and render YAMLs (previously 'configure')
      - prepare: Prepare environment or data before execution
      - execute: Run workloads or apply configurations
      - destroy: Clean up or rollback resources
      - report: Generate summary or benchmark reports
    """
    logger = get_logger("llmdbench.cli")

    parser = argparse.ArgumentParser(
        prog="llmdbench",
        description="Manage and benchmark llmd configurations.",
    )

    parser.add_argument(
        "--workspace",
        required=True,
        default=".",
        help="Workspace directory used as the root for configs, outputs, etc.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # --------------------------
    # plan
    # --------------------------
    plan_parser = subparsers.add_parser(
        "plan",
        help="Merge charts/images and render templates into a versioned YAML plan.",
    )
    plan_parser.add_argument(
        "--experiment",
        required=True,
        help="Path to the experiment file to plan.",
    )

    # --------------------------
    # prepare
    # --------------------------
    prepare_parser = subparsers.add_parser(
        "prepare", help="Prepare the environment or dependencies for execution."
    )
    prepare_parser.add_argument(
        "--config", required=False, help="Optional path to configuration YAML."
    )

    # --------------------------
    # execute
    # --------------------------
    execute_parser = subparsers.add_parser(
        "execute", help="Execute the benchmark or deployment defined in the plan."
    )
    execute_parser.add_argument(
        "--plan", required=True, help="Path to the planned YAML configuration."
    )

    # --------------------------
    # destroy
    # --------------------------
    destroy_parser = subparsers.add_parser(
        "destroy", help="Tear down or rollback any created resources."
    )
    destroy_parser.add_argument(
        "--plan", required=False, help="Path to the plan used for deployment."
    )

    # --------------------------
    # report
    # --------------------------
    report_parser = subparsers.add_parser(
        "report", help="Generate a report or analysis from execution results."
    )
    report_parser.add_argument(
        "--input", required=False, help="Path to execution results or metrics."
    )
    report_parser.add_argument(
        "--output", default="report.yaml", help="Path to save the report output."
    )

    # --------------------------
    # Parse and dispatch
    # --------------------------
    args = parser.parse_args()

    with open(args.experiment, "r") as f:
        data = yaml.safe_load(f)
    default_file = os.path.join(args.workspace, data["values"]["path"].lstrip("/"))
    template_path = os.path.join(args.workspace, data["templates"]["path"].lstrip("/"))
    scenario_path = os.path.join(args.workspace, data["scenario"]["path"].lstrip("/"))

    # Regardless - we need create a plan - otherwise we won't have context of
    # what to todo - in the future we can "import" a context to "rerun" a plan.
    system = SystemParser(default_file, template_path, args.workspace, scenario_path)
    system.parse()

    if args.command == "plan":
        set_stage(logger, "🔧 PLAN")
        logger.info("Creating execution and deployment plan...")
        logger.info(f"Plan saved to {args.workspace}")
        system.plan_to_yaml()
    elif args.command == "prepare":
        set_stage(logger, "🔧 PREPARE")
        logger.info("Preparing environment...")
    elif args.command == "execute":
        set_stage(logger, "🚀 EXECUTE")
        logger.info(f"Executing plan: {args.plan}")
    elif args.command == "destroy":
        set_stage(logger, "🧹 DESTROY")
        logger.info("Cleaning up resources...")
    elif args.command == "report":
        set_stage(logger, "📊 REPORT")
        logger.info("Generating report...")
    else:
        parser.print_help()


if __name__ == "__main__":
    cli()
