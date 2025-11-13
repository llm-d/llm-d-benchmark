#!/usr/bin/env bash

apt-get update

NSIGHT_SYSTEMS_VER=$(apt-cache policy nsight-systems-* | grep ^nsight | grep -v target | tail -1 | sed 's^:^^g')
NSIGHT_COMPUTE_VER=$(apt-cache policy nsight-compute-* | grep ^nsight | grep -v target | tail -1 | sed 's^:^^g')
apt install -y $NSIGHT_SYSTEMS_VER $NSIGHT_COMPUTE_VER