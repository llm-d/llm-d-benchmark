"""Command to pull benchmark runs from a remote."""

import sys
import fnmatch
from pathlib import Path
from llmdbenchmark.result_store.config import ConfigManager
from llmdbenchmark.result_store.store import StoreManager, StoreNotFound
from llmdbenchmark.result_store.utils import parse_report_path
from llmdbenchmark.result_store.commands import register_command
from llmdbenchmark.result_store.client import get_storage_client, get_fallback_client

@register_command("pull")
def execute(args, logger):
    config = ConfigManager()
    try:
        remote_name = args.remote[0] if isinstance(args.remote, list) else args.remote
        uri = config.get_remote(remote_name)
        client = get_storage_client(uri)
        
        # 1. Find the runs
        logger.log_info(f"Resolving run '{args.run_uid}' in {remote_name}...")
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
            logger.log_error(f"No objects found in remote '{remote_name}'.")
            sys.exit(1)

        # Parse URI to get base prefix
        if uri.startswith("gs://"):
             parts = uri[5:].split("/", 1)
             base_prefix = parts[1] if len(parts) > 1 else ""
        else:
             base_prefix = ""

        matching_runs = []
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
                    
                run_uid = run_info["run_uid"]
                if '*' in args.run_uid or '?' in args.run_uid:
                    if fnmatch.fnmatch(run_uid, args.run_uid):
                        run_info["blob_name"] = name
                        matching_runs.append(run_info)
                elif run_uid == args.run_uid or run_uid.startswith(args.run_uid):
                    run_info["blob_name"] = name
                    matching_runs.append(run_info)
                    
        # Deduplicate by run_uid
        unique_runs = {}
        for run in matching_runs:
            unique_runs[run["run_uid"]] = run
        matching_runs = list(unique_runs.values())
                
        if len(matching_runs) == 0:
            logger.log_error(f"Run UID '{args.run_uid}' not found in {remote_name}.")
            sys.exit(1)
            
        is_wildcard = '*' in args.run_uid or '?' in args.run_uid
        if len(matching_runs) > 1 and not is_wildcard:
            logger.log_error(f"Ambiguous UID '{args.run_uid}'. Found multiple matches:")
            for run in matching_runs:
                logger.log_error(f"  {run['run_uid']} ({run['scenario']}/{run['model']})")
            sys.exit(1)
            
        logger.log_info(f"Found {len(matching_runs)} matching runs. Pulling...")
        
        for target_run in matching_runs:
            group = target_run.get("group", "default")
            scenario = target_run.get("scenario", "missing")
            full_uid = target_run["run_uid"]
            short_uid = full_uid[:8]
            blob_name = target_run["blob_name"]
            
            # Construct target prefix from blob_name (remove report_v0.2.yaml)
            target_prefix = blob_name.rsplit("/", 1)[0]
            
            if uri.startswith("gs://"):
                bucket_name = uri[5:].split("/", 1)[0]
                full_uri = f"gs://{bucket_name}/{target_prefix}"
            else:
                full_uri = f"{uri.rstrip('/')}/{target_prefix}"
            
            # 2. Construct workspace path
            ws_name = f"{remote_name}_{group}_{short_uid}"
            try:
                store_root = StoreManager.find_store_root()
                ws_dir = store_root / "workspaces" / ws_name
            except StoreNotFound:
                logger.log_info("Store not initialized. Putting workspace in current directory.")
                ws_dir = Path.cwd() / "workspaces" / ws_name
            
            logger.log_info(f"Reconstructing workspace at {ws_dir}...")
            
            # 3. Create structure
            results_dir = ws_dir / "results"
            results_dir.mkdir(parents=True, exist_ok=True)
            
            plan_scen_dir = ws_dir / "plan" / scenario
            plan_scen_dir.mkdir(parents=True, exist_ok=True)
            
            # 4. Pull files
            target_dest = results_dir / full_uid
            if target_dest.exists():
                if sys.stdout.isatty():
                    ans = input(f"Run {full_uid} already exists in workspace. Overwrite? [y/N]: ").strip().lower()
                    if ans != 'y':
                        logger.log_info(f"Skipping {short_uid}.")
                        continue
                else:
                    logger.log_error(f"Run {full_uid} already exists in workspace. Skipping in non-interactive mode.")
                    continue
                    
            try:
                count = client.pull(full_uri, str(target_dest))
                logger.log_info(f"Successfully pulled {count} files to {target_dest}")
            except Exception as exception:
                logger.log_error(f"Failed to pull {short_uid}: {exception}")
                continue
        logger.log_info(f"Workspace recognized as scenario '{scenario}'.")
        
    except Exception as exception:
        logger.log_error(f"Pull failed: {exception}")
        sys.exit(1)
