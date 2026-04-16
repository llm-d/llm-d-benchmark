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
# Installs the llmdbenchmark CLI, planner, and validates
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
export LLMDBENCH_CONTROL_PCMD=${LLMDBENCH_CONTROL_PCMD:-python}

# ---------------------------------------------------------------------------
# pip isolation
# Any pip.conf on the machine (e.g. a corporate index that injects credentials
# or redirects to an internal mirror) will break:
#   - editable local installs  (index doesn't know about local paths)
#   - git+https:// installs    (index can't proxy GitHub source URLs)
#
# We force every pip call in this script to run with:
#   PIP_CONFIG_FILE=/dev/null   -- ignores all pip.conf files
#   --isolated                  -- disables env-var overrides too
#
# PyPI is still reachable; we are only suppressing *custom* index injection.
# If your environment truly requires a proxy, set LLMDBENCH_PIP_EXTRA_ARGS
# before running, e.g.:
#   export LLMDBENCH_PIP_EXTRA_ARGS="--index-url https://my.mirror/simple"
# ---------------------------------------------------------------------------
LLMDBENCH_PIP_EXTRA_ARGS="${LLMDBENCH_PIP_EXTRA_ARGS:-}"
_pip_isolated() {
    PIP_CONFIG_FILE=/dev/null ${PIP_CMD} --isolated "$@" ${LLMDBENCH_PIP_EXTRA_ARGS}
}

# ---------------------------------------------------------------------------
# Architecture detection
# Normalises uname -m output into two variables used throughout:
#   ARCH_UNAME  -- raw uname -m value  (x86_64 | aarch64 | armv7l | ...)
#   ARCH_GO     -- Go-style name       (amd64  | arm64   | arm    | ...)
#   ARCH_DEB    -- Debian/apt name     (amd64  | arm64   | armhf  | ...)
# ---------------------------------------------------------------------------
ARCH_UNAME="$(uname -m)"
case "$ARCH_UNAME" in
    x86_64)          ARCH_GO="amd64";   ARCH_DEB="amd64"  ;;
    aarch64|arm64)   ARCH_GO="arm64";   ARCH_DEB="arm64"  ;;
    armv7l|armhf)    ARCH_GO="arm";     ARCH_DEB="armhf"  ;;
    ppc64le)         ARCH_GO="ppc64le"; ARCH_DEB="ppc64el" ;;
    s390x)           ARCH_GO="s390x";   ARCH_DEB="s390x"  ;;
    *)
        echo "WARNING: Unrecognised architecture '${ARCH_UNAME}'. Assuming amd64."
        ARCH_GO="amd64"; ARCH_DEB="amd64"
        ;;
esac

# ---------------------------------------------------------------------------
# Bootstrap: if run via curl (no repo present), clone first
#   curl -sSL https://raw.githubusercontent.com/llm-d/llm-d-benchmark/main/install.sh | bash
# ---------------------------------------------------------------------------
_bootstrap_if_needed() {
    local need_clone=false

    if [[ -z "${BASH_SOURCE[0]:-}" || "${BASH_SOURCE[0]}" == "bash" || "${BASH_SOURCE[0]}" == "/dev/stdin" ]]; then
        need_clone=true
    elif [[ ! -f "pyproject.toml" && ! -d "llmdbenchmark" ]]; then
        need_clone=true
    fi

    if [[ "$need_clone" == "true" ]]; then
        echo ""
        echo "  llm-d-benchmark repository not detected in current directory."
        echo ""

        if [[ -d "${REPO_DIR}" && -f "${REPO_DIR}/pyproject.toml" ]]; then
            echo "  Found existing clone at ./${REPO_DIR}"
            cd "${REPO_DIR}"
        else
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
    2. Checks for required system tools  (curl, git, kubectl, helm, helmfile,
                                          skopeo, kustomize, jq, yq, crane)
    3. Checks for optional system tools   (oc)
    4. Installs llmdbenchmark             (editable: pip install -e .)
    5. Installs planner (llm-d-planner)  (pip install git+https://...)
    6. Verifies that all Python packages are importable

    Supported architectures: x86_64 (amd64), aarch64/arm64, armv7l, ppc64le, s390x.

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
LLMDBENCH_VENV_DIR=${LLMDBENCH_VENV_DIR:-"${SCRIPT_DIR}/.venv"}
LLMDBENCH_SYSTEM_PYTHON=${LLMDBENCH_SYSTEM_PYTHON:-python3}
CREATED_VENV=false

_detected_venv="${VIRTUAL_ENV:-${CONDA_PREFIX:-}}"
if [[ -n "$_detected_venv" && -d "$_detected_venv" ]]; then
    if command -v python &>/dev/null; then
        PYTHON_CMD="python"
        PIP_CMD="python -m pip"
    else
        PYTHON_CMD="python3"
        PIP_CMD="python3 -m pip"
    fi
    echo "Virtual environment detected: ${_detected_venv}"
elif [[ "$allow_system_python" == "true" ]]; then
    PYTHON_CMD=$LLMDBENCH_SYSTEM_PYTHON
    PIP_CMD="$PYTHON_CMD -m pip"
    echo "Using system python3 (forced with -y flag)"
else
    if [[ -d "$LLMDBENCH_VENV_DIR" ]]; then
        if grep -q "venv created." "$dependencies_checked_file" 2>/dev/null; then
            true
        else
            echo "Using existing virtual environment: ${LLMDBENCH_VENV_DIR}"
            echo "venv created." >> "$dependencies_checked_file"
        fi
    else
        PYTHON_CMD=$LLMDBENCH_SYSTEM_PYTHON
        echo "No virtual environment detected — creating ${LLMDBENCH_VENV_DIR} with $PYTHON_CMD..."
        $PYTHON_CMD -m venv "$LLMDBENCH_VENV_DIR"
        CREATED_VENV=true
        echo "Virtual environment created: ${LLMDBENCH_VENV_DIR}"
        echo "venv created." >> "$dependencies_checked_file"
    fi
    # shellcheck disable=SC1091
    source "${LLMDBENCH_VENV_DIR}/bin/activate"
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
echo "Python ${python_version} — OK  [arch: ${ARCH_UNAME} → ${ARCH_GO}]"

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
# System tool checks
# ===================================================================
echo ""
echo "=== System tools ==="

tools="curl git helm helmfile skopeo kustomize jq yq crane"

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

optional_tools="oc"

# ---------------------------------------------------------------------------
# Version helper
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
# Linux install helpers — all now arch-aware via $ARCH_GO / $ARCH_UNAME
# ---------------------------------------------------------------------------

install_yq_linux() {
    local version=v4.52.5
    local binary="yq_linux_${ARCH_GO}"
    curl -sL "https://github.com/mikefarah/yq/releases/download/${version}/${binary}" \
        -o "/tmp/${binary}"
    chmod +x "/tmp/${binary}"
    sudo cp -f "/tmp/${binary}" /usr/local/bin/yq
}

install_helmfile_linux() {
    local version=1.1.3
    local oc_arch="${ARCH_UNAME}"
    if [ "${oc_arch}" = "s390x" ]; then
	    git clone  https://github.com/helmfile/helmfile.git
	    cd helmfile || exit 1
	    git checkout v1.1.3
	    GOARCH=s390x GOOS=linux go build -o helmfile
	    sudo mv helmfile /usr/local/bin/
	    sudo chmod +x /usr/local/bin/helmfile
    else
    	local pkg="helmfile_${version}_linux_${ARCH_GO}"
    	curl -sL "https://github.com/helmfile/helmfile/releases/download/v${version}/${pkg}.tar.gz" \
        	-o "/tmp/${pkg}.tar.gz"
    	tar xzf "/tmp/${pkg}.tar.gz" -C /tmp
    	sudo cp -f /tmp/helmfile /usr/local/bin/helmfile
    fi
}

install_helm_linux() {
    # The official get-helm-3 script is arch-aware
    curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash \
        || { echo "ERROR: Failed to install Helm"; exit 1; }
    helm version --short || { echo "ERROR: Helm installation verification failed"; exit 1; }
}

install_kubectl_linux() {
    local stable
    stable=$(curl -sL https://dl.k8s.io/release/stable.txt)
    curl -sL "https://dl.k8s.io/release/${stable}/bin/linux/${ARCH_GO}/kubectl" \
        -o /tmp/kubectl
    chmod +x /tmp/kubectl
    sudo mv /tmp/kubectl /usr/local/bin/kubectl
}

install_oc_linux() {
    # oc mirrors use aarch64 / x86_64 naming, not Go-style
    local oc_arch="${ARCH_UNAME}"  # x86_64 | aarch64 | ppc64le | s390x
    local oc_file="openshift-client-linux"
    [[ "$oc_arch" == "aarch64" ]] && oc_file="${oc_file}-arm64-rhel9"
    oc_file="${oc_file}.tar.gz"
    curl -sL "https://mirror.openshift.com/pub/openshift-v4/${oc_arch}/clients/ocp/stable/${oc_file}" \
        -o "/tmp/${oc_file}"
    tar xzf "/tmp/${oc_file}" -C /tmp
    sudo mv /tmp/oc /usr/local/bin/
    sudo chmod +x /usr/local/bin/oc
}

install_kustomize_linux() {
    # The upstream install_kustomize.sh script is arch-aware (detects GOARCH internally)
    curl -s "https://raw.githubusercontent.com/kubernetes-sigs/kustomize/master/hack/install_kustomize.sh" | bash
    sudo mv kustomize /usr/local/bin/
}

install_crane_linux() {
    local version=v0.20.3
    # go-containerregistry release tarballs use Go arch names (X86_64 capitalised)
    local go_arch_cap
    case "$ARCH_GO" in
        amd64)   go_arch_cap="x86_64" ;;
        arm64)   go_arch_cap="arm64"  ;;
        arm)     go_arch_cap="armv6"  ;;
        ppc64le) go_arch_cap="ppc64le" ;;
        s390x)   go_arch_cap="s390x"  ;;
        *)       go_arch_cap="x86_64" ;;
    esac
    local pkg="go-containerregistry_Linux_${go_arch_cap}"
    curl -sL "https://github.com/google/go-containerregistry/releases/download/${version}/${pkg}.tar.gz" \
        -o "/tmp/${pkg}.tar.gz"
    tar xzf "/tmp/${pkg}.tar.gz" -C /tmp crane
    sudo cp -f /tmp/crane /usr/local/bin/crane
    sudo chmod +x /usr/local/bin/crane
}

install_skopeo_linux() {
    # skopeo is widely available in distro package managers
    ${PKG_MGR} skopeo || true
}

# ---------------------------------------------------------------------------
# Ensure PostgreSQL dev headers are present (required to build psycopg2 from
# source on architectures that lack pre-built wheels, e.g. s390x, ppc64le).
# ---------------------------------------------------------------------------
install_pg_dev_deps() {
    if command -v pg_config &>/dev/null; then
        return 0
    fi
    echo "  pg_config not found — installing PostgreSQL dev headers..."
    if command -v dnf &>/dev/null; then
        sudo dnf install -y postgresql-devel python3-devel gcc || true
    elif command -v yum &>/dev/null; then
        sudo yum install -y postgresql-devel python3-devel gcc || true
    elif command -v apt-get &>/dev/null; then
        sudo apt-get install -y libpq-dev python3-dev gcc || true
    elif command -v apt &>/dev/null; then
        sudo apt install -y libpq-dev python3-dev gcc || true
    else
        echo "  WARNING: Cannot install pg_config automatically. If planner install fails,"
        echo "           install postgresql-devel (rpm) or libpq-dev (deb) manually."
    fi
}

# ---------------------------------------------------------------------------
# macOS install helpers — Homebrew handles arch transparently on both
# Intel and Apple Silicon, so these are simple wrappers.
# ---------------------------------------------------------------------------
install_yq_mac()       { brew install yq; }
install_helmfile_mac() { brew install helmfile; }
install_helm_mac()     { brew install helm; }
install_kubectl_mac()  { brew install kubectl; }
install_oc_mac()       { brew install openshift-cli; }
install_kustomize_mac(){ brew install kustomize; }
install_crane_mac()    { brew install crane; }
install_skopeo_mac()   { brew install skopeo; }
install_jq_mac()       { brew install jq; }

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
# Ensure helm-diff plugin is installed
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

print_pkg() {
    local name="$1" status="$2"
    local ver
    ver=$(_pip_isolated show "$name" 2>/dev/null | awk '/^Version:/{print $2}')
    ver="${ver:---}"
    printf "  %-22s %-14s %s\n" "$name" "$ver" "$status"
}

# 1. Install llmdbenchmark (editable)
if grep -q "llmdbenchmark is already installed." "$dependencies_checked_file" 2>/dev/null; then
    print_pkg llmdbenchmark ""
else
    if _pip_isolated install -e "${SCRIPT_DIR}" --quiet; then
        print_pkg llmdbenchmark "(installed)"
        echo "llmdbenchmark is already installed." >> "$dependencies_checked_file"
    else
        echo "ERROR: Failed to install llmdbenchmark!"
        exit 1
    fi
fi

# 2. Install planner
PLANNER_GIT="git+https://github.com/llm-d-incubation/llm-d-planner.git@f51812bebca30e0291ec541bd2ef2acf0572e8a4"

if grep -q "planner is already installed." "$dependencies_checked_file" 2>/dev/null; then
    print_pkg planner ""
else
    # psycopg2-binary is a planner dependency. On architectures without pre-built
    # wheels (s390x, ppc64le, older arm64 builds) pip falls back to compiling from
    # source and requires pg_config / PostgreSQL dev headers. We ensure those are
    # present first, then force the binary wheel where available so we never
    # needlessly compile from source.
    if [[ "$target_os" == "linux" ]]; then
        install_pg_dev_deps
    fi
    _pip_isolated install psycopg2-binary --only-binary=:all: --quiet 2>/dev/null \
        || true  # if no wheel exists for this arch, fall through to source build above

    if _pip_isolated install "${PLANNER_GIT}" --quiet; then
        print_pkg planner "(installed)"
        echo "planner is already installed." >> "$dependencies_checked_file"
    else
        echo "ERROR: Failed to install planner (llm-d-planner)!"
        exit 1
    fi
fi

# 3. Show key dependencies
echo ""
echo "  Dependencies:"
for pkg in PyYAML Jinja2 requests kubernetes pykube-ng kubernetes-asyncio \
           GitPython huggingface_hub transformers packaging \
           pydantic scipy pandas numpy; do
    ver=$(_pip_isolated show "$pkg" 2>/dev/null | awk '/^Version:/{print $2}')
    if [[ -n "$ver" ]]; then
        printf "    %-22s %s\n" "$pkg" "$ver"
    fi
done

# 4. Verify imports
echo ""
import_ok=true
if ! ${PYTHON_CMD} -c "import llmdbenchmark" 2>/dev/null; then
    echo "WARNING: llmdbenchmark installed but not importable"
    import_ok=false
fi
if ! ${PYTHON_CMD} -c "import planner" 2>/dev/null; then
    echo "WARNING: planner installed but not importable"
    import_ok=false
fi
if ! ${PYTHON_CMD} -c "from planner.capacity_planner import model_memory_req" 2>/dev/null; then
    echo "WARNING: planner.capacity_planner not importable"
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
echo "  source ${LLMDBENCH_VENV_DIR}/bin/activate"
echo ""
echo "To deactivate the virtual environment in your shell:"
echo ""
echo "  deactivate"
echo ""
echo ""
