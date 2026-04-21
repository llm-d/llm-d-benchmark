"""Command to push staged runs to a remote."""

import sys
from pathlib import Path
from llmdbenchmark.result_store.config import ConfigManager
from llmdbenchmark.result_store.workspace import WorkspaceManager
from llmdbenchmark.result_store.store import StoreNotFound
from llmdbenchmark.result_store.commands import register_command
from llmdbenchmark.result_store.client import get_storage_client, get_fallback_client

@register_command("push")
def execute(args, logger):
    config = ConfigManager()
    try:
        uri = config.get_remote(args.remote)
        client = get_storage_client(uri)
        runs_to_push = []
        
        if args.path:
            path = Path(args.path)
            if not path.exists() or not path.is_dir():
                logger.log_error(f"Directory '{args.path}' does not exist.")
                sys.exit(1)
                
            try:
                workspace_manager = WorkspaceManager()
            except StoreNotFound:
                workspace_manager = WorkspaceManager(staged_path=Path("/tmp/dummy.json"))
                
            run = workspace_manager._parse_report(path)
            run["path"] = str(path)
            run["status"] = "staged"
            runs_to_push.append(run)
        else:
            try:
                workspace_manager = WorkspaceManager()
                staged_runs = workspace_manager.list_staged()
                for r in staged_runs:
                    if r.get("status") == "staged" and r.get("path"):
                        runs_to_push.append(r)
            except StoreNotFound:
                logger.log_error("Store not initialized and no path provided.")
                sys.exit(1)
                
        if not runs_to_push:
            logger.log_plain("No runs to push.")
            return
            
        pushed_count = 0
        for run in runs_to_push:
            path = run.get("path")
            if run.get("status") == "staged" and path:
                short_uid = run.get("run_uid", "missing")[:8]
                
                scenario = run.get("scenario", "missing")
                model = run.get("model", "missing")
                hardware = run.get("hardware", "missing")
                run_uid = run.get("run_uid", "missing")
                
                full_uri = f"{uri.rstrip('/')}/{args.group}/{scenario}/{model}/{hardware}/{run_uid}"
                
                try:
                    remote_exists = client.exists(full_uri)
                except Exception as exception:
                    fallback_client = get_fallback_client(client)
                    if fallback_client:
                        logger.log_warning(f"Direct access failed, trying fallback: {exception}")
                        client = fallback_client
                        remote_exists = client.exists(full_uri)
                    else:
                        raise exception
                    
                if remote_exists:
                    if sys.stdout.isatty():
                        ans = input(f"Run {run_uid} already exists in remote. Overwrite? [y/N]: ").strip().lower()
                        if ans != 'y':
                            logger.log_plain(f"Skipping {short_uid}.")
                            continue
                    else:
                        logger.log_plain(f"\033[31mRun {run_uid} already exists in remote. Failing in non-interactive mode.\033[0m")
                        sys.exit(1)
                        
                logger.log_plain(f"Pushing {short_uid}...")
                try:
                    uploaded_files = client.push(full_uri, path)
                    logger.log_plain(f"Successfully pushed {uploaded_files} files to {full_uri}")
                    if not args.path and 'workspace_manager' in locals():
                        workspace_manager.remove_workspace(path)
                    pushed_count += 1
                except Exception as exception:
                    logger.log_plain(f"\033[31mFailed to push {path}: {exception}\033[0m")
                    
        logger.log_plain(f"Pushed {pushed_count} runs.")
        
    except Exception as exception:
        logger.log_error(f"Push operations failed: {exception}")
        sys.exit(1)
