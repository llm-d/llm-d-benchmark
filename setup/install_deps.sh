#!/usr/bin/env bash

is_mac=$(uname -s | grep -i darwin || true)
if [[ ! -z $is_mac ]]; then
    target_os=mac
else
    target_os=linux
fi

dependencies_checked_file=~/.llmdbench_dependencies_checked

if [[ $1 == "noreset" ]]; then
  true
else
  rm -f $dependencies_checked_file
fi

# common deps
tools="sed python3 curl git oc kubectl helm helmfile kustomize rsync make skopeo jq yq openssl"

# get package manager
if [ "$target_os" = "mac" ]; then
    PKG_MGR="brew install"
elif command -v apt &> /dev/null; then
    PKG_MGR="sudo apt install -y"
elif command -v apt-get &> /dev/null; then
    PKG_MGR="sudo apt-get install -y"
elif command -v brew &> /dev/null; then
    PKG_MGR="brew install"
elif command -v yum &> /dev/null; then
    PKG_MGR="sudo yum install -y"
elif command -v dnf &> /dev/null; then
    PKG_MGR="sudo dnf install -y"
else
    echo "No supported package manager found (apt, apt-get, brew, yum, dnf)"
    exit 1
fi

function install_yq_linux {
    set -euo pipefail
    local version=v4.45.4
    local binary=yq_linux_amd64
    curl -L https://github.com/mikefarah/yq/releases/download/${version}/${binary} -o ${binary}
    chmod +x ${binary}
    sudo cp -f ${binary} /usr/local/bin/yq
    set +euo pipefail
}

function install_helmfile_linux {
    set -euo pipefail
    local version=1.1.3
    local pkg=helmfile_${version}_linux_amd64

    curl -L https://github.com/helmfile/helmfile/releases/download/v${version}/${pkg}.tar.gz -o ${pkg}.tar.gz
    tar xzf ${pkg}.tar.gz
    sudo cp -f helmfile /usr/local/bin/helmfile
    set +euo pipefail
}

function install_helm_linux {
    set -euo pipefail
    curl -fsSL -o get_helm.sh https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3
    chmod 700 get_helm.sh
    sudo ./get_helm.sh
    is_plugin_diff=$(helm plugin list | grep ^diff || true)
    if [[ -z $is_plugin_diff ]]; then
        helm plugin install https://github.com/databus23/helm-diff
    fi
    set +euo pipefail
}

function install_oc_linux {
    set -euo pipefail
    local oc_file_name=openshift-client-$(uname -s | sed -e "s/Linux/linux/g" -e "s/Darwin/apple-darwin/g")$(echo "-$(uname -m)" | sed -e 's/-x86_64//g' -e 's/-amd64//g' -e 's/aarch64/arm64-rhel9/g').tar.gz
    curl -L https://mirror.openshift.com/pub/openshift-v4/$(uname -m)/clients/ocp/stable/$oc_file_name  -o $oc_file_name
    tar xzf $oc_file_name
    sudo mv oc /usr/local/bin/
    sudo mv kubectl /usr/local/bin/
    sudo chmod +x /usr/local/bin/oc
    sudo chmod +x /usr/local/bin/kubectl
    rm openshift-client-*.tar.gz
    set +euo pipefail
}

function install_oc_mac {
    brew install openshift-cli
}

function install_sed_mac {
    brew install gsed
}

function install_kustomize_linux {
    set -euo pipefail
    curl -s "https://raw.githubusercontent.com/kubernetes-sigs/kustomize/master/hack/install_kustomize.sh"  | bash
    chmod +x kustomize
    sudo mv kustomize /usr/local/bin
    set +euo pipefail
}

pushd /tmp &>/dev/null
for tool in $tools; do
    grep -q "$tool already installed." $dependencies_checked_file
    if [[ $? -ne 0 ]]; then
        if command -v $tool &> /dev/null; then
            echo "$tool already installed." >> $dependencies_checked_file
            continue
        fi
        echo "---------------------------"
        echo "Installing $tool..."
        install_func=install_${tool}_$target_os
        if declare -F "$install_func" &>/dev/null; then
            eval $install_func
        else
            $PKG_MGR $tool || echo "Could not install $tool"
        fi
        if command -v $tool &> /dev/null; then
            true
        else
            echo "$tool failed to install!"
            exit 1
        fi
        echo "---------------------------"
    fi
done

#
#
# Check minimum Python version (3.11+) based on new requirements
#

grep -q "is available on system." $dependencies_checked_file
if [[ $? -ne 0 ]]; then
    python_present=""
    verlist=""
    for pybin in python3 python3.{13..11}; do
        if command -v ${pybin} &>/dev/null; then
            ver=$(${pybin} -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
            verlist=$ver,$verlist
            major=$(echo ${ver} | cut -d. -f1)
            minor=$(echo ${ver} | cut -d. -f2)
            if (( major > 3 || (major == 3 && minor >= 11) )); then
                python_present=$(command -v ${pybin})
                break
            fi
        fi
    done

    if [[ -z "${python_present}" ]]; then
        echo "ERROR: Python 3.11 and up is required, but only versions \"$(echo ${verlist} | sed 's^,$^^g')\" found."
        exit 1
    else
        echo "${python_present} is available on system." >> $dependencies_checked_file
    fi
fi

grep -q "pip3 installed successfully." $dependencies_checked_file
if [[ $? -ne 0 ]]; then
    if ! command -v pip3 &> /dev/null; then
        echo "pip3 not found. Attempting to install it..."
        if [ "$target_os" = "mac" ]; then
            echo "ERROR: pip3 not found. Please ensure python3 from Homebrew is correctly installed"
            echo "Try running 'brew doctor' or 'brew reinstall python3'"
            exit 1
        elif [ "$target_os" = "linux" ]; then
            PIP_PACKAGE="python3-pip"
            echo "Attempting to install $PIP_PACKAGE using the system package manager..."
            $PKG_MGR $PIP_PACKAGE
        fi

        # verify pip was installed successfully
        if ! command -v pip3 &> /dev/null; then
            echo "ERROR: Failed to install pip3. Please install it manually and re-run the script."
            exit 1
        fi
        echo "pip3 installed successfully." >> $dependencies_checked_file
    fi
fi

python_deps="kubernetes pykube-ng kubernetes-asyncio GitPython requests PyYAML Jinja2 requests huggingface_hub==0.34.4 transformers==4.55.4"

for dep in $python_deps; do
    pkg_name=$(echo "${dep}" | cut -d= -f1)
    grep -q "$(echo $dep) is already installed." $dependencies_checked_file
    if [[ $? -ne 0 ]]; then
        importdep="import $(echo $dep | cut -d '=' -f 1 | tr '[:upper:]' '[:lower:]' | sed -e 's/-ng//g' -e 's/gitpython/git/g' -e 's/pyyaml/yaml/g' -e 's/-/_/g')"
        echo "$importdep"
        if pip3 show "${pkg_name}" &>/dev/null; then
            # check if a version was specified
            if [[ "${dep}" == *"=="* ]]; then
                required_version=$(echo "${dep}" | cut -d= -f3)
                installed_version=$(pip3 show "${pkg_name}" | awk '/Version:/{print $2}')
                if [[ "${installed_version}" == "${required_version}" ]]; then
                    echo "${pkg_name}==${installed_version} is already installed." >> $dependencies_checked_file
                    continue
                else
                    echo "${pkg_name} installed but version mismatch (${installed_version} != ${required_version}). Upgrading..."
                fi
            else
                echo "${pkg_name} is already installed." >> $dependencies_checked_file
                continue
            fi
        fi

        echo "Installing ${dep}..."
        if ! pip3 install "${dep}"; then
            echo "ERROR: Failed to install Python package ${dep}!"
            exit 1
        fi
    fi
done

popd &>/dev/null
