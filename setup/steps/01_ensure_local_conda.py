import os
import sys
import platform
import subprocess
import json
import shutil
from pathlib import Path

# Add project root to path for imports
current_file = Path(__file__).resolve()
project_root = current_file.parents[1]
sys.path.insert(0, str(project_root))

try:
    from functions import announce, environment_variable_to_dict
    import requests
except ImportError as e:
    # Fallback for when dependencies are not available
    print(f"Warning: Could not import required modules: {e}")
    print("This script requires the llm-d environment to be properly set up.")
    print("Please run: ./setup/install_deps.sh")
    print("And ensure requests is installed: pip install requests")
    sys.exit(1)


def get_platform_info():
    """Get platform information using native Python instead of shell commands"""
    system = platform.system().lower()
    return {
        'system': system,
        'machine': platform.machine(),
        'is_mac': system == 'darwin',
        'is_linux': system == 'linux'
    }


def is_conda_available():
    """Check if conda is available using native Python instead of shell command"""
    return shutil.which('conda') is not None


def get_conda_info():
    """Get conda information using JSON output instead of shell parsing"""
    try:
        #FIXME (USE llmdbench_execute_cmd)
        result = subprocess.run(['conda', 'info', '--json'],
                              capture_output=True, text=True, check=True)
        return json.loads(result.stdout)
    except (subprocess.CalledProcessError, json.JSONDecodeError, FileNotFoundError):
        return None


def check_conda_environment(env_name: str):
    """Check if conda environment exists using conda env list"""
    try:
        #FIXME (USE llmdbench_execute_cmd)
        result = subprocess.run(['conda', 'env', 'list'],
                              capture_output=True, text=True, check=True)
        return env_name in result.stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def install_miniforge_macos(dry_run: bool, verbose: bool):
    """Install Miniforge on macOS using Homebrew"""
    announce("🛠️ Installing Miniforge for macOS...")

    if dry_run:
        announce("---> would execute: brew install --cask miniforge")
        anaconda_path = 'export PATH="/opt/homebrew/bin/conda:$PATH"'
        conda_sh = Path("/opt/homebrew/Caskroom/miniforge/base/etc/profile.d/conda.sh")
        return 0, anaconda_path, conda_sh

    # Check if brew is available
    if not shutil.which('brew'):
        raise EnvironmentError("Homebrew not found. Please install Homebrew first.")

    # Install miniforge using brew
    cmd = ['brew', 'install', '--cask', 'miniforge']
    if verbose:
        announce(f"---> executing: {' '.join(cmd)}")

    #FIXME (USE llmdbench_execute_cmd)
    result = subprocess.run(cmd, capture_output=not verbose, text=True)

    if result.returncode != 0:
        raise RuntimeError(f"Failed to install miniforge: {result.stderr if not verbose else ''}")

    anaconda_path = 'export PATH="/opt/homebrew/bin/conda:$PATH"'
    conda_sh = Path("/opt/homebrew/Caskroom/miniforge/base/etc/profile.d/conda.sh")

    return result.returncode, anaconda_path, conda_sh


def install_miniforge_linux(dry_run: bool, verbose: bool):
    """Install Miniforge on Linux using the official installer"""
    announce("🛠️ Installing Miniforge for Linux...")

    platform_info = get_platform_info()

    # Construct download URL using native Python
    url = f"https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-{platform_info['system'].title()}-{platform_info['machine']}.sh"

    if dry_run:
        announce(f"---> would download and install from {url}")
        anaconda_path = 'export PATH="/opt/miniconda/bin/conda:$PATH"'
        conda_sh = Path("/opt/miniconda/etc/profile.d/conda.sh")
        return 0, anaconda_path, conda_sh

    if verbose:
        announce(f"---> downloading installer from {url}")

    try:
        # Download using requests instead of wget
        response = requests.get(url, stream=True, timeout=300)
        response.raise_for_status()

        if verbose:
            announce("---> running miniforge installer")

        # Run installer
        #FIXME (USE llmdbench_execute_cmd)
        process = subprocess.Popen(
            ['bash', '-s', '--', '-b', '-p', '/opt/miniconda'],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE if not verbose else None,
            stderr=subprocess.PIPE if not verbose else None
        )

        stdout, stderr = process.communicate(input=response.content)

        if process.returncode != 0:
            error_msg = stderr.decode() if stderr else "Unknown error during installation"
            raise RuntimeError(f"Failed to install miniforge: {error_msg}")

        anaconda_path = 'export PATH="/opt/miniconda/bin/conda:$PATH"'
        conda_sh = Path("/opt/miniconda/etc/profile.d/conda.sh")

        return process.returncode, anaconda_path, conda_sh

    except requests.RequestException as e:
        raise RuntimeError(f"Failed to download miniforge installer: {e}")


def update_shell_rc_file(anaconda_path: str, shell_name: str, dry_run: bool):
    """Update shell RC file with conda path using native Python file operations"""
    rc_file = Path.home() / f".{shell_name}rc"

    if dry_run:
        announce(f"---> would check and update {rc_file}")
        return True

    # Check if path already exists in RC file
    try:
        if rc_file.exists():
            content = rc_file.read_text()
            if anaconda_path in content:
                announce(f"⏭️  Anaconda path already present in {rc_file}")
                return True

        # Add anaconda path to RC file
        with open(rc_file, 'a') as f:
            f.write(f"\n{anaconda_path}\n")

        announce(f"✅ Anaconda path added to {rc_file}")
        return True

    except IOError as e:
        announce(f"❌ Failed to update {rc_file}: {e}")
        return False


def source_conda_script(conda_sh: Path, dry_run: bool, verbose: bool):
    """Source conda.sh script"""
    if dry_run:
        announce(f"---> would source {conda_sh}")
        return 0

    if not conda_sh.exists():
        raise FileNotFoundError(f"Could not find conda.sh at {conda_sh}")


    announce(f"⏭️ running {conda_sh}")

    # Note: sourcing in subprocess doesn't affect parent shell
    # This is mainly for validation that the file exists and is executable
    cmd = f'source "{conda_sh}"'
    if verbose:
        announce(f"---> executing: {cmd}")

    #FIXME (USE llmdbench_execute_cmd)
    result = subprocess.run(['bash', '-c', cmd],
                          capture_output=not verbose, text=True)

    if result.returncode != 0:
        raise RuntimeError(f"Failed to source conda.sh: {result.stderr if not verbose else ''}")

    return result.returncode


def create_conda_environment(env_name: str, dry_run: bool, verbose: bool):
    """Create and configure conda environment"""
    if check_conda_environment(env_name):
        announce(f"⏭️  Conda environment \"{env_name}\" already created, skipping installation")
        return 0

    announce(f"📜 Configuring conda environment \"{env_name}\"...")

    if dry_run:
        announce(f"---> would create conda environment: {env_name}")
        announce(f"---> would activate conda environment: {env_name}")
        announce(f"---> would install requirements")
        return 0

    try:
        # Create environment
        cmd = ['conda', 'create', '--name', env_name, '-y']
        if verbose:
            announce(f"---> executing: {' '.join(cmd)}")

        #FIXME (USE llmdbench_execute_cmd)
        result = subprocess.run(cmd, capture_output=not verbose, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"Failed to create conda environment: {result.stderr if not verbose else ''}")

        # Activate environment
        cmd = ['conda', 'activate', env_name]
        if verbose:
            announce(f"---> executing: {' '.join(cmd)}")

        #FIXME (USE llmdbench_execute_cmd)
        result = subprocess.run(cmd, capture_output=not verbose, text=True)
        if result.returncode != 0:
            announce(f"Warning: conda activate returned {result.returncode} (this is often normal)")

        # Install requirements if available
        requirements_file = Path(os.getenv('LLMDBENCH_MAIN_DIR', '.')) / 'build' / 'requirements.txt'
        if requirements_file.exists():
            python_cmd = os.getenv('LLMDBENCH_CONTROL_PCMD', 'python')

            # Show environment info
            announce(f"ℹ️  Python: {shutil.which(python_cmd) or 'not found'}")

            # Install requirements
            cmd = [python_cmd, '-m', 'pip', 'install', '-r', str(requirements_file)]
            if verbose:
                announce(f"---> executing: {' '.join(cmd)}")

            #FIXME (USE llmdbench_execute_cmd)
            result = subprocess.run(cmd, capture_output=not verbose, text=True)
            if result.returncode != 0:
                announce(f"Warning: pip install returned {result.returncode}")

        return 0

    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to configure conda environment: {e}")


def ensure_local_conda(
    run_locally: bool,
    host_os: str,
    host_shell: str,
    env_name: str,
    dry_run: bool,
    verbose: bool
) -> int:
    """
    Ensure local conda environment is set up using native Python libraries where possible.

    Args:
        run_locally: Whether to run experiment analysis locally
        host_os: Host operating system (mac/linux)
        host_shell: Shell type (bash/zsh/etc)
        env_name: Conda environment name
        dry_run: If True, only print what would be executed
        verbose: If True, print detailed output

    Returns:
        0 for success, non-zero for failure
    """

    # Early exit check
    if not run_locally:
        announce("⏭️  Environment variable \"LLMDBENCH_RUN_EXPERIMENT_ANALYZE_LOCALLY\" is set to 0, skipping local setup of conda environment")
        return 0

    try:
        platform_info = get_platform_info()

        # Check if conda is already available
        if not is_conda_available():
            # Install conda based on platform
            if platform_info['is_mac']:
                exit_code, anaconda_path, conda_sh = install_miniforge_macos(dry_run, verbose)
            elif platform_info['is_linux']:
                exit_code, anaconda_path, conda_sh = install_miniforge_linux(dry_run, verbose)
            else:
                raise RuntimeError(f"Unsupported platform: {platform_info['system']}")

            if exit_code != 0:
                return exit_code

            # Update shell RC file
            if not update_shell_rc_file(anaconda_path, host_shell, dry_run):
                return 1
        else:
            # Conda already available, find conda.sh
            conda_info = get_conda_info()
            if not conda_info:
                raise RuntimeError("Could not get conda information")

            root_prefix = Path(conda_info.get('root_prefix', ''))
            if platform_info['is_mac']:
                conda_sh = root_prefix / 'base' / 'etc' / 'profile.d' / 'conda.sh'
            else:
                conda_sh = root_prefix / 'etc' / 'profile.d' / 'conda.sh'

        # Source conda.sh
        source_conda_script(conda_sh, dry_run, verbose)

        # Create and configure conda environment
        create_conda_environment(env_name, dry_run, verbose)

        announce(f"✅ Conda environment \"{env_name}\" configured")
        return 0

    except Exception as e:
        announce(f"❌ Error setting up conda environment: {e}")
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
    return ensure_local_conda(
        run_locally=ev["run_experiment_analyze_locally"],
        host_os=ev["control_deploy_host_os"],
        host_shell=ev["control_deploy_host_shell"] ,
        env_name=ev["harness_conda_env_name"],
        dry_run=ev["control_dry_run"],
        verbose=ev["control_verbose"]
    )


if __name__ == "__main__":
    sys.exit(main())
