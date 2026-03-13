#!/bin/bash
# "Sledgehammer" Fix v3: Robust MAC detection

# 1. Gather all local IPs and MACs for net1-* devices
declare -A ip_map
declare -A mac_map

# Get list of devices, stripping the @suffix
devs=$(ip -o link show | awk -F': ' '{print $2}' | cut -d@ -f1 | grep '^net1-')

echo "Gathering local details..."
for dev in $devs; do
    # Extract IP (assuming one IPv4 per interface)
    ip=$(ip -o -4 addr show dev $dev | awk '{print $4}' | cut -d/ -f1)

    # FIX: Robust MAC extraction. Look for "link/ether", print the next field.
    mac=$(ip -o link show dev $dev | awk '{for(i=1;i<=NF;i++) if($i=="link/ether") print $(i+1)}')

    if [[ -n "$ip" && -n "$mac" ]]; then
        ip_map[$dev]=$ip
        mac_map[$dev]=$mac
        echo "  Found $dev: IP=$ip MAC=$mac"
    else
        echo "  Warning: Could not parse details for $dev"
    fi
done

echo "------------------------------------------------"
echo "Cross-populating ARP tables..."

# 2. Loop through every device (Source)
for src_dev in "${!ip_map[@]}"; do
    # Loop through every other device (Destination)
    for dst_dev in "${!ip_map[@]}"; do
        if [[ "$src_dev" != "$dst_dev" ]]; then
            dst_ip=${ip_map[$dst_dev]}
            dst_mac=${mac_map[$dst_dev]}

            # 3. Add the static neighbor entry
            # "nud permanent" stops the kernel from asking the switch
            ip neigh replace $dst_ip lladdr $dst_mac dev $src_dev nud permanent
        fi
    done
done

echo "Done. Please check 'ip neigh show' to verify correct MACs."
