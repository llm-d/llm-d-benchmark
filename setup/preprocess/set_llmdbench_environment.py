#!/usr/bin/env python3

import subprocess
import ipaddress
import os
import json
import time
import shutil

from pathlib import Path
from optparse import OptionParser

ip_address_info={}
ip_route_info={}
device_to_network={}
hcadev_to_gid={}
gid_to_device={}
curr_if=''
multi_if_net=None
hca_info={}
nccl_list =[]
nixl_list =[]
is_infiniband = False

hcaids_down = []
hcaids_wrong_gid = []
hcaids_excluded = os.getenv('NCCL_EXCLUDE_IB_HCA','').split(',')

executables_path = "/usr/local/bin"

ips_for_fping = []
curr_hca=''

deps_present = {}
deps_present["ip"] = False
deps_present["ibstat"] = False
deps_present["show_gids"]= False
deps_present["gemini-arp-fix"] = False

nvshmem_remote_transport="ibgda"
nvshmem_ib_enable_ibgda="true"
nvshmem_enable_nic_pe_mapping=1
nvshmem_ib_addr_range = os.getenv('NVSHMEM_IB_ADDR_RANGE', None)

disable_acs = os.getenv("NCCL_ACS_DISABLE","0")

usage = '''usage: %prog [options] [command]
'''
_parser = OptionParser(usage)

_parser.add_option("-d" , "--debug", \
                    action="store_true", \
                    dest="debug", \
                    default=False, \
                    help="Display messages describing the output of discovery process on this pod")

_parser.add_option("-e" , "--envfile", \
                    dest="envfile", \
                    default="llmdbench_env.sh", \
                    help="name of the environment file generated (on user's home directory")

_parser.set_defaults()
(options, _args) = _parser.parse_args()

if os.getenv('FLEX_DEVICE','PF') == 'VF' :
    env_file_name=f"{Path.home()}/.senlib.json"
    if Path(env_file_name).is_file():
        print(f"INFO: Environment variable \"FLEX_DEVICE\" detected, will modify \"{env_file_name}\"")
        with open(env_file_name, "r", encoding="utf-8") as senlib_file:
            senlib_contents = json.load(senlib_file)
        senlib_contents['RISCV']['DOOM']['enable'] = True
        with open(env_file_name, 'w') as senlib_file:
            json.dump(senlib_contents, senlib_file, indent=4)

for dep in deps_present.keys() :
    try :
        result = subprocess.run(['which', dep], capture_output=True, text=True, check=True)
        deps_present[dep] = True
    except subprocess.CalledProcessError as e:
        if os.access(executables_path, os.W_OK):
            print(f"WARNING: Dependency \"{dep}\" not available on the image: {e.cmd} returned {e.returncode}. Trying to obtain externally...")
            tool_cfgmap_fn=f"/setup/preprocess/{dep}.sh"
            if Path(tool_cfgmap_fn).is_file() :
                tool_image_fn = f"{executables_path}/{dep}"
                shutil.copy2(tool_cfgmap_fn, tool_image_fn)
                os.chmod(tool_image_fn, 0o755)
    try :
        result = subprocess.run(['which', dep], capture_output=True, text=True, check=True)
        deps_present[dep] = True
    except subprocess.CalledProcessError as e:
        print(f"WARNING: Dependency \"{dep}\" neither available on the image nor on the config map: {e.cmd} returned {e.returncode}.")

if deps_present["ip"] :
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

    file_name=f"{Path.home()}/.ip_address_info.json"
    with open(file_name, 'w') as fh:
        json.dump(ip_address_info, fh)

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

    file_name=f"{Path.home()}/.ip_route_info.json"
    with open(file_name, 'w') as fh:
        json.dump(ip_route_info, fh)

    file_name=f"{Path.home()}/.device_to_network.json"
    with open(file_name, 'w') as fh:
        json.dump(ip_route_info, fh)

if deps_present["show_gids"] :
    file_name=f"{Path.home()}/.gid_to_device.json"
    if Path(file_name).is_file():
        with open(file_name, 'r') as fh:
            gid_to_device = json.load(fh)

    file_name=f"{Path.home()}/.hcadev_to_gid.json"
    if Path(file_name).is_file():
        with open(file_name, 'r') as fh:
            hcadev_to_gid = json.load(fh)

    if not gid_to_device and not hcadev_to_gid :
        show_gids_command_output = subprocess.run(['show_gids'], capture_output=True, text=True, check=True)
        for line in show_gids_command_output.stdout.split('\n')[2:] :
            if len(line.split('\t')) == 7 :
                hcadev, _, gidnum, gid, ipv4, _, netdev = line.split('\t')
                if gidnum not in gid_to_device :
                    gid_to_device[gidnum] = []

                if hcadev not in hcadev_to_gid :
                    hcadev_to_gid[hcadev] = []

                if hcadev not in gid_to_device[gidnum] :
                    gid_to_device[gidnum].append(hcadev)

                if gidnum not in hcadev_to_gid[hcadev] :
                    hcadev_to_gid[hcadev].append(gidnum)

        file_name=f"{Path.home()}/.gid_to_device.json"
        with open(file_name, 'w') as fh:
            json.dump(gid_to_device, fh)

        file_name=f"{Path.home()}/.hcadev_to_gid.json"
        with open(file_name, 'w') as fh:
            json.dump(hcadev_to_gid, fh)

s_gid_len = 0
s_gid = []
for gidnum in gid_to_device.keys() :
    if len(gid_to_device[gidnum]) >= s_gid_len :
        s_gid_len = len(gid_to_device[gidnum])

for gidnum in gid_to_device.keys() :
    if len(gid_to_device[gidnum]) == s_gid_len :
        if gidnum not in s_gid :
            s_gid.append(gidnum)

if options.debug :
    print(f"{'-' * 20} ip_info {'-' * 20}")
    print(json.dumps(ip_address_info, sort_keys=True, indent=4))
    print(f"{'-' * 20} ip_info {'-' * 20}")

    print(f"{'-' * 20} ip_route_info {'-' * 20}")
    print(json.dumps(ip_route_info, sort_keys=True, indent=4))
    print(f"{'-' * 20} ip_route_info {'-' * 20}")

    print(f"{'-' * 20} gid_to_device {'-' * 20}")
    print(json.dumps(gid_to_device, sort_keys=True, indent=4))
    print(f"{'-' * 20} gid_to_device {'-' * 20}")

    print(f"{'-' * 20} hcadev_to_gid {'-' * 20}")
    print(json.dumps(hcadev_to_gid, sort_keys=True, indent=4))
    print(f"{'-' * 20} hcadev_to_gid {'-' * 20}")

    print(f"{'-' * 20} device_to_network {'-' * 20}")
    print(json.dumps(device_to_network, sort_keys=True, indent=4))
    print(f"{'-' * 20} device_to_network {'-' * 20}")

    print(f"{'-' * 20} selected gids: {s_gid}  {'-' * 20}")

if deps_present["ibstat"] :
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

        hca_info[curr_hca]["gids"] = []
        if curr_hca in hcadev_to_gid :
            hca_info[curr_hca]["gids"] = hcadev_to_gid[curr_hca]

    file_name=f"{Path.home()}/.hca_info.json"
    with open(file_name, 'w') as fh:
        json.dump(hca_info, fh)

    c1="mlx name"
    c2="node guid"
    c3="port"
    c4="state"
    c5="if name"
    c6="ipv4"
    c7="ipv6"

    if options.debug :
        print(f"{c1.ljust(10)} {c2.ljust(25)} {c3.ljust(5)} {c4.ljust(5)} {c5.ljust(10)} {c6.ljust(20)} {c7}")

    for entry in hca_info.keys() :
        hcaid = hca_info[entry]['hca_id']
        lo = hca_info[entry]['last_octect']
        stat = hca_info[entry]['status']
        node_guid = hca_info[entry]['node_guid']
        port = hca_info[entry]['port']
        status = hca_info[entry]["status"]
        if_name = "N/A"
        ipv4 = "N/A"
        ipv6 = "N/A"

        if status == "DOWN" :
            if hcaid not in hcaids_down :
                hcaids_down.append(hcaid)

        # For multi-nic with RoCE/GDR, we match the mlx_name with if name by the last octet of the IPv6 address
        if lo in ip_address_info :
            if_name = ip_address_info[lo]['interface_name']
            ipv4 = ip_address_info[lo]['ipv4']
            ipv6 = ip_address_info[lo]['ipv6']
            if status == "UP" :
                if s_gid == hcadev_to_gid[hcaid] :
                    if hcaid not in hcaids_excluded :
                        hca_info[entry]["ipv4"] = ipv4
                        ips_for_fping.append(ipv4.split('/')[0])
                        nccl_list.append(f"{entry}")
                        nixl_list.append(f"{if_name}")
                else :
                    if hcaid not in hcaids_wrong_gid :
                        hcaids_wrong_gid.append(hcaid)

        # For infiniband, we only check the status of the ibpX device.
        if hcaid.count("ibp") :
            is_infiniband = True
            if status == "UP" :
                nccl_list.append(f"{entry}")

        if options.debug :
            print(f"{entry.ljust(10)} {node_guid.ljust(25)} {port.ljust(5)} {stat.ljust(5)} {if_name.ljust(10)} {ipv4.ljust(20)} {ipv6}")

    if not nixl_list and nccl_list :
        for entry in ip_address_info.keys() :
            if ip_address_info[entry]["interface_name"].count('eth') :
                nixl_list.append(ip_address_info[entry]["interface_name"])

create_multiple_routing_tables = False
for entry in ip_route_info.keys() :
    if len(ip_route_info[entry]) > 1 :
        nvshmem_ib_addr_range = entry
        multi_if_net = entry
        create_multiple_routing_tables = True

if not nvshmem_ib_addr_range and multi_if_net :
    nvshmem_ib_addr_range = entry

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
                    print(f"WARNING: Command \"{e.cmd}\" returned {e.returncode}.")

                if not new_routing_table_populated :
                    try :
                        subprocess.run(['ip', 'route', 'add', network, 'dev', interface, 'src', ip, 'table', table], capture_output=True, text=True, check=True)
                    except subprocess.CalledProcessError as e:
                        print(f"WARNING: Command \"{e.cmd}\" returned {e.returncode}.")

                    try :
                        subprocess.run(['ip', 'rule', 'add', 'from', ip, 'lookup', table], capture_output=True, text=True, check=True)
                    except subprocess.CalledProcessError as e:
                        print(f"WARNING: Command \"{e.cmd}\" returned {e.returncode}.")


                i=i+1
    if deps_present["gemini-arp-fix"] :
        try :
            subprocess.run(['gemini-arp-fix'], capture_output=True, text=True, check=True)
        except subprocess.CalledProcessError as e:
            print(f"WARNING: Command \"{e.cmd}\" returned {e.returncode}.")

    else :
        print("WARNING: unable to find a directory for the file \"rt_tables\"")

env_file_contents=[]
env_file_name=f"{Path.home()}/{options.envfile}"
env_file_contents.append("#!/usr/bin/env bash")

print(f"INFO: HCA IDs selected: {nccl_list}")
print(f"INFO: HCA IDs marked as down: {hcaids_down}")
print(f"INFO: HCA IDs with wrong gid: {hcaids_wrong_gid}")
print(f"INFO: HCA IDs excluded: {hcaids_excluded}")

if nixl_list :
    nccl_list.sort(key=len)
    nixl_list.sort(key=len)
    hcaids_down.sort(key=len)
    hcaids_excluded.sort(key=len)

    env_vars = [ 'UCX_NET_DEVICES', 'NCCL_IB_HCA' ]
    nvshmem_debug = os.getenv("NVSHMEM_DEBUG", "none")
    if nvshmem_debug != "none" :
        env_vars.append('NVSHMEM_HCA_LIST')

    print(f"INFO: Adding environment variables {env_vars} to {env_file_name}")
    print()
    first_device=nccl_list[0]
    first_octect=hca_info[nccl_list[0]]["ipv4"].split('.')[0]
    nccl_list = ','.join(nccl_list)
    nixl_list = ','.join(nixl_list)
    ips_for_fping = ' '.join(ips_for_fping)
    env_file_contents.append(f"export SMOKETEST_IPS=\"{ips_for_fping}\"")
    env_file_contents.append(f"export UCX_NET_DEVICES=\"{nixl_list}\"")
    env_file_contents.append(f"export NCCL_IB_HCA=\"={nccl_list}\"")
    env_file_contents.append(f"export NCCL_SOCKET_IFNAME=\"{default_interface}\"")
    env_file_contents.append(f"export NCCL_IB_GID_INDEX={s_gid[0]}")
    if 'NVSHMEM_HCA_LIST' in env_vars != "none" :
        env_file_contents.append(f"export GLOO_SOCKET_IFNAME=\"{default_interface}\"")
        env_file_contents.append(f"export NVSHMEM_DEBUG=\"{nvshmem_debug}\"")
        env_file_contents.append(f"export NVSHMEM_REMOTE_TRANSPORT=\"{nvshmem_remote_transport}\"")
        env_file_contents.append(f"export NVSHMEM_IB_ENABLE_IBGDA=\"{nvshmem_ib_enable_ibgda}\"")
        env_file_contents.append(f"export NVSHMEM_BOOTSTRAP_UID_SOCK_IFNAME=\"{default_interface}\"")
        env_file_contents.append(f"export NVSHMEM_IB_GID_INDEX=\"{s_gid[0]}\"")
        env_file_contents.append(f"export NVSHMEM_ENABLE_NIC_PE_MAPPING=\"{nvshmem_enable_nic_pe_mapping}\"")
        env_file_contents.append(f"export NVSHMEM_HCA_LIST=\"{nccl_list.replace(',',':1,')}:1\"")
        env_file_contents.append(f"export NVSHMEM_IB_ADDR_RANGE=\"{nvshmem_ib_addr_range}\"")
        if is_infiniband :
            env_file_contents.append(f"export NVSHMEM_IB_ENABLE_IBGDA=\"{is_infiniband}\"")

lwswi = int(os.getenv("LWS_WORKER_INDEX", "0"))
dpsi = int(os.getenv("DP_SIZE_LOCAL", "0"))
sr = lwswi * dpsi
env_file_contents.append(f"export START_RANK=\"{sr}\"")

env_file_contents.append("if [[ -z $LWS_WORKER_INDEX ]]; then")
env_file_contents.append("  find /dev/shm -type f -delete")
env_file_contents.append("fi")

if disable_acs == "1" :
    env_file_contents.append("if [[ ! -z $UCX_NET_DEVICES && ! -z NCCL_IB_HCA && ! -f ~/acs_disabled ]]; then")
    env_file_contents.append(" acs_disable_failure=0")
    env_file_contents.append(" for BDF in $(lspci -d \"*:*:*\" | awk '{print $1}'); do")
    env_file_contents.append("    setpci -v -s ${BDF} ECAP_ACS+0x6.w > /dev/null 2>&1")
    env_file_contents.append("    if [ $? -ne 0 ]; then")
    env_file_contents.append("      #echo \"ACS is already disabled for PCI device \\\"${BDF}\\\"\"")
    env_file_contents.append("      continue")
    env_file_contents.append("    fi")
    env_file_contents.append("    setpci -v -s ${BDF} ECAP_ACS+0x6.w=0000 > /dev/null 2>&1")
    env_file_contents.append("    if [ $? -eq 0 ]; then")
    env_file_contents.append("      echo \"ACS disabled for PCI device \\\"${BDF}\\\"\"")
    env_file_contents.append("    else")
    env_file_contents.append("      acs_disable_failure=1")
    env_file_contents.append("      echo \"WARNING: Failed to disable ACS for PCI device \\\"${BDF}\\\"\"")
    env_file_contents.append("    fi")
    env_file_contents.append("  done")
    env_file_contents.append(" if [[ $acs_disable_failure -eq 0 ]]; then")
    env_file_contents.append("      echo \"ACS is disabled for all relevant PCI devices\"")
    env_file_contents.append(" fi")
    env_file_contents.append(" touch ~/acs_disabled")
    env_file_contents.append("fi")

env_file_contents.append("echo")

pod_name = os.uname()[1]
if pod_name.count("decode") :
    pod_index=eval(pod_name.split('decode-')[-1].replace('-','+'))
if pod_name.count("prefill") :
    pod_index=eval(pod_name.split('prefill-')[-1].replace('-','+'))

for key in dict(os.environ).keys():
    if "VLLM_" in key:
        value = os.environ.get(key)
        if value.count(',,') :
            newvalue = value.split(',,')[pod_index]
            print(f"INFO: Variable \"{key}\" with value \"{value}\" will be re-exported with \"{newvalue}\" ({pod_index})")
            env_file_contents.append(f"export {key}={newvalue}")

env_file_contents.append("echo \"Defined NCCL environment variables\"")
env_file_contents.append("env | grep -E \"^NCCL|^UCX|^CUDA|^OMP|^NPROC|^SMOKETEST|^NVSHMEM|START|WORLD_SIZE|RANK|^MASTER\" | sort")
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
        if line.count(f"source ~/{options.envfile}") :
            bashrc_updated = True
            break

    if not bashrc_updated :
        with open(f"{Path.home()}/.bashrc", 'a') as file:
            file.write(f"source ~/{options.envfile}" + '\n')
