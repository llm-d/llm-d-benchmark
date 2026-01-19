#!/usr/bin/env python3

import subprocess
import ipaddress
import os
from pathlib import Path

ip_info={}
curr_if=''
hca_info={}
curr_hca=''

deps_checked = True
for dep in [ 'ip', 'ibstat' ] :
    try :
        result = subprocess.run(['which', 'ip'], capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as e:
        print(f"WARNING: Dependency '{dep}' not available on the image {e.cmd} returned {e.returncode}.")
        deps_checked = False

if deps_checked :
    result = subprocess.run(['ip', '-o', 'a', 'list'], capture_output=True, text=True, check=True)
    for line in result.stdout.split('\n') :
        if line.count('inet ') :
            curr_if = line.split()[1]
            curr_ipv4 = line.split()[3]
        if line.count('inet6') :
            curr_ipv6=line.split()[3]
            curr_last_octect=curr_ipv6.split(':')[-1].split('/')[0]
            ip_info[curr_last_octect] = {}
            ip_info[curr_last_octect]['interface_name'] = curr_if
            ip_info[curr_last_octect]['ipv4'] = curr_ipv4
            ip_info[curr_last_octect]['ipv6'] = curr_ipv6
    #print(ip_info)
    result = subprocess.run(['ibstat'], capture_output=True, text=True, check=True)
    for line in result.stdout.split('\n') :
        if line.count("CA '") :
            curr_hca=line.split("'")[1].strip()
            hca_info[curr_hca] = {}
            hca_info[curr_hca]['hca_id'] = curr_hca
        if line.count('Port ') and not line.count('GUID') :
            hca_info[curr_hca]['port'] = line.split('Port ')[-1].split(':')[0].strip()
        if line.count('Node GUID') :
            hca_info[curr_hca]['node_guid'] = line.split(':')[-1].strip()
            hca_info[curr_hca]['node_guid'] = str(ipaddress.IPv6Address(int(hca_info[curr_hca]['node_guid'],16)))
            hca_info[curr_hca]['last_octect'] = hca_info[curr_hca]['node_guid'].split(':')[-1]
        if line.count('State') :
            hca_info[curr_hca]['status'] = line.split(':')[-1].strip().replace('Active','UP').replace('Down','DOWN')

    nccl_list =[]
    nixl_list =[]

    c1="mlx name"
    c2="node guid"
    c3="port"
    c4="state"
    c5="if name"
    c6="ipv4"
    c7="ipv6"
    print(f"{c1.ljust(10)} {c2.ljust(25)} {c3.ljust(5)} {c4.ljust(5)} {c5.ljust(10)} {c6.ljust(20)} {c7}")
    for entry in hca_info.keys() :
        lo = hca_info[entry]['last_octect']
        stat = hca_info[entry]['status']
        node_guid = hca_info[entry]['node_guid']
        port = hca_info[entry]['port']
        if_name = "N/A"
        ipv4 = "N/A"
        ipv6 = "N/A"
        if lo in ip_info :
            if_name = ip_info[lo]['interface_name']
            ipv4 = ip_info[lo]['ipv4']
            ipv6 = ip_info[lo]['ipv6']
            if hca_info[entry]["status"] == "UP" :
                nccl_list.append(f"{entry}")
                nixl_list.append(f"{if_name}")
        print(f"{entry.ljust(10)} {node_guid.ljust(25)} {port.ljust(5)} {stat.ljust(5)} {if_name.ljust(10)} {ipv4.ljust(20)} {ipv6}")
else :
    print(f"WARNING: Unable to create network device file map.")

env_file_contents=[]
env_file_name=f"{Path.home()}/llmdbench_env.sh"
env_file_contents.append("#!/usr/bin/env bash")
if nixl_list :
    print()
    nccl_list = ','.join(nccl_list)
    nixl_list = ','.join(nixl_list)
    env_file_contents.append(f"export UCX_NET_DEVICES=\"{nixl_list}\"")
    env_file_contents.append(f"export NCCL_IB_HCA=\"={nccl_list}\"")

lwswi = os.getenv("LWS_WORKER_INDEX", "0")
dpsi = os.getenv("DP_SIZE_LOCAL", "0")
sr = int(lwswi) * int(dpsi)
env_file_contents.append(f"export START_RANK=\"{sr}\"")

env_file_contents.append("if [[ -z $LWS_WORKER_INDEX ]]; then")
env_file_contents.append("  find /dev/shm -type f -delete")
env_file_contents.append("fi")

env_file_contents.append("if [[ ! -z $UCX_NET_DEVICES && ! -z NCCL_IB_HCA ]]; then")
env_file_contents.append(" for BDF in $(lspci -d \"*:*:*\" | awk '{print $1}'); do")
env_file_contents.append("    setpci -v -s ${BDF} ECAP_ACS+0x6.w > /dev/null 2>&1")
env_file_contents.append("    if [ $? -ne 0 ]; then")
env_file_contents.append("      echo \"ACS is already disabled for PCI device \\\"${BDF}\\\"\"")
env_file_contents.append("      continue")
env_file_contents.append("    fi")
env_file_contents.append("    setpci -v -s ${BDF} ECAP_ACS+0x6.w=0000 > /dev/null 2>&1")
env_file_contents.append("    if [ $? -eq 0 ]; then")
env_file_contents.append("      echo \"ACS disabled for PCI device \\\"${BDF}\\\"\"")
env_file_contents.append("    else")
env_file_contents.append("      echo \"WARNING: Failed to disable ACS for PCI device \\\"${BDF}\\\"\"")
env_file_contents.append("    fi")
env_file_contents.append("  done")
env_file_contents.append("fi")

env_file_contents.append("echo")
env_file_contents.append("env | grep -E 'UCX|NCCL' | sort")
env_file_contents.append("echo")

env_file_contents='\n'.join(env_file_contents)

with open(env_file_name, "w") as file:
    file.write(env_file_contents)
