#!/usr/bin/env bash

is_mac=$(uname -s | grep -i darwin || true)
if [[ ! -z $is_mac ]]; then
    target_os=mac
else
    target_os=linux
fi

rm -f ~/.llmdbench_dependencies_checked

# common deps
tools="gsed python3 curl git oc kubectl helm helmfile kustomize rsync make skopeo jq yq"

# get package manager
if command -v apt &> /dev/null; then
    PKG_MGR="sudo apt install -y"
    tools=$(echo $tools | sed 's/gsed /sed openssl /g')
elif command -v apt-get &> /dev/null; then
    PKG_MGR="sudo apt-get install -y"
elif command -v brew &> /dev/null; then
    PKG_MGR="brew install"
    tools=$(echo $tools | sed 's/ oc / openshift-cli openssl /g')
elif command -v yum &> /dev/null; then
    PKG_MGR="sudo yum install -y"
    tools=$(echo $tools | sed 's/gsed /sed openssl /g')
elif command -v dnf &> /dev/null; then
    PKG_MGR="sudo dnf install -y"
    tools=$(echo $tools | sed 's/gsed /sed openssl /g')
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
    curl https://mirror.openshift.com/pub/openshift-v4/$(uname -m)/clients/ocp/stable/$oc_file_name  -o $oc_file_name
    tar xzf $oc_file_name
    sudo mv oc /usr/local/bin/
    sudo mv kubectl /usr/local/bin/
    sudo chmod +x /usr/local/bin/oc
    sudo chmod +x /usr/local/bin/kubectl
    rm openshift-client-*.tar.gz
    set +euo pipefail
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
    if command -v $tool &> /dev/null; then
        echo "$tool already installed" >> ~/.llmdbench_dependencies_checked
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
done
popd &>/dev/null