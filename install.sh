#!/usr/bin/env bash
#
# install.sh -- Install all dependencies for llm-d-benchmark
#
# Can be run two ways:
#
#   1. From inside the repo:
#      ./install.sh
#
#   2. Via curl (auto-clones the repo into ./llm-d-benchmark):
#      curl -sSL https://raw.githubusercontent.com/llm-d/llm-d-benchmark/main/install.sh | bash
#
#      To clone a specific branch:
#      LLMDBENCH_BRANCH=my-branch curl -sSL ... | bash
#
# Installs the llmdbenchmark CLI, config_explorer, and validates
# that required system tools are available.
#
# Usage:
#   ./install.sh                   # interactive -- prompts if no venv
#   ./install.sh -y                # non-interactive -- allows system python
#   ./install.sh noreset           # skip cache reset (re-use previous checks)
#   source install.sh              # also works when sourced
#
set -euo pipefail

REPO_URL="https://github.com/llm-d/llm-d-benchmark.git"
REPO_DIR="llm-d-benchmark"
DEFAULT_BRANCH="main"

# ---------------------------------------------------------------------------
# Bootstrap: if run via curl (no repo present), clone first
#   curl -sSL https://raw.githubusercontent.com/llm-d/llm-d-benchmark/main/install.sh | bash
# ---------------------------------------------------------------------------
_bootstrap_if_needed() {
    # Detect curl-pipe-bash: BASH_SOURCE is empty or points to stdin
    local need_clone=false

    if [[ -z "${BASH_SOURCE[0]:-}" || "${BASH_SOURCE[0]}" == "bash" || "${BASH_SOURCE[0]}" == "/dev/stdin" ]]; then
        need_clone=true
    elif [[ ! -f "pyproject.toml" && ! -d "llmdbenchmark" ]]; then
        # Script exists on disk but not inside the repo
        need_clone=true
    fi

    if [[ "$need_clone" == "true" ]]; then
        echo ""
        echo "  llm-d-benchmark repository not detected in current directory."
        echo ""

        # Check if it already exists in cwd
        if [[ -d "${REPO_DIR}" && -f "${REPO_DIR}/pyproject.toml" ]]; then
            echo "  Found existing clone at ./${REPO_DIR}"
            cd "${REPO_DIR}"
        else
            # Check for git
            if ! command -v git &>/dev/null; then
                echo "  ERROR: git is required but not installed."
                exit 1
            fi

            local branch="${LLMDBENCH_BRANCH:-${DEFAULT_BRANCH}}"
            echo "  Cloning ${REPO_URL} (branch: ${branch})..."
            git clone --branch "${branch}" "${REPO_URL}" "${REPO_DIR}"
            cd "${REPO_DIR}"
            echo "  Cloned to $(pwd)"
        fi

        echo ""
        # Re-exec the install script from within the repo
        exec bash install.sh "$@"
    fi
}

_bootstrap_if_needed "$@"

# ---------------------------------------------------------------------------
# Resolve script directory (works whether sourced or executed)
# ---------------------------------------------------------------------------
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
else
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi

# ---------------------------------------------------------------------------
# Help screen
# ---------------------------------------------------------------------------
show_help() {
    cat <<'HELP'
install.sh — Install all dependencies for llm-d-benchmark

USAGE
    ./install.sh [OPTIONS]
    source install.sh [OPTIONS]

DESCRIPTION
    Sets up the complete development / runtime environment for llm-d-benchmark.

    1. Validates Python 3.11+ and pip
    2. Checks for required system tools  (curl, git, kubectl, helm)
    3. Checks for optional system tools   (oc)
    4. Installs llmdbenchmark             (editable: pip install -e .)
    5. Installs config_explorer           (editable: pip install -e config_explorer/)
    6. Verifies that all Python packages are importable

    If no virtual environment is active, the script will automatically
    create one at .venv/ and activate it for the install. After the
    script finishes, run "source .venv/bin/activate" in your shell.

    Pass -y to skip venv creation and install with system Python instead.

OPTIONS
    -h, --help      Show this help message and exit.
    -y              Non-interactive mode — use system Python directly
                    instead of creating a virtual environment.
    noreset         Reuse the dependency cache (~/.llmdbench_dependencies_checked)
                    from a previous run instead of re-checking everything.

CACHE
    The script records which tools and packages have already been verified
    in ~/.llmdbench_dependencies_checked.  By default each run resets the
    cache; pass "noreset" to keep it.

HELP
}

# ---------------------------------------------------------------------------
# OS detection
# ---------------------------------------------------------------------------
is_mac=$(uname -s | grep -i darwin || true)
if [[ -n "$is_mac" ]]; then
    target_os=mac
else
    target_os=linux
    # shellcheck disable=SC1091
    [[ -f /etc/os-release ]] && source /etc/os-release
fi

# ---------------------------------------------------------------------------
# CLI flags
# ---------------------------------------------------------------------------
allow_system_python=false
reset_cache=true

for arg in "$@"; do
    case $arg in
        -h|--help)    show_help; exit 0 ;;
        -y)           allow_system_python=true ;;
        noreset)      reset_cache=false ;;
    esac
done

# ---------------------------------------------------------------------------
# Cache file — skip already-checked items across invocations
# ---------------------------------------------------------------------------
dependencies_checked_file=~/.llmdbench_dependencies_checked

if [[ "$reset_cache" == "true" ]]; then
    rm -f "$dependencies_checked_file"
fi
touch "$dependencies_checked_file"

# ---------------------------------------------------------------------------
# Package manager detection
# ---------------------------------------------------------------------------
if [[ "$target_os" == "mac" ]]; then
    PKG_MGR="brew install"
elif command -v apt &>/dev/null; then
    PKG_MGR="sudo apt install -y"
elif command -v apt-get &>/dev/null; then
    PKG_MGR="sudo apt-get install -y"
elif command -v brew &>/dev/null; then
    PKG_MGR="brew install"
elif command -v yum &>/dev/null; then
    PKG_MGR="sudo yum install -y"
elif command -v dnf &>/dev/null; then
    PKG_MGR="sudo dnf install -y"
else
    echo "WARNING: No supported package manager found (apt, brew, yum, dnf)"
    echo "         System tool installation may fail."
    PKG_MGR="echo SKIP:"
fi

# ---------------------------------------------------------------------------
# Python / pip detection — auto-creates a .venv if none is active
# ---------------------------------------------------------------------------
VENV_DIR="${SCRIPT_DIR}/.venv"
CREATED_VENV=false

_detected_venv="${VIRTUAL_ENV:-${CONDA_PREFIX:-}}"
if [[ -n "$_detected_venv" && -d "$_detected_venv" ]]; then
    # Prefer "python", fall back to "python3" (macOS venvs may lack "python")
    if command -v python &>/dev/null; then
        PYTHON_CMD="python"
        PIP_CMD="python -m pip"
    else
        PYTHON_CMD="python3"
        PIP_CMD="python3 -m pip"
    fi
    echo "Virtual environment detected: ${_detected_venv}"
elif [[ "$allow_system_python" == "true" ]]; then
    PYTHON_CMD="python3"
    PIP_CMD="python3 -m pip"
    echo "Using system python3 (forced with -y flag)"
else
    # No venv active — reuse existing .venv or create a new one
    if [[ -d "$VENV_DIR" ]]; then
        if grep -q "venv created." "$dependencies_checked_file" 2>/dev/null; then
            true  # cached — skip the log line
        else
            echo "Using existing virtual environment: ${VENV_DIR}"
            echo "venv created." >> "$dependencies_checked_file"
        fi
    else
        echo "No virtual environment detected — creating ${VENV_DIR} ..."
        python3 -m venv "$VENV_DIR"
        CREATED_VENV=true
        echo "Virtual environment created: ${VENV_DIR}"
        echo "venv created." >> "$dependencies_checked_file"
    fi
    # shellcheck disable=SC1091
    source "${VENV_DIR}/bin/activate"
    if command -v python &>/dev/null; then
        PYTHON_CMD="python"
        PIP_CMD="python -m pip"
    else
        PYTHON_CMD="python3"
        PIP_CMD="python3 -m pip"
    fi
fi

# ---------------------------------------------------------------------------
# Validate Python 3.11+
# ---------------------------------------------------------------------------
if ! command -v ${PYTHON_CMD} &>/dev/null; then
    # Last resort: try the other name
    if [[ "$PYTHON_CMD" == "python" ]] && command -v python3 &>/dev/null; then
        PYTHON_CMD="python3"
        PIP_CMD="python3 -m pip"
    elif [[ "$PYTHON_CMD" == "python3" ]] && command -v python &>/dev/null; then
        PYTHON_CMD="python"
        PIP_CMD="python -m pip"
    else
        echo "ERROR: Neither python nor python3 found in PATH"
        exit 1
    fi
fi

python_version=$(${PYTHON_CMD} -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
python_major=$(echo "${python_version}" | cut -d. -f1)
python_minor=$(echo "${python_version}" | cut -d. -f2)

if ! (( python_major > 3 || (python_major == 3 && python_minor >= 11) )); then
    echo "ERROR: Python 3.11+ required, but ${PYTHON_CMD} is version ${python_version}"
    exit 1
fi
echo "Python ${python_version} — OK"

# Ensure pip is available
if ! ${PIP_CMD} --version &>/dev/null; then
    echo "pip not found. Attempting to install..."
    if [[ "$target_os" == "linux" ]]; then
        ${PKG_MGR} python3-pip
    else
        echo "ERROR: pip not found. Please install it manually."
        exit 1
    fi
fi

# ===================================================================
# System tool checks — shows version inline, no separate summary
# ===================================================================
echo ""
echo "=== System tools ==="

# Tools required for cluster operations
tools="curl git helm helmfile skopeo kustomize jq yq crane"

# One of kubectl or oc is required
kube_tool=""
if command -v kubectl &>/dev/null; then
    kube_tool="kubectl"
elif command -v oc &>/dev/null; then
    kube_tool="oc"
fi
if [ -z "$kube_tool" ]; then
    echo "  kubectl/oc -- NOT FOUND, attempting kubectl install..."
    tools="$tools kubectl"
else
    printf "  %-14s %-20s %s\n" "$kube_tool" "$($kube_tool version --client --short 2>/dev/null || $kube_tool version --client 2>/dev/null | head -1)" ""
fi

# Optional tools -- checked but not fatal if missing
optional_tools="oc"

# ---------------------------------------------------------------------------
# Version helper — returns version string for a given tool
# ---------------------------------------------------------------------------
tool_version() {
    local tool="$1"
    case "$tool" in
        curl)       curl --version 2>&1 | head -1 | awk '{print $2}' ;;
        git)        git --version 2>&1 | awk '{print $3}' ;;
        kubectl)    kubectl version --client -o json 2>/dev/null \
                        | ${PYTHON_CMD} -c "import sys,json; print(json.load(sys.stdin)['clientVersion']['gitVersion'])" 2>/dev/null \
                        || kubectl version --client 2>&1 | head -1 ;;
        helm)       helm version --short 2>&1 | tr -d '\n' ;;
        oc)         oc version --client 2>&1 | head -1 | awk '{print $NF}' ;;
        helmfile)   helmfile --version 2>&1 | awk '{print $NF}' ;;
        kustomize)  kustomize version 2>&1 | head -1 ;;
        jq)         jq --version 2>&1 ;;
        yq)         yq --version 2>&1 | awk '{print $NF}' ;;
        skopeo)     skopeo --version 2>&1 | awk '{print $NF}' ;;
        crane)      crane version 2>&1 | tr -d '\n' ;;
        *)          echo "(unknown)" ;;
    esac
}

# ---------------------------------------------------------------------------
# Per-tool Linux install helpers
# ---------------------------------------------------------------------------
install_yq_linux() {
    local version=v4.45.4
    local binary=yq_linux_amd64
    curl -sL "https://github.com/mikefarah/yq/releases/download/${version}/${binary}" -o "/tmp/${binary}"
    chmod +x "/tmp/${binary}"
    sudo cp -f "/tmp/${binary}" /usr/local/bin/yq
}

install_helmfile_linux() {
    local version=1.1.3
    local pkg="helmfile_${version}_linux_amd64"
    curl -sL "https://github.com/helmfile/helmfile/releases/download/v${version}/${pkg}.tar.gz" -o "/tmp/${pkg}.tar.gz"
    tar xzf "/tmp/${pkg}.tar.gz" -C /tmp
    sudo cp -f /tmp/helmfile /usr/local/bin/helmfile
}

install_helm_linux() {
    curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash || { echo "ERROR: Failed to install Helm"; exit 1; }
    helm version --short || { echo "ERROR: Helm installation verification failed"; exit 1; }
}

install_oc_linux() {
    local arch
    arch=$(uname -m)
    local oc_file="openshift-client-linux"
    [[ "$arch" == "aarch64" ]] && oc_file="${oc_file}-arm64-rhel9"
    oc_file="${oc_file}.tar.gz"
    curl -sL "https://mirror.openshift.com/pub/openshift-v4/${arch}/clients/ocp/stable/${oc_file}" -o "/tmp/${oc_file}"
    tar xzf "/tmp/${oc_file}" -C /tmp
    sudo mv /tmp/oc /usr/local/bin/
    sudo chmod +x /usr/local/bin/oc
}

install_kustomize_linux() {
    curl -s "https://raw.githubusercontent.com/kubernetes-sigs/kustomize/master/hack/install_kustomize.sh" | bash
    sudo mv kustomize /usr/local/bin/
}

install_crane_linux() {
    local version=v0.20.3
    local arch
    arch=$(uname -m)
    local go_arch="x86_64"
    [[ "$arch" == "aarch64" ]] && go_arch="arm64"
    local pkg="go-containerregistry_Linux_${go_arch}"
    curl -sL "https://github.com/google/go-containerregistry/releases/download/${version}/${pkg}.tar.gz" -o "/tmp/${pkg}.tar.gz"
    tar xzf "/tmp/${pkg}.tar.gz" -C /tmp crane
    sudo cp -f /tmp/crane /usr/local/bin/crane
    sudo chmod +x /usr/local/bin/crane
}

install_oc_mac() { brew install openshift-cli; }

# ---------------------------------------------------------------------------
# Check required tools (fail if missing)
# ---------------------------------------------------------------------------
for tool in $tools; do
    if grep -q "${tool} already installed." "$dependencies_checked_file" 2>/dev/null; then
        continue
    fi
    if command -v "$tool" &>/dev/null; then
        printf "  %-14s %-20s %s\n" "$tool" "$(tool_version "$tool")" ""
        echo "${tool} already installed." >> "$dependencies_checked_file"
    else
        echo "  ${tool} — NOT FOUND, attempting install..."
        install_func="install_${tool}_${target_os}"
        if declare -F "$install_func" &>/dev/null; then
            eval "$install_func"
        else
            ${PKG_MGR} "$tool" || true
        fi
        if command -v "$tool" &>/dev/null; then
            printf "  %-14s %-20s %s\n" "$tool" "$(tool_version "$tool")" "(newly installed)"
            echo "${tool} already installed." >> "$dependencies_checked_file"
        else
            echo "ERROR: Failed to install required tool: ${tool}"
            exit 1
        fi
    fi
done

# ---------------------------------------------------------------------------
# Ensure helm-diff plugin is installed (required by helmfile apply).
# Runs regardless of whether helm was just installed or already existed.
# ---------------------------------------------------------------------------
helm_diff_url="https://github.com/databus23/helm-diff"

if command -v helm &>/dev/null; then
    if ! helm plugin list 2>/dev/null | grep -q "^diff"; then
        echo "  helm-diff    -- NOT FOUND, installing..."
        if ! helm plugin install ${helm_diff_url}; then
            echo "First attempt failed, retrying without signature verification..."
            if ! helm plugin install ${helm_diff_url} --verify=false; then
                echo "ERROR: Failed to install helm-diff plugin"; exit 1
            fi
        fi
        printf "  %-14s %-20s %s\n" "helm-diff" "$(helm plugin list | grep '^diff' | awk '{print $2}')" "(newly installed)"
    else
        printf "  %-14s %-20s %s\n" "helm-diff" "$(helm plugin list | grep '^diff' | awk '{print $2}')" ""
    fi
fi

# ---------------------------------------------------------------------------
# Check optional tools (warn but don't fail)
# ---------------------------------------------------------------------------
for tool in $optional_tools; do
    if grep -q "${tool} already installed." "$dependencies_checked_file" 2>/dev/null; then
        continue
    fi
    if command -v "$tool" &>/dev/null; then
        printf "  %-14s %-20s %s\n" "$tool" "$(tool_version "$tool")" ""
        echo "${tool} already installed." >> "$dependencies_checked_file"
    else
        printf "  %-14s %-20s %s\n" "$tool" "—" "(optional, not found)"
    fi
done

# ===================================================================
# Python package installation
# ===================================================================
echo ""
echo "=== Python packages ==="

# ---------------------------------------------------------------------------
# Helper — print package name + version in aligned columns
# ---------------------------------------------------------------------------
print_pkg() {
    local name="$1" status="$2"
    local ver
    ver=$(${PIP_CMD} show "$name" 2>/dev/null | awk '/^Version:/{print $2}')
    ver="${ver:---}"
    printf "  %-22s %-14s %s\n" "$name" "$ver" "$status"
}

# ---------------------------------------------------------------------------
# 1. Install llmdbenchmark (editable)
# ---------------------------------------------------------------------------
if grep -q "llmdbenchmark is already installed." "$dependencies_checked_file" 2>/dev/null; then
    print_pkg llmdbenchmark ""
else
    if ${PIP_CMD} install -e "${SCRIPT_DIR}" --quiet 2>/dev/null; then
        print_pkg llmdbenchmark "(installed)"
        echo "llmdbenchmark is already installed." >> "$dependencies_checked_file"
    else
        echo "ERROR: Failed to install llmdbenchmark!"
        exit 1
    fi
fi

# ---------------------------------------------------------------------------
# 2. Install config_explorer (editable)
# ---------------------------------------------------------------------------
config_explorer_dir="${SCRIPT_DIR}/config_explorer"

if [[ ! -d "$config_explorer_dir" ]]; then
    echo "ERROR: config_explorer directory not found at ${config_explorer_dir}"
    exit 1
fi

if grep -q "config_explorer is already installed." "$dependencies_checked_file" 2>/dev/null; then
    print_pkg config_explorer ""
else
    if ${PIP_CMD} install -e "${config_explorer_dir}" --quiet 2>/dev/null; then
        print_pkg config_explorer "(installed)"
        echo "config_explorer is already installed." >> "$dependencies_checked_file"
    else
        echo "ERROR: Failed to install config_explorer!"
        exit 1
    fi
fi

# ---------------------------------------------------------------------------
# 3. Show key dependencies
# ---------------------------------------------------------------------------
echo ""
echo "  Dependencies:"
for pkg in PyYAML Jinja2 requests kubernetes pykube-ng kubernetes-asyncio \
           GitPython huggingface_hub transformers packaging \
           pydantic scipy pandas numpy matplotlib; do
    ver=$(${PIP_CMD} show "$pkg" 2>/dev/null | awk '/^Version:/{print $2}')
    if [[ -n "$ver" ]]; then
        printf "    %-22s %s\n" "$pkg" "$ver"
    fi
done

# ---------------------------------------------------------------------------
# 4. Verify imports
# ---------------------------------------------------------------------------
echo ""
import_ok=true
if ! ${PYTHON_CMD} -c "import llmdbenchmark" 2>/dev/null; then
    echo "WARNING: llmdbenchmark installed but not importable"
    import_ok=false
fi
if ! ${PYTHON_CMD} -c "import config_explorer" 2>/dev/null; then
    echo "WARNING: config_explorer installed but not importable"
    import_ok=false
fi
if ! ${PYTHON_CMD} -c "from config_explorer.capacity_planner import model_memory_req" 2>/dev/null; then
    echo "WARNING: config_explorer.capacity_planner not importable"
    import_ok=false
fi
if [[ "$import_ok" == "true" ]]; then
    echo "All imports verified."
fi

echo ""
echo "=== Done ==="

echo ""
echo "Reminder: Please activate the virtual environment in your shell:"
echo ""
echo "  source ${VENV_DIR}/bin/activate"
echo ""
echo "To deactivate the virtual environment in your shell:"
echo ""
echo "  deactivate"
echo ""
echo ""
