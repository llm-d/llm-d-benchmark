import os
import sys
from pathlib import Path

# Add project root to path for imports
current_file = Path(__file__).resolve()
project_root = current_file.parents[1]
sys.path.insert(0, str(project_root))

try:
    from functions import announce, llmdbench_execute_cmd
except ImportError as e:
    # Fallback for when dependencies are not available
    print(f"Warning: Could not import functions module: {e}")
    print("This script requires the llm-d environment to be properly set up.")
    print("Please run: ./setup/install_deps.sh")
    print("Or use the unit tests for development: python3 ./util/unit_test/test_00_ensure_llm-d-infra.py")
    import sys
    sys.exit(1)


def run_git_command(command: str, cwd: str, dry_run: bool, verbose: bool) -> int:
    """
    Execute a git command in the specified directory.
    
    Args:
        command: Git command to execute
        cwd: Working directory for the command
        dry_run: If True, only print what would be executed
        verbose: If True, print command output
    
    Returns:
        Return code of the command (0 for success)
    """
    full_command = f"cd {cwd}; {command}"
    return llmdbench_execute_cmd(
        actual_cmd=full_command, 
        dry_run=dry_run, 
        verbose=verbose
    )


def ensure_llm_d_infra(infra_dir: str, git_repo: str, git_branch: str, dry_run: bool, verbose: bool) -> int:
    """
    Ensure llm-d-infra repository is present and up-to-date.
    
    Args:
        infra_dir: Directory where llm-d-infra should be located
        git_repo: Git repository URL
        git_branch: Git branch to use
        dry_run: If True, only print what would be executed
        verbose: If True, print detailed output
    
    Returns:
        0 for success, non-zero for failure
    """
    announce("💾 Cloning and setting up llm-d-infra...")
    
    # Ensure infra_dir exists
    infra_path = Path(infra_dir)
    if not dry_run:
        infra_path.mkdir(parents=True, exist_ok=True)
    
    llm_d_infra_path = infra_path / "llm-d-infra"
    
    try:
        if not llm_d_infra_path.exists():
            # Clone the repository
            clone_command = f'git clone "{git_repo}" -b "{git_branch}"'
            result = run_git_command(clone_command, infra_dir, dry_run, verbose)
            if result != 0:
                return result
        else:
            # Update existing repository
            update_command = f"git checkout {git_branch}; git pull"
            result = run_git_command(update_command, str(llm_d_infra_path), dry_run, verbose)
            if result != 0:
                return result
        
        announce(f'✅ llm-d-infra is present at "{infra_dir}"')
        return 0
        
    except Exception as e:
        announce(f"❌ Error managing llm-d-infra repository: {e}")
        return 1


def main():
    """Main function following the pattern from 04_ensure_model_namespace_prepared.py"""
    
    # Set current step name for logging/tracking
    os.environ["CURRENT_STEP_NAME"] = os.path.splitext(os.path.basename(__file__))[0]
    
    # Parse environment variables into ev dictionary (following established pattern)
    ev = {}
    for key, value in os.environ.items():
        if "LLMDBENCH_" in key:
            ev[key.split("LLMDBENCH_")[1].lower()] = value
    
    # Source env.sh for any additional setup (following established pattern)
    llmdbench_execute_cmd(
        actual_cmd=f'source "{ev["control_dir"]}/env.sh"', 
        dry_run=ev.get("control_dry_run") == '1', 
        verbose=ev.get("control_verbose") == '1'
    )
    
    # Extract required environment variables with defaults
    infra_dir = ev.get("infra_dir", "/tmp")
    git_repo = ev.get("infra_git_repo", "https://github.com/llm-d-incubation/llm-d-infra.git")
    git_branch = ev.get("infra_git_branch", "main")
    dry_run = ev.get("control_dry_run") == '1'
    verbose = ev.get("control_verbose") == '1'
    
    if dry_run:
        announce("DRY RUN enabled. No actual changes will be made.")
    
    # Execute the main logic
    return ensure_llm_d_infra(
        infra_dir=infra_dir,
        git_repo=git_repo,
        git_branch=git_branch,
        dry_run=dry_run,
        verbose=verbose
    )


if __name__ == "__main__":
    sys.exit(main())