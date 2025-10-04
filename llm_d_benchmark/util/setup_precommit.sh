#!/usr/bin/env bash

if [[ $0 != "-bash" ]]; then
    pushd `dirname "$(realpath $0)"` > /dev/null 2>&1
fi

mydir=$(realpath $(pwd)/)"/../"

if [ $0 != "-bash" ] ; then
    popd  > /dev/null 2>&1
fi

pushd $mydir &>/dev/null
python3 -m venv venv
source venv/bin/activate
pip3 install -r .pre-commit_requirements.txt
pre-commit install
