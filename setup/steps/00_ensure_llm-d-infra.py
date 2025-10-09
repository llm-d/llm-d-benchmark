import os
import re
import sys
from pathlib import Path
from dataclasses import dataclass
from typing import List, Tuple

current_file = Path(__file__).resolve()
workspace_root = current_file.parents[2]
setup_dir = current_file.parents[1]
config_explorer_src = workspace_root / "config_explorer" / "src"
sys.path.insert(0, str(config_explorer_src))
sys.path.insert(1, str(setup_dir))
sys.path.insert(2, str(workspace_root))
if "PYTHONPATH" in os.environ:
    os.environ["PYTHONPATH"] = f"{config_explorer_src}:{setup_dir}:{workspace_root}:{os.environ['PYTHONPATH']}"
else:
    os.environ["PYTHONPATH"] = f"{config_explorer_src}:{setup_dir}:{workspace_root}"

print(f"Workspace root directory added to PYTHONPATH: {os.environ['PYTHONPATH']}")

# ---------------- Import local packages ----------------
try:
    from functions import announce, environment_variable_to_dict
except ImportError as e:
    # Fallback for when dependencies are not available
    print(f"❌ ERROR: Could not import required modules: {e}")
    print("This script requires the llm-d environment to be properly set up.")
    print("Please run: ./setup/install_deps.sh")
    sys.exit(1)

def main():
    """Main function following the pattern from other Python steps"""

    # Set current step name for logging/tracking
    os.environ["LLMDBENCH_CURRENT_STEP"] = os.path.splitext(os.path.basename(__file__))[0]

    ev = {}
    environment_variable_to_dict(ev)

    if ev["control_dry_run"]:
        announce("DRY RUN enabled. No actual changes will be made.")

if __name__ == "__main__":
    sys.exit(main())
