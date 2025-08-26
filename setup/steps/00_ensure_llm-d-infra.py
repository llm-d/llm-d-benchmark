import os
import sys
from pathlib import Path

# Add project root to path for imports
current_file = Path(__file__).resolve()
project_root = current_file.parents[1]
sys.path.insert(0, str(project_root))

try:
    from functions import announce, environment_variable_to_dict
    import git
except ImportError as e:
    # Fallback for when dependencies are not available
    print(f"Warning: Could not import required modules: {e}")
    print("This script requires the llm-d environment to be properly set up.")
    print("Please run: ./setup/install_deps.sh")
    print("And ensure GitPython is installed: pip install GitPython")
    sys.exit(1)


def ensure_llm_d_infra(infra_dir: str, git_repo: str, git_branch: str, dry_run: bool, verbose: bool) -> int:
    """
    Ensure llm-d-infra repository is present and up-to-date using GitPython.

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
            if dry_run:
                announce(f"---> would clone repository {git_repo} branch {git_branch} to {llm_d_infra_path}")
            else:
                if verbose:
                    announce(f"---> cloning repository {git_repo} branch {git_branch} to {llm_d_infra_path}")

                repo = git.Repo.clone_from(
                    url=git_repo,
                    to_path=str(llm_d_infra_path),
                    branch=git_branch
                )

                if verbose:
                    announce(f"---> successfully cloned to {repo.working_dir}")
        else:
            # Update existing repository
            if dry_run:
                announce(f"---> would checkout branch {git_branch} and pull latest changes in {llm_d_infra_path}")
            else:
                if verbose:
                    announce(f"---> updating existing repository in {llm_d_infra_path}")

                repo = git.Repo(str(llm_d_infra_path))

                # Checkout the target branch
                repo.git.checkout(git_branch)

                # Pull latest changes
                origin = repo.remotes.origin
                origin.pull()

                if verbose:
                    announce(f"---> successfully updated to latest {git_branch}")

        announce(f'✅ llm-d-infra is present at "{infra_dir}"')
        return 0

    except git.exc.GitError as e:
        announce(f"❌ Git operation failed: {e}")
        return 1
    except Exception as e:
        announce(f"❌ Error managing llm-d-infra repository: {e}")
        return 1


def main():
    """Main function following the pattern from other Python steps"""

    # Set current step name for logging/tracking
    os.environ["LLMDBENCH_CURRENT_STEP"] = os.path.splitext(os.path.basename(__file__))[0]

    ev = {}
    environment_variable_to_dict(ev)

    if ev["control_dry_run"]:
        announce("DRY RUN enabled. No actual changes will be made.")

    # Execute the main logic
    return ensure_llm_d_infra(
        infra_dir=ev["infra_dir"],
        git_repo=ev["infra_git_repo"],
        git_branch=ev["infra_git_branch"],
        dry_run=ev["control_dry_run"],
        verbose=ev["control_verbose"]
    )


if __name__ == "__main__":
    sys.exit(main())
