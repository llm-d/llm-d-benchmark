#!/usr/bin/env bash

if [[ $0 != "-bash" ]]; then
    pushd `dirname "$(realpath $0)"` > /dev/null 2>&1
fi

mydir=$(realpath $(pwd)/)"/../"

if [ $0 != "-bash" ] ; then
    popd  > /dev/null 2>&1
fi

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]
then
    echo "please run \"source $0\""
    exit 1
fi

excluded_envars="LLMDBENCH_CONTROL_PCMD|LLMDBENCH_CONTROL_KCMD|LLMDBENCH_CONTROL_HCMD|LLMDBENCH_HF_TOKEN|LLMDBENCH_CONTROL_WORK_DIR"
echo "📜 Resetting all environment variables LLMDBENCH to default values..."
echo "ℹ️ Excluding the following variables: $(echo $excluded_envars | sed 's/|/, /g')"
for envar in $(env | grep LLMDBENCH | grep -Ev ${excluded_envars} | sort); do
  CMD="unset $(echo $envar | cut -d '=' -f 1)"
  echo $CMD
  eval $CMD
done
echo "✅ All environment variables LLMDBENCH reset to default values"
