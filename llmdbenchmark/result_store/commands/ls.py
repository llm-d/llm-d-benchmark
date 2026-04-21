"""Command to list benchmark runs in a remote."""

import sys
import fnmatch
from pathlib import Path
from llmdbenchmark.result_store.config import ConfigManager
from llmdbenchmark.result_store.utils import color_pad, parse_report_path
from llmdbenchmark.result_store.commands import register_command
from llmdbenchmark.result_store.client import get_storage_client, get_fallback_client

@register_command("ls")
def execute(args, logger):
    config = ConfigManager()
    try:
        uri = config.get_remote(args.remote)
        client = get_storage_client(uri)
        
        try:
            blob_names = client.ls(uri)
        except Exception as exception:
            fallback_client = get_fallback_client(client)
            if fallback_client:
                logger.log_warning(f"Direct access failed, trying fallback: {exception}")
                client = fallback_client
                blob_names = client.ls(uri)
            else:
                raise exception

        if not blob_names:
            logger.log_info(f"No objects found in remote '{args.remote}'.")
            return

        # Parse URI to get base prefix for relative path calculation
        if uri.startswith("gs://"):
             parts = uri[5:].split("/", 1)
             base_prefix = parts[1] if len(parts) > 1 else ""
        else:
             base_prefix = ""

        runs = []
        for name in blob_names:
            if name.endswith("report_v0.2.yaml"):
                relative_name = name
                if base_prefix:
                    prefix_check = base_prefix if base_prefix.endswith("/") else base_prefix + "/"
                    if relative_name.startswith(prefix_check):
                        relative_name = relative_name[len(prefix_check):]
                        
                run_info = parse_report_path(relative_name)
                if not run_info:
                    logger.log_warning(f"Ignoring remote object with unexpected path structure: {name}")
                    continue
                    
                # Apply filters
                if args.model:
                    if '*' in args.model or '?' in args.model:
                        if not fnmatch.fnmatch(run_info["model"], args.model):
                            continue
                    elif args.model != run_info["model"]:
                        continue
                        
                if args.hardware:
                    if '*' in args.hardware or '?' in args.hardware:
                        if not fnmatch.fnmatch(run_info["hardware"], args.hardware):
                            continue
                    elif args.hardware != run_info["hardware"]:
                        continue

                run_info["blob_name"] = name
                runs.append(run_info)

        if not runs:
            logger.log_info(f"No benchmark runs found matching filters in remote '{args.remote}'.")
            return

        logger.log_info(f"Runs in {args.remote} ({uri}):")
        logger.log_plain(f"{'Run UID':<8} | {'Scenario':<25} | {'Model':<30} | {'Hardware':<15}")
        logger.log_plain("-" * 85)
        
        by_group = {}
        for run in runs:
            group_name = run.get("group", "default")
            if group_name not in by_group:
                by_group[group_name] = []
            by_group[group_name].append(run)
            
        for group_name in sorted(by_group.keys()):
            logger.log_plain(f"[{group_name}]")
            for run in by_group[group_name]:
                short_uid = run['run_uid'][:8] if len(run['run_uid']) > 8 else run['run_uid']
                logger.log_plain(f"  {color_pad(short_uid, 8)} | {color_pad(run['scenario'], 25)} | {color_pad(run['model'], 30)} | {color_pad(run['hardware'], 15)}")
    except ValueError as exception:
        logger.log_error(str(exception))
        sys.exit(1)
    except Exception as exception:
        logger.log_error(f"Failed to list remote: {exception}")
        sys.exit(1)
