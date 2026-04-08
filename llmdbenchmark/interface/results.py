"""CLI definition for the ``results`` subcommand."""

import argparse
import sys
from pathlib import Path

from llmdbenchmark.interface.commands import Command

def color_pad(text, width, color_code="31"):
    colored = str(text)
    if "missing" in colored:
        colored = colored.replace("missing", f"\033[{color_code}mmissing\033[0m")
    if "#" in colored:
        colored = colored.replace("#", f"\033[{color_code}m#\033[0m")
        
    if colored != str(text):
        return colored + " " * (width - len(str(text)))
    return f"{str(text):<{width}}"

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
    from llmdbenchmark.result_store.store import StoreManager, StoreNotFound
    from llmdbenchmark.result_store.config import ConfigManager
    from llmdbenchmark.result_store.workspace import WorkspaceManager
    from llmdbenchmark.result_store.gcs import GCSClient


    cmd = args.results_command
    
    if cmd == "init":
        try:
            store_dir, created = StoreManager.init_store()
            if created:
                logger.log_info(f"Initialized empty Result Store in {store_dir}")
            else:
                logger.log_info(f"Result Store already exists at {store_dir}")
        except Exception as e:
            logger.log_error(f"Failed to initialize store: {e}")
            sys.exit(1)
        return

    # All commands beneath rely on the store existing (except push and pull)
    if cmd not in ["push", "pull"]:
        try:
            from llmdbenchmark.result_store.store import StoreManager, StoreNotFound
            StoreManager.find_store_root()
        except StoreNotFound as e:
            logger.log_error(str(e))
            sys.exit(1)
            
    config = ConfigManager()

    if cmd == "remote":
        action = args.remote_action
        if action == "add":
            config.add_remote(args.name, args.uri)
            logger.log_info(f"Added remote '{args.name}' -> {args.uri}")
        elif action == "rm":
            try:
                config.remove_remote(args.name)
                logger.log_info(f"Removed remote '{args.name}'.")
            except ValueError as e:
                logger.log_error(str(e))
                sys.exit(1)
        elif action == "ls":
            remotes = config.list_remotes()
            for name, uri in remotes.items():
                logger.log_info(f"{name}\t{uri}")
                
    elif cmd == "add":
        wm = WorkspaceManager()
        import fnmatch
        
        for input_path in args.paths:
            target_path = input_path
            
            p = Path(input_path)
            if not p.exists() or not p.is_dir():
                logger.log_debug(f"'{input_path}' is not a directory. Searching for matching Run UID...")
                
                try:
                    store_root = StoreManager.find_store_root()
                except StoreNotFound as e:
                    logger.log_error(str(e))
                    continue
                    
                workspaces_dir = store_root / "workspaces"
                matches = []
                
                if workspaces_dir.exists():
                    for workspace in workspaces_dir.iterdir():
                        if not workspace.is_dir():
                            continue
                        results_dir = workspace / "results"
                        if not results_dir.exists() or not results_dir.is_dir():
                            continue
                            
                        for exp_dir in results_dir.iterdir():
                            if not exp_dir.is_dir():
                                continue
                                
                            info = wm._parse_report(exp_dir)
                            full_uid = info.get("run_uid", "-")
                            
                            if full_uid != "-":
                                if '*' in input_path or '?' in input_path:
                                    if fnmatch.fnmatch(full_uid, input_path):
                                        matches.append(exp_dir.resolve())
                                elif full_uid == input_path or full_uid.startswith(input_path):
                                    matches.append(exp_dir.resolve())
                
                if len(matches) == 0:
                    logger.log_error(f"Could not find matching Run UID or directory for '{input_path}'")
                    continue
                elif len(matches) > 1:
                    logger.log_error(f"Ambiguous UID '{input_path}'. Found multiple matches:")
                    for m in matches:
                        logger.log_error(f"  {m}")
                    continue
                else:
                    target_path = str(matches[0])
                    logger.log_debug(f"Resolved '{input_path}' to {target_path}")

            info = wm._parse_report(Path(target_path))
            overrides = {}
            
            missing_fields = []
            for field in ["scenario", "model", "hardware"]:
                val = info.get(field, "missing")
                if val == "missing" or "missing" in val or "#" in val:
                    missing_fields.append(field)
                    
            if missing_fields:
                if sys.stdout.isatty():
                    uid = info.get("run_uid", "missing")
                    short_uid = uid[:8] if len(uid) > 8 else uid
                    print(f"\033[33mMissing metadata detected for {short_uid}. Please fill in required fields:\033[0m")
                    field_error = False
                    for field in missing_fields:
                        while True:
                            val = input(f"  {field.capitalize()}: ").strip().replace("\\n", "").replace("\\r", "")
                            if not val:
                                print(f"\033[31mError: {field} is required to add results.\033[0m")
                                field_error = True
                                break
                            
                            if field == "hardware":
                                orig_val = info.get("hardware", "missing")
                                if "-x" in orig_val and "-x" not in val:
                                    count_part = orig_val.split("-x", 1)[1]
                                    val = f"{val}-x{count_part}"
                                
                                if "-x" not in val:
                                    print(f"\033[31mError: Hardware count could not be inferred. Please specify count (e.g., l4-x1).\033[0m")
                                    continue
                                    
                            overrides[field] = val
                            break
                        
                        if field_error:
                            break
                    if field_error:
                        continue
                else:
                    print(f"\033[31mError: Missing metadata for fields: {', '.join(missing_fields)} in {input_path}. Skipping.\033[0m")
                    continue

            if wm.add_workspace(target_path, overrides=overrides):
                # Update info with overrides for display
                info.update(overrides)
                uid = info.get("run_uid", "missing")
                short_uid = uid[:8] if len(uid) > 8 else uid
                print(f"Staged '{short_uid}'")
            else:
                print(f"Workspace '{target_path}' is already staged.")
            
    elif cmd == "rm":
        wm = WorkspaceManager()
        import fnmatch
        
        for input_path in args.paths:
            target_path = input_path
            
            p = Path(input_path)
            if not p.exists() or not p.is_dir():
                staged_runs = wm.list_staged()
                matches = []
                for s in staged_runs:
                    full_uid = s.get("run_uid", "-")
                    if full_uid != "-":
                        if '*' in input_path or '?' in input_path:
                            if fnmatch.fnmatch(full_uid, input_path):
                                matches.append(s["path"])
                        elif full_uid == input_path or full_uid.startswith(input_path):
                            matches.append(s["path"])
                        
                if len(matches) == 0:
                    logger.log_error(f"Workspace '{input_path}' was not staged.")
                    continue
                elif len(matches) > 1:
                    logger.log_error(f"Ambiguous UID '{input_path}'. Found multiple matches in staged runs:")
                    for m in matches:
                        logger.log_error(f"  {m}")
                    continue
                else:
                    target_path = matches[0]
                    logger.log_debug(f"Resolved UID '{input_path}' to staged path {target_path}")

            if wm.remove_workspace(target_path):
                info = wm._parse_report(Path(target_path))
                uid = info.get("run_uid", "-")
                short_uid = uid[:8] if len(uid) > 8 else uid
                print(f"Unstaged '{short_uid}'")
            else:
                logger.log_error(f"Workspace '{target_path}' was not staged.")
                continue
            
    elif cmd == "status":
        try:
            store_root = StoreManager.find_store_root()
        except StoreNotFound as e:
            logger.log_error(str(e))
            sys.exit(1)

        wm = WorkspaceManager()
        staged_runs = wm.list_staged()
        staged_paths = {r["path"] for r in staged_runs}
        


        workspaces_dir = store_root / "workspaces"
        staged_by_workspace = {}
        for s in staged_runs:
            path = Path(s['path'])
            ws_name = path.parent.parent.name if len(path.parts) >= 3 else "unknown"
            staged_by_workspace.setdefault(ws_name, []).append(s)

        untracked_by_workspace = {}
        if workspaces_dir.exists():
            for workspace in workspaces_dir.iterdir():
                if not workspace.is_dir():
                    continue
                results_dir = workspace / "results"
                if not results_dir.exists() or not results_dir.is_dir():
                    continue
                
                for exp_dir in results_dir.iterdir():
                    if not exp_dir.is_dir():
                        continue
                    
                    path_str = str(exp_dir.resolve())
                    if path_str not in staged_paths:
                        info = wm._parse_report(exp_dir)
                        info.update({"path": path_str, "status": "untracked"})
                        if info.get("run_uid") == "missing" and info.get("scenario") == "missing":
                            continue # Skip malformed/empty
                        
                        workspace_name = workspace.name
                        untracked_by_workspace.setdefault(workspace_name, []).append(info)

        if not staged_runs and not untracked_by_workspace:
            print(f"No benchmark runs found in {workspaces_dir}.")
            return

        if staged_by_workspace:
            print("Changes to be pushed (staged):")
            print(f"{'Run UID':<10} | {'Scenario':<25} | {'Model':<30} | {'Hardware':<15}")
            print("-" * 90)
            for ws_name in sorted(staged_by_workspace.keys()):
                print(f"[{ws_name}]")
                for s in staged_by_workspace[ws_name]:
                    short_uid = s['run_uid'][:8] if len(s['run_uid']) > 8 else s['run_uid']
                    print(f"  {color_pad(short_uid, 8)} | {color_pad(s['scenario'], 25)} | {color_pad(s['model'], 30)} | {color_pad(s['hardware'], 15)}")
            print("")

        if untracked_by_workspace:
            print("Untracked results:")
            print(f"{'Run UID':<10} | {'Scenario':<25} | {'Model':<30} | {'Hardware':<15}")
            print("-" * 90)
            for ws_name in sorted(untracked_by_workspace.keys()):
                print(f"[{ws_name}]")
                for s in untracked_by_workspace[ws_name]:
                    short_uid = s['run_uid'][:8] if len(s['run_uid']) > 8 else s['run_uid']
                    print(f"  {color_pad(short_uid, 8)} | {color_pad(s['scenario'], 25)} | {color_pad(s['model'], 30)} | {color_pad(s['hardware'], 15)}")
            
    elif cmd == "ls":
        try:
            uri = config.get_remote(args.remote)
            client = GCSClient()
            runs = client.ls(uri, model=args.model, hardware=args.hardware)
            if not runs:
                logger.log_info(f"No runs found in remote '{args.remote}'.")
                return

            logger.log_info(f"Runs in {args.remote} ({uri}):")
            print(f"{'Run UID':<8} | {'Scenario':<25} | {'Model':<30} | {'Hardware':<15}")
            print("-" * 85)
            
            by_group = {}
            for r in runs:
                g = r.get("group", "default")
                if g not in by_group:
                    by_group[g] = []
                by_group[g].append(r)
                
            for g in sorted(by_group.keys()):
                print(f"[{g}]")
                for r in by_group[g]:
                    short_uid = r['run_uid'][:8] if len(r['run_uid']) > 8 else r['run_uid']
                    print(f"  {color_pad(short_uid, 8)} | {color_pad(r['scenario'], 25)} | {color_pad(r['model'], 30)} | {color_pad(r['hardware'], 15)}")
        except ValueError as e:
            logger.log_error(str(e))
            sys.exit(1)
        except Exception as e:
            logger.log_error(f"Failed to list remote: {e}")
            sys.exit(1)
            

            
    elif cmd == "push":
        try:
            uri = config.get_remote(args.remote)
            client = GCSClient()
            runs_to_push = []
            
            from llmdbenchmark.result_store.workspace import WorkspaceManager
            from llmdbenchmark.result_store.store import StoreNotFound
            
            if args.path:
                path = Path(args.path)
                if not path.exists() or not path.is_dir():
                    logger.log_error(f"Directory '{args.path}' does not exist.")
                    sys.exit(1)
                    
                try:
                    wm = WorkspaceManager()
                except StoreNotFound:
                    wm = WorkspaceManager(staged_path=Path("/tmp/dummy.json"))
                    
                run = wm._parse_report(path)
                run["path"] = str(path)
                run["status"] = "staged"
                runs_to_push.append(run)
            else:
                try:
                    wm = WorkspaceManager()
                    staged_runs = wm.list_staged()
                    for r in staged_runs:
                        if r.get("status") == "staged" and r.get("path"):
                            runs_to_push.append(r)
                except StoreNotFound:
                    logger.log_error("Store not initialized and no path provided.")
                    sys.exit(1)
                    
            if not runs_to_push:
                print("No runs to push.")
                return
                
            pushed_count = 0
            for run in runs_to_push:
                path = run.get("path")
                if run.get("status") == "staged" and path:
                    short_uid = run.get("run_uid", "missing")[:8]
                    
                    if client.exists(uri, run, group=args.group):
                        if sys.stdout.isatty():
                            ans = input(f"Run {run.get('run_uid')} already exists in remote. Overwrite? [y/N]: ").strip().lower()
                            if ans != 'y':
                                print(f"Skipping {short_uid}.")
                                continue
                        else:
                            print(f"\033[31mRun {run.get('run_uid')} already exists in remote. Failing in non-interactive mode.\033[0m")
                            sys.exit(1)
                            
                    print(f"Pushing {short_uid}...")
                    try:
                        dest = client.push(uri, path, run, group=args.group)
                        print(f"Successfully pushed to {dest}")
                        if not args.path and 'wm' in locals():
                            wm.remove_workspace(path)
                        pushed_count += 1
                    except Exception as e:
                        print(f"\033[31mFailed to push {path}: {e}\033[0m")
                        
            print(f"Pushed {pushed_count} runs.")
            
        except Exception as e:
            logger.log_error(f"Push operations failed: {e}")
            sys.exit(1)
            
    elif cmd == "pull":
        try:
            remote_name = args.remote[0] if isinstance(args.remote, list) else args.remote
            uri = config.get_remote(remote_name)
            client = GCSClient()
            
            # 1. Find the runs
            logger.log_info(f"Resolving run '{args.run_uid}' in {remote_name}...")
            runs = client.ls(uri)
            import fnmatch
            
            matching_runs = []
            for r in runs:
                if '*' in args.run_uid or '?' in args.run_uid:
                    if fnmatch.fnmatch(r["run_uid"], args.run_uid):
                        matching_runs.append(r)
                elif r["run_uid"] == args.run_uid or r["run_uid"].startswith(args.run_uid):
                    matching_runs.append(r)
                    
            # Deduplicate by run_uid
            unique_runs = {}
            for r in matching_runs:
                unique_runs[r["run_uid"]] = r
            matching_runs = list(unique_runs.values())
                    
            if len(matching_runs) == 0:
                logger.log_error(f"Run UID '{args.run_uid}' not found in {remote_name}.")
                sys.exit(1)
                
            is_wildcard = '*' in args.run_uid or '?' in args.run_uid
            if len(matching_runs) > 1 and not is_wildcard:
                logger.log_error(f"Ambiguous UID '{args.run_uid}'. Found multiple matches:")
                for r in matching_runs:
                    logger.log_error(f"  {r['run_uid']} ({r['scenario']}/{r['model']})")
                sys.exit(1)
                
            logger.log_info(f"Found {len(matching_runs)} matching runs. Pulling...")
            
            for target_run in matching_runs:
                group = target_run.get("group", "default")
                scenario = target_run.get("scenario", "missing")
                full_uid = target_run["run_uid"]
                short_uid = full_uid[:8]
                
                # 2. Construct workspace path
                ws_name = f"{remote_name}_{group}_{short_uid}"
                try:
                    from llmdbenchmark.result_store.store import StoreManager, StoreNotFound
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
                    dest_path, count = client.pull(uri, full_uid, str(results_dir))
                    logger.log_info(f"Successfully pulled {count} files to {dest_path}")
                except Exception as e:
                    logger.log_error(f"Failed to pull {short_uid}: {e}")
                    continue
            logger.log_info(f"Workspace recognized as scenario '{scenario}'.")
            
        except Exception as e:
            logger.log_error(f"Pull failed: {e}")
            sys.exit(1)
