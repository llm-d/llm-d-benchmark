import sys
import os
import subprocess
import argparse

# assumes the component scripts are in setup subdirectory
# this script should be placed in setup dir

SETUP_DIR = os.path.join(os.getcwd(), "setup")


def check_script_exists(script_name) -> str:
    script_path = os.path.join(SETUP_DIR, script_name)
    if not os.path.exists(script_path):
        print(f"Script: {script_name} not found in: {script_path}")
        sys.exit(1)

    # return absolute path to call script 
    return script_name

def run_command(script_path, args) -> None:
    ''' executes a shell script with the given arguments '''
    script_path = script_path = os.path.join(SETUP_DIR, script_path)
    command = [script_path] + args
    print(f"Running command: {' '.join(command)}")
    try:
        # check=True so if subproccess returns non 0 then we can raise CalledProcessError
        subprocess.run(command, check=True)
    except subprocess.CalledProcessError as e:
        print(f"Error: Command '{' '.join(command)}' failed with exit code {e.returncode}", file=sys.stderr)
        sys.exit(e.returncode)

def handle_run(args) -> None:
    '''
    manages a single end-to-end test run
    standup.sh -> run.sh -> teardown.sh
    '''
    print("Mode: Single Run")
    standup_script = check_script_exists("standup.sh")
    run_script = check_script_exists("run.sh")
    teardown_script = check_script_exists("teardown.sh")

    try:
        print("\n[STANDUP] (1/3) Standing up stack")
        run_command(standup_script, args)
        print("[STANDUP] Stack standup complete")

        print("\n[RUN] (2/3) Running workload")
        run_command(run_script, args)
        print("[RUN] Workload execution finished")

    finally:
        print("\n[CLEANUP] (3/3) Executing Teardown")
        # we dont want the teardown failure to prevent the script from exiting with the original error code if one hapened
        try:
            # pass no args to teardown for now
            run_command(teardown_script, [])
        except Exception as e:
            print(f"Warning: Teardown command failed: {e}", file=sys.stderr)
        print("[CLEANUP] Teardown complete")


def handle_sweep(args) -> None:
    print("Mode: Sweep")
    sweep_script = check_script_exists("sweep.sh")
    print(f"Running {sweep_script} with arguments: {' '.join(args)}")
    run_command(sweep_script, args)
    print("sweep.sh execution complete")


def handle_batch(args) -> None:
    print("Mode: Batch")
    run_script = check_script_exists("run.sh")

    # args contains <file> and args, it should require a input file for now
    if len(args) == 0:
        print(f"Error: Missing <file>\nBatch usage: python3 ./setup/e2e.py batch <file>")
    else:
        batch_file = args[0] 
        with open(batch_file, 'r') as f:
            lines = f.readlines()
        for line in lines:
            cur_run_args = line.split(' ')
            run_command(run_script, cur_run_args)

def get_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Runs an end to end workload, wrapping standup.sh, run.sh, sweep.sh, and teardown.sh",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "command",
        choices=["run", "sweep", "batch"],
        help=(
            "The command to execute:\n"
            "   run [args...]              - Runs a single end to end test ( standup -> run -> teardown )\n"
            "   batch <file>               - Runs multiple run.sh calls from a file against a single deployment\n"
            "   sweep [args...]            - Delegates to sweep.sh to run different workloads on a single profile\n"
        )
    )
    parser.add_argument(
        "script_args",
        nargs=argparse.REMAINDER,
        help="All subsequent arguments are passed directly to the underlying script(s)"
    )

    return parser



def main() -> None:
    # we only use the parser for initial validation and the help message
    parser = get_parser()
    
    if len(sys.argv) < 2 or sys.argv[1] not in ["run", "sweep", "batch"]:
        parser.print_help()
        sys.exit(1)

    command = sys.argv[1]
    # all other arguments are passed to run or sweep
    passthrough_args = sys.argv[2:]

    if command == "run":
        handle_run(passthrough_args)
    elif command == "batch":
        handle_batch(passthrough_args)
    elif command == "sweep":
        handle_sweep(passthrough_args)


if __name__ == "__main__":
    main()

