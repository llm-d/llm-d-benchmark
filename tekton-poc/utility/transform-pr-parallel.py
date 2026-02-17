#!/usr/bin/env python3
import sys
import yaml
import itertools
import argparse
from copy import deepcopy

def load_yaml_from_path_or_stdin(path: str):
    """Load YAML from a file path or stdin if path == '-'."""
    if path == "-":
        try:
            return yaml.safe_load(sys.stdin)
        except Exception as e:
            raise ValueError(f"Failed to read YAML from stdin: {e}")
    else:
        try:
            with open(path, "r") as f:
                return yaml.safe_load(f)
        except FileNotFoundError:
            raise ValueError(f"Input file not found: {path}")
        except Exception as e:
            raise ValueError(f"Failed to read YAML from '{path}': {e}")

def dump_yaml_to_path_or_stdout(data, path: str | None, announce_to_stderr: str | None = None):
    """
    Write YAML to the given path. If path is None or '-', write to stdout with no extra noise.
    If path is a real file, write there and optionally announce to stderr.
    """
    if path is None or path == "-":
        yaml.safe_dump(data, sys.stdout, sort_keys=False)
    else:
        with open(path, "w") as f:
            yaml.safe_dump(data, f, sort_keys=False)
        if announce_to_stderr:
            print(announce_to_stderr, file=sys.stderr)

# -------------------- EXPANSION (existing behavior) -------------------- #
def transform_matrix_to_batched_dict(original_yaml: dict, max_parallel: int, sliding_window: bool):
    """
    Expand the matrix task into concrete tasks with runAfter enforcing either:
      - barrier batching (default), or
      - sliding-window (--sliding-window).
    """
    if max_parallel < 1:
        raise ValueError("max_parallel must be >= 1")

    try:
        pipeline_spec = original_yaml["spec"]["pipelineSpec"]
        tasks = pipeline_spec["tasks"]
    except Exception:
        raise ValueError("Input YAML must contain spec.pipelineSpec.tasks")

    if not isinstance(tasks, list) or len(tasks) == 0:
        raise ValueError("spec.pipelineSpec.tasks must be a non-empty list")

    base_task = deepcopy(tasks[0])
    base_name = base_task.get("name", "task")

    # matrix params
    matrix_params = {}
    for p in base_task.get("matrix", {}).get("params", []):
        vals = p.get("value", [])
        if not isinstance(vals, list):
            vals = [vals]
        matrix_params[p["name"]] = vals

    combos = list(itertools.product(*matrix_params.values())) if matrix_params else [tuple()]
    total = len(combos)

    new_tasks = []
    for i, combo in enumerate(combos):
        t = deepcopy(base_task)
        t.pop("matrix", None)
        t["name"] = f"{base_name}-{i}"

        t["params"] = deepcopy(base_task.get("params", [])) + [
            {"name": name, "value": value}
            for name, value in zip(matrix_params.keys(), combo)
        ]

        if sliding_window:
            if i >= max_parallel:
                t["runAfter"] = [f"{base_name}-{i - max_parallel}"]
            else:
                t.pop("runAfter", None)
        else:
            batch_index = i // max_parallel
            if batch_index > 0:
                prev_start = (batch_index - 1) * max_parallel
                prev_end = min(batch_index * max_parallel, total)
                t["runAfter"] = [f"{base_name}-{j}" for j in range(prev_start, prev_end)]
            else:
                t.pop("runAfter", None)

        new_tasks.append(t)

    new_pr = deepcopy(original_yaml)
    new_pipeline_spec = deepcopy(pipeline_spec)
    new_pipeline_spec["tasks"] = new_tasks
    new_pr["spec"]["pipelineSpec"] = new_pipeline_spec
    return new_pr

# -------------------- UNROLLING (new behavior) -------------------- #
def transform_unroll_params_dict(original_yaml: dict, unroll_params: list[str]):
    """
    Unroll (hoist) one or more matrix parameters into separate tasks.

    For given unroll_params (subset of the matrix param names):
      - Create one task for each Cartesian product of the chosen params' values.
      - In each task:
          * Set the chosen params as fixed task 'params' (not matrix).
          * Keep a 'matrix' of the remaining matrix params (if any).
      - Do not add runAfter constraints (preserve original no-dependency behavior).
    """
    if not unroll_params:
        raise ValueError("unroll_params must be a non-empty list of parameter names")

    try:
        pipeline_spec = original_yaml["spec"]["pipelineSpec"]
        tasks = pipeline_spec["tasks"]
    except Exception:
        raise ValueError("Input YAML must contain spec.pipelineSpec.tasks")

    if not isinstance(tasks, list) or len(tasks) == 0:
        raise ValueError("spec.pipelineSpec.tasks must be a non-empty list")

    base_task = deepcopy(tasks[0])
    base_name = base_task.get("name", "task")

    # Load matrix params preserving order as a list of (name, values)
    matrix_params_list = []
    for p in base_task.get("matrix", {}).get("params", []):
        vals = p.get("value", [])
        if not isinstance(vals, list):
            vals = [vals]
        matrix_params_list.append((p["name"], vals))

    if not matrix_params_list:
        raise ValueError("Base task has no matrix to unroll")

    # Validate unroll params are present in matrix
    matrix_names = [name for name, _ in matrix_params_list]
    unknown = [n for n in unroll_params if n not in matrix_names]
    if unknown:
        raise ValueError(f"Unroll params not found in matrix: {unknown}. Available: {matrix_names}")

    # Split into "chosen" vs "remaining"
    chosen = [(name, vals) for name, vals in matrix_params_list if name in unroll_params]
    remaining = [(name, vals) for name, vals in matrix_params_list if name not in unroll_params]

    # Cartesian product over chosen
    chosen_names = [name for name, _ in chosen]
    chosen_values_lists = [vals for _, vals in chosen]
    chosen_combos = list(itertools.product(*chosen_values_lists)) if chosen else [tuple()]

    new_tasks = []
    for i, combo in enumerate(chosen_combos):
        t = deepcopy(base_task)
        t["name"] = f"{base_name}-{i}"

        # Remove matrix entirely; we will rebuild it only with remaining params
        t.pop("matrix", None)

        # Merge original params plus fixed chosen params for this task
        t["params"] = deepcopy(base_task.get("params", [])) + [
            {"name": name, "value": value}
            for name, value in zip(chosen_names, combo)
        ]

        # Rebuild matrix from the remaining params (if any)
        if remaining:
            t["matrix"] = {
                "params": [{"name": name, "value": vals} for name, vals in remaining]
            }
        else:
            # Nothing remains; ensure no stray runAfter or matrix fields
            t.pop("matrix", None)

        # Preserve lack of dependencies (no runAfter) unless the base had them explicitly
        if "runAfter" in t:
            # Typically matrix tasks don't carry runAfter; remove to keep parallelism by default
            t.pop("runAfter", None)

        new_tasks.append(t)

    # Replace tasks with our new set
    new_pr = deepcopy(original_yaml)
    new_pipeline_spec = deepcopy(pipeline_spec)
    new_pipeline_spec["tasks"] = new_tasks
    new_pr["spec"]["pipelineSpec"] = new_pipeline_spec
    return new_pr

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Tekton PipelineRun matrix transformer.\n"
            "Default: expand the matrix to concrete tasks with barrier batching or sliding-window.\n"
            "Use --unroll to split specified matrix params into separate tasks while keeping a reduced matrix."
        )
    )
    parser.add_argument("input", help="Input PipelineRun YAML file or '-' for stdin")

    # Mutually exclusive: either unroll OR expand
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--unroll", metavar="PARAMS",
        help="Comma-separated matrix parameter names to hoist into tasks (e.g., 'gaiePluginConfig' or 'p1,p2')."
    )
    mode_group.add_argument(
        "--sliding-window", action="store_true",
        help="(Expand mode) Use sliding-window scheduling (each task i depends on i-n). Default is barrier batching."
    )

    # Expansion options (used only if NOT --unroll)
    parser.add_argument(
        "-n", "--max-parallel", type=int, default=1,
        help="(Expand mode) Maximum number of tasks to run in parallel. Default: 1"
    )

    parser.add_argument(
        "-o", "--output", default=None,
        help="Output file path. Use '-' or omit to write to stdout."
    )

    args = parser.parse_args()

    try:
        original = load_yaml_from_path_or_stdin(args.input)

        if args.unroll:
            unroll_params = [s.strip() for s in args.unroll.split(",") if s.strip()]
            transformed = transform_unroll_params_dict(original_yaml=original, unroll_params=unroll_params)
            mode_desc = f"unroll={unroll_params}"
        else:
            transformed = transform_matrix_to_batched_dict(
                original_yaml=original,
                max_parallel=args.max_parallel,
                sliding_window=args.sliding_window,
            )
            mode_desc = "sliding-window" if args.sliding_window else "barrier"

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    announce = None
    if args.output not in (None, "-"):
        if args.unroll:
            announce = f"✅ Transformed PipelineRun saved to '{args.output}' ({mode_desc})"
        else:
            announce = f"✅ Transformed PipelineRun saved to '{args.output}' (mode={mode_desc}, max_parallel={args.max_parallel})"

    dump_yaml_to_path_or_stdout(transformed, args.output, announce_to_stderr=announce)

if __name__ == "__main__":
    main()