import os
import re
import sys
from pathlib import Path
from dataclasses import dataclass
from typing import List, Tuple

# Add project root to path for imports
current_file = Path(__file__).resolve()
project_root = current_file.parents[1]
sys.path.insert(0, str(project_root))

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

    ev = {'current_step_name': os.path.splitext(os.path.basename(__file__))[0] }
    environment_variable_to_dict(ev)

    if ev["control_dry_run"]:
        announce("DRY RUN enabled. No actual changes will be made.")

if __name__ == "__main__":
    sys.exit(main())
