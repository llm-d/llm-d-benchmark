#!/usr/bin/env python3

import subprocess
import ipaddress
import os
import json
import time

from pathlib import Path

ip_address_info={}
ip_route_info={}
device_to_network={}
curr_if=''
hca_info={}
nccl_list =[]
nixl_list =[]
ips_for_fping = []
curr_hca=''
disable_acs = True

if os.getenv('FLEX_DEVICE','PF') == 'VF' :
    env_file_name=f"{Path.home()}/.senlib.json"
    print(f"INFO: Environment variable \"FLEX_DEVICE\" detected, will modify \"{env_file_name}\"")
    with open(env_file_name, "r", encoding="utf-8") as senlib_file:
        senlib_contents = json.load(senlib_file)
    senlib_contents['RISCV']['DOOM']['enable'] = True
    with open(env_file_name, 'w') as senlib_file:
        json.dump(senlib_contents, senlib_file, indent=4)

has_ip_command = True
try :
    result = subprocess.run(['which', 'ip'], capture_output=True, text=True, check=True)
except subprocess.CalledProcessError as e:
    print(f"WARNING: Dependency \"ip\" not available on the image {e.cmd} returned {e.returncode}.")
    has_ip_command = False

if has_ip_command :
    ip_address_list_command_output = subprocess.run(['ip', '-o', 'address', 'list'], capture_output=True, text=True, check=True)
    for line in ip_address_list_command_output.stdout.split('\n') :
        if line.count('inet ') :
            curr_if = line.split()[1]
            curr_ipv4 = line.split()[3]
        if line.count('inet6') :
            curr_ipv6=line.split()[3]
            curr_last_octect=curr_ipv6.split(':')[-1].split('/')[0]
            ip_address_info[curr_last_octect] = {}
            ip_address_info[curr_last_octect]['interface_name'] = curr_if
            ip_address_info[curr_last_octect]['ipv4'] = curr_ipv4
            ip_address_info[curr_last_octect]['ipv6'] = curr_ipv6
    #print(ip_address_info)

    default_interface = None
    ip_route_list_command_output = subprocess.run(['ip', 'route', 'list'], capture_output=True, text=True, check=True)
    for line in ip_route_list_command_output.stdout.split('\n') :
        if line and line.count('default') and not default_interface :
            default_interface = line.split()[-1]
            break

    for line in ip_route_list_command_output.stdout.split('\n') :
        if line and not line.count(default_interface) :
            network = line.split()[0]
            device = line.split()[2]

            if network not in ip_route_info :
                ip_route_info[network] = []

            if device not in ip_route_info[network] :
                ip_route_info[network].append(device)

            if device not in device_to_network :
                device_to_network[device] = network
    #print(ip_route_info)
    #print(device_to_network)

has_ibstat_command = True
try :
    result = subprocess.run(['which', 'ibstat'], capture_output=True, text=True, check=True)
except subprocess.CalledProcessError as e:
    print(f"WARNING: Dependency \"ibstat\" not available on the image {e.cmd} returned {e.returncode}.")
    has_ibstat_command = False

if has_ibstat_command :
    ibstat_command_output = subprocess.run(['ibstat'], capture_output=True, text=True, check=True)
    for line in ibstat_command_output.stdout.split('\n') :
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

    c1="mlx name"
    c2="node guid"
    c3="port"
    c4="state"
    c5="if name"
    c6="ipv4"
    c7="ipv6"
    print(f"{c1.ljust(10)} {c2.ljust(25)} {c3.ljust(5)} {c4.ljust(5)} {c5.ljust(10)} {c6.ljust(20)} {c7}")
    for entry in hca_info.keys() :
        id = hca_info[entry]['hca_id']
        lo = hca_info[entry]['last_octect']
        stat = hca_info[entry]['status']
        node_guid = hca_info[entry]['node_guid']
        port = hca_info[entry]['port']
        status = hca_info[entry]["status"]
        if_name = "N/A"
        ipv4 = "N/A"
        ipv6 = "N/A"

        # For multi-nic with RoCE/GDR, we match the mlx_name with if name by the last octet of the IPv6 address
        if lo in ip_address_info :
            if_name = ip_address_info[lo]['interface_name']
            ipv4 = ip_address_info[lo]['ipv4']
            ipv6 = ip_address_info[lo]['ipv6']
            if status == "UP" :
                hca_info[entry]["ipv4"] = ipv4
                ips_for_fping.append(ipv4.split('/')[0])
                nccl_list.append(f"{entry}")
                nixl_list.append(f"{if_name}")

        # For infiniband, we only check the status of the ibpX device.
        if id.count("ibp") and status == "UP" :
            disable_acs = False
            nccl_list.append(f"{entry}")

    # print(hca_info)
        print(f"{entry.ljust(10)} {node_guid.ljust(25)} {port.ljust(5)} {stat.ljust(5)} {if_name.ljust(10)} {ipv4.ljust(20)} {ipv6}")

    if not nixl_list and nccl_list :
        for entry in ip_address_info.keys() :
            if ip_address_info[entry]["interface_name"].count('eth') :
                nixl_list.append(ip_address_info[entry]["interface_name"])

create_multiple_routing_tables = False
for entry in ip_route_info.keys() :
    if len(ip_route_info[entry]) > 1 :
        create_multiple_routing_tables = True

i = 0
if create_multiple_routing_tables :
    rtdir = None
    for rtdir in [ "/etc/iproute2", "/usr/share/iproute2" ] :
        rt_tables_path = Path(f"{rtdir}/rt_tables")
        if rt_tables_path.is_file():
            break

    if rtdir :
        print("INFO: one or more interfaces have IPs on the same subnet, will create multiple routing tables")
        with open(f"{rt_tables_path}", 'r') as file:
            rt_tables_content = file.read().split('\n')

        for entry in ip_address_info :
            if ip_address_info[entry]["interface_name"] != default_interface and ip_address_info[entry]["interface_name"] != "lo" :
                table = f"table{i}"
                new_routing_table_entry_found = False
                for line in rt_tables_content :
                    if line.count(f" table{i} ") :
                        new_routing_table_entry_found = True
                        break
                if not new_routing_table_entry_found :
                    new_routing_table_entry = f"{100+i} {table} "
                    with open(f"{rt_tables_path}", 'a') as file:
                        file.write(new_routing_table_entry + '\n')
                    time.sleep(1)

                interface = ip_address_info[entry]["interface_name"]
                network = device_to_network[interface]
                ip = ip_address_info[entry]["ipv4"].split('/')[0]

                new_routing_table_populated = False
                try :
                    table_output = subprocess.run(['ip', 'route', 'list', 'table', table], capture_output=True, text=True, check=True)
                    for line in table_output.stdout.split('\n') :
                        if line.count(f"{network} dev {interface} scope link src {ip}") :
                            new_routing_table_populated = True
                            break
                except subprocess.CalledProcessError as e:
                    True

                if not new_routing_table_populated :
                    subprocess.run(['ip', 'route', 'add', network, 'dev', interface, 'src', ip, 'table', table], capture_output=True, text=True, check=True)
                    subprocess.run(['ip', 'rule', 'add', 'from', ip, 'lookup', table], capture_output=True, text=True, check=True)

                i=i+1
    else :
        print("WARNING: unable to find a directory for the file \"rt_tables\"")

env_file_contents=[]
env_file_name=f"{Path.home()}/llmdbench_env.sh"
env_file_contents.append("#!/usr/bin/env bash")
if nixl_list :
    print(f"INFO: Adding environment variables \"UCX_NET_DEVICES\" and \"NCCL_IB_HCA\" to {env_file_name}")
    print()
    first_device=nccl_list[0]
    first_octect=hca_info[nccl_list[0]]["ipv4"].split('.')[0]
    nccl_list = ','.join(nccl_list)
    nixl_list = ','.join(nixl_list)
    ips_for_fping = ' '.join(ips_for_fping)
    env_file_contents.append(f"export SMOKETEST_IPS=\"{ips_for_fping}\"")
    env_file_contents.append(f"export UCX_NET_DEVICES=\"{nixl_list}\"")
    env_file_contents.append(f"export NCCL_IB_HCA=\"={nccl_list}\"")
    env_file_contents.append(f"export NCCL_IB_GID_INDEX=$(show_gids | sed 's^\\t^,^g' | grep \"{first_device},\" | grep v2 | grep {first_octect} | cut -d ',' -f 3)")

lwswi = os.getenv("LWS_WORKER_INDEX", "0")
dpsi = os.getenv("DP_SIZE_LOCAL", "0")
sr = int(lwswi) * int(dpsi)
env_file_contents.append(f"export START_RANK=\"{sr}\"")

env_file_contents.append("if [[ -z $LWS_WORKER_INDEX ]]; then")
env_file_contents.append("  find /dev/shm -type f -delete")
env_file_contents.append("fi")

if disable_acs :
    env_file_contents.append("if [[ ! -z $UCX_NET_DEVICES && ! -z NCCL_IB_HCA && ! -f ~/acs_disabled ]]; then")
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
    env_file_contents.append("touch ~/acs_disabled")
    env_file_contents.append("fi")

env_file_contents.append("echo")
env_file_contents.append("echo \"Defined NCCL environment variables\"")
env_file_contents.append("env | grep -E \"^NCCL|^UCX|^CUDA|^OMP|^NPROC|^SMOKETEST\" | sort")
env_file_contents.append("echo")

env_file_contents='\n'.join(env_file_contents)
with open(env_file_name, "w") as file:
    file.write(env_file_contents)

bashrc_path = Path(f"{Path.home()}/.bashrc")
if bashrc_path.is_file():
    bashrc_updated = False
    with open(f"{Path.home()}/.bashrc", 'r') as file:
        bashrc_contents = file.read().split('\n')

    for line in bashrc_contents :
        if line.count("source ~/nccl_env.sh") :
            bashrc_updated = True
            break

    if not bashrc_updated :
        with open(f"{Path.home()}/.bashrc", 'a') as file:
            file.write("source ~/nccl_env.sh" + '\n')
