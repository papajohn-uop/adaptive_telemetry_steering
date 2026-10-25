#!/bin/bash
# ==============================================================================
# setup_phase2_topology.sh
#
# Configures the 3-Namespace Multi-Backhaul Emulation Topology for Phase 2 & 3:
#   ns_client (10.0.1.1, 10.0.2.1)
#      │           │
#      │ Link A    │ Link B (Impaired)
#      ▼           ▼
#   ns_router (10.0.1.2, 10.0.2.2, 10.0.3.1)
#      │
#      │ Sink Link (Pristine)
#      ▼
#   ns_server (10.0.3.2 - Invariant Destination IP)
#
# Usage:
#   sudo ./setup_phase2_topology.sh [setup|teardown|status|test|set-impairment]
# ==============================================================================

set -e

# Namespace definitions
NS_CLIENT="ns_client"
NS_ROUTER="ns_router"
NS_SERVER="ns_server"

# IP Address Configuration
IP_CLIENT_A="10.0.1.1"
IP_ROUTER_A="10.0.1.2"
SUBNET_A="10.0.1.0/24"

IP_CLIENT_B="10.0.2.1"
IP_ROUTER_B="10.0.2.2"
SUBNET_B="10.0.2.0/24"

IP_ROUTER_SINK="10.0.3.1"
IP_SERVER="10.0.3.2"
SUBNET_SINK="10.0.3.0/24"

# Routing Table IDs for Policy Routing
TABLE_LINK_A=101
TABLE_LINK_B=102

# Check Root Privileges
check_root() {
    if [ "$EUID" -ne 0 ]; then
        echo "[ERROR] This script must be run as root (sudo)."
        exit 1
    fi
}

# Teardown any existing namespaces and virtual links
teardown() {
    echo "[CLEANUP] Tearing down existing namespaces and virtual links..."
    
    # Delete namespaces (this automatically destroys attached veth interfaces)
    ip netns del "$NS_CLIENT" 2>/dev/null || true
    ip netns del "$NS_ROUTER" 2>/dev/null || true
    ip netns del "$NS_SERVER" 2>/dev/null || true
    
    # Also clean legacy namespaces if lingering
    ip netns del "left-ns" 2>/dev/null || true
    ip netns del "right-ns" 2>/dev/null || true
    
    echo "[SUCCESS] Teardown complete."
}

# Apply Traffic Control on a specific interface
apply_tc() {
    local ns=$1
    local dev=$2
    local rate=$3
    local delay=$4
    local jitter=$5
    local loss=$6

    # Clear existing qdiscs
    ip netns exec "$ns" tc qdisc del dev "$dev" root 2>/dev/null || true

    local netem_args=""
    if [ -n "$delay" ] && [ "$delay" != "0ms" ]; then
        netem_args="delay $delay"
        if [ -n "$jitter" ] && [ "$jitter" != "0ms" ]; then
            netem_args="$netem_args $jitter distribution normal"
        fi
    fi
    if [ -n "$loss" ] && [ "$loss" != "0%" ] && [ "$loss" != "0.0%" ]; then
        netem_args="$netem_args loss $loss"
    fi

    if [ -n "$netem_args" ]; then
        # HTB class shaping nested under netem root
        ip netns exec "$ns" tc qdisc replace dev "$dev" root handle 1: netem $netem_args
        ip netns exec "$ns" tc qdisc add dev "$dev" parent 1: handle 2: htb default 10
        ip netns exec "$ns" tc class add dev "$dev" parent 2: classid 2:10 htb rate "$rate" ceil "$rate" quantum 1500
        ip netns exec "$ns" tc qdisc add dev "$dev" parent 2:10 handle 100: fq_codel
    else
        # Pure HTB shaping without channel impairments
        ip netns exec "$ns" tc qdisc replace dev "$dev" root handle 1: htb default 10
        ip netns exec "$ns" tc class add dev "$dev" parent 1: classid 1:10 htb rate "$rate" ceil "$rate" quantum 1500
        ip netns exec "$ns" tc qdisc add dev "$dev" parent 1:10 handle 100: fq_codel
    fi
}

# Setup the full 3-namespace topology
setup() {
    teardown

    echo "[SETUP] Provisioning 3-Namespace Multi-Backhaul Architecture..."

    # 1. Create Namespaces
    ip netns add "$NS_CLIENT"
    ip netns add "$NS_ROUTER"
    ip netns add "$NS_SERVER"

    # 2. Bring up Loopback interfaces
    ip -n "$NS_CLIENT" link set lo up
    ip -n "$NS_ROUTER" link set lo up
    ip -n "$NS_SERVER" link set lo up

    # 3. Create Virtual Ethernet Pairs
    # Link A (Client <-> Router)
    ip link add veth_client_a type veth peer name veth_router_a
    ip link set veth_client_a netns "$NS_CLIENT"
    ip link set veth_router_a netns "$NS_ROUTER"

    # Link B (Client <-> Router)
    ip link add veth_client_b type veth peer name veth_router_b
    ip link set veth_client_b netns "$NS_CLIENT"
    ip link set veth_router_b netns "$NS_ROUTER"

    # Sink Link (Router <-> Server)
    ip link add veth_r_sink type veth peer name veth_server
    ip link set veth_r_sink netns "$NS_ROUTER"
    ip link set veth_server netns "$NS_SERVER"

    # 4. Assign IP Addresses
    # Link A Subnet (10.0.1.0/24)
    ip -n "$NS_CLIENT" addr add "${IP_CLIENT_A}/24" dev veth_client_a
    ip -n "$NS_ROUTER" addr add "${IP_ROUTER_A}/24" dev veth_router_a

    # Link B Subnet (10.0.2.0/24)
    ip -n "$NS_CLIENT" addr add "${IP_CLIENT_B}/24" dev veth_client_b
    ip -n "$NS_ROUTER" addr add "${IP_ROUTER_B}/24" dev veth_router_b

    # Sink Subnet (10.0.3.0/24)
    ip -n "$NS_ROUTER" addr add "${IP_ROUTER_SINK}/24" dev veth_r_sink
    ip -n "$NS_SERVER" addr add "${IP_SERVER}/24" dev veth_server

    # 5. Bring Up Interfaces
    ip -n "$NS_CLIENT" link set veth_client_a up
    ip -n "$NS_CLIENT" link set veth_client_b up

    ip -n "$NS_ROUTER" link set veth_router_a up
    ip -n "$NS_ROUTER" link set veth_router_b up
    ip -n "$NS_ROUTER" link set veth_r_sink up

    ip -n "$NS_SERVER" link set veth_server up

    # 6. Enable IP Forwarding on Router Gateway
    echo "[SETUP] Enabling IP Forwarding in $NS_ROUTER..."
    ip netns exec "$NS_ROUTER" sysctl -w net.ipv4.ip_forward=1 >/dev/null

    # 7. Disable TCP Metrics Caching Across All Namespaces (Kernel Hardening)
    echo "[SETUP] Hardening kernel route caches (tcp_no_metrics_save=1)..."
    ip netns exec "$NS_CLIENT" sysctl -w net.ipv4.tcp_no_metrics_save=1 >/dev/null
    ip netns exec "$NS_ROUTER" sysctl -w net.ipv4.tcp_no_metrics_save=1 >/dev/null
    ip netns exec "$NS_SERVER" sysctl -w net.ipv4.tcp_no_metrics_save=1 >/dev/null

    # 8. Configure Routing in ns_server
    # Server points default route back to Router sink interface
    echo "[SETUP] Setting up return routing in $NS_SERVER..."
    ip -n "$NS_SERVER" route add default via "$IP_ROUTER_SINK" dev veth_server

    # 9. Configure Policy Routing in ns_client (Dual-Path Isolation)
    echo "[SETUP] Configuring Linux Policy Routing in $NS_CLIENT..."
    # Table 101: Forces egress via Link A for probes bound to 10.0.1.1
    ip netns exec "$NS_CLIENT" ip rule add from "$IP_CLIENT_A" lookup "$TABLE_LINK_A"
    ip netns exec "$NS_CLIENT" ip route add default via "$IP_ROUTER_A" dev veth_client_a table "$TABLE_LINK_A"

    # Table 102: Forces egress via Link B for probes bound to 10.0.2.1
    ip netns exec "$NS_CLIENT" ip rule add from "$IP_CLIENT_B" lookup "$TABLE_LINK_B"
    ip netns exec "$NS_CLIENT" ip route add default via "$IP_ROUTER_B" dev veth_client_b table "$TABLE_LINK_B"

    # Main Routing Table: Active default route initially points to Link B (Cellular)
    # The dynamic steering controller can atomically replace this:
    #   'ip route replace default via 10.0.1.2 dev veth_client_a'
    ip -n "$NS_CLIENT" route add default via "$IP_ROUTER_B" dev veth_client_b

    # Flush route cache
    ip netns exec "$NS_CLIENT" ip route flush cache 2>/dev/null || true
    ip netns exec "$NS_ROUTER" ip route flush cache 2>/dev/null || true
    ip netns exec "$NS_SERVER" ip route flush cache 2>/dev/null || true

    # 10. Configure Traffic Control (tc) Profiles
    echo "[SETUP] Applying Traffic Control (tc) profiles on $NS_ROUTER and $NS_CLIENT..."
    
    # Link A (Fiber Profile: 100 Mbit/s, 2ms one-way delay = ~4ms RTT, 0ms jitter, 0% loss)
    apply_tc "$NS_ROUTER" veth_router_a "100mbit" "2ms" "0ms" "0%"
    apply_tc "$NS_CLIENT" veth_client_a "100mbit" "2ms" "0ms" "0%"

    # Link B (Cellular Profile: 50 Mbit/s, 25ms one-way delay = ~50ms RTT, 0ms jitter initially, 0% loss initially)
    apply_tc "$NS_ROUTER" veth_router_b "50mbit" "25ms" "0ms" "0%"
    apply_tc "$NS_CLIENT" veth_client_b "50mbit" "25ms" "0ms" "0%"

    echo "[SUCCESS] Topology successfully initialized and configured!"
}

# Display Status
status() {
    echo "==================== TOPOLOGY STATUS ===================="
    echo "Namespaces:"
    ip netns list
    echo ""
    echo "--- ns_client Interfaces & Routes ---"
    ip -n "$NS_CLIENT" -br addr
    echo "Main Table Default Route:"
    ip -n "$NS_CLIENT" route show default
    echo "Policy Routing Rules:"
    ip netns exec "$NS_CLIENT" ip rule show
    echo ""
    echo "--- ns_router Interfaces & Forwarding ---"
    ip -n "$NS_ROUTER" -br addr
    echo "IP Forward: $(ip netns exec "$NS_ROUTER" sysctl -n net.ipv4.ip_forward)"
    echo ""
    echo "--- ns_server Interfaces & Routes ---"
    ip -n "$NS_SERVER" -br addr
    echo "IP Route Show:"
    ip -n "$NS_SERVER" route show
    echo ""
    echo "--- Traffic Control (tc) Configuration ---"
    echo "veth_router_a (Link A):"
    ip netns exec "$NS_ROUTER" tc qdisc show dev veth_router_a
    echo "veth_router_b (Link B):"
    ip netns exec "$NS_ROUTER" tc qdisc show dev veth_router_b
    echo "========================================================="
}

# Test Connectivity
test_connectivity() {
    echo "[TEST] Testing Dual-Path Connectivity to ns_server ($IP_SERVER)..."
    
    echo -n "1. Link A Probe (Sourced from $IP_CLIENT_A -> $IP_SERVER): "
    output_a=$(ip netns exec "$NS_CLIENT" ping -c 3 -I "$IP_CLIENT_A" "$IP_SERVER" 2>&1)
    if [ $? -eq 0 ]; then
        rtt_a=$(echo "$output_a" | tail -1 | awk -F '/' '{print $5}')
        echo "[OK] SUCCESS (Avg RTT = ${rtt_a} ms - Expected ~4 ms)"
    else
        echo "[FAIL] FAILED"
        echo "$output_a"
    fi

    echo -n "2. Link B Probe (Sourced from $IP_CLIENT_B -> $IP_SERVER): "
    output_b=$(ip netns exec "$NS_CLIENT" ping -c 3 -I "$IP_CLIENT_B" "$IP_SERVER" 2>&1)
    if [ $? -eq 0 ]; then
        rtt_b=$(echo "$output_b" | tail -1 | awk -F '/' '{print $5}')
        echo "[OK] SUCCESS (Avg RTT = ${rtt_b} ms - Expected ~50 ms)"
    else
        echo "[FAIL] FAILED"
        echo "$output_b"
    fi

    echo -n "3. Default Route Egress ($IP_SERVER): "
    output_def=$(ip netns exec "$NS_CLIENT" ping -c 3 "$IP_SERVER" 2>&1)
    if [ $? -eq 0 ]; then
        rtt_def=$(echo "$output_def" | tail -1 | awk -F '/' '{print $5}')
        echo "[OK] SUCCESS (Avg RTT = ${rtt_def} ms)"
    else
        echo "[FAIL] FAILED"
        echo "$output_def"
    fi
}

# Dynamically alter link impairments
set_impairment() {
    local link=$1
    local delay=${2:-"25ms"}
    local jitter=${3:-"0ms"}
    local loss=${4:-"0%"}
    local rate=${5:-"50mbit"}

    case "$link" in
        A|a)
            echo "[MODIFY] Link A: Rate=$rate, Delay=$delay, Jitter=$jitter, Loss=$loss"
            apply_tc "$NS_ROUTER" veth_router_a "$rate" "$delay" "$jitter" "$loss"
            apply_tc "$NS_CLIENT" veth_client_a "$rate" "$delay" "$jitter" "$loss"
            ;;
        B|b)
            echo "[MODIFY] Link B: Rate=$rate, Delay=$delay, Jitter=$jitter, Loss=$loss"
            apply_tc "$NS_ROUTER" veth_router_b "$rate" "$delay" "$jitter" "$loss"
            apply_tc "$NS_CLIENT" veth_client_b "$rate" "$delay" "$jitter" "$loss"
            ;;
        *)
            echo "[ERROR] Invalid link '$link'. Specify 'A' or 'B'."
            exit 1
            ;;
    esac
}

# ==================== MAIN DISPATCHER ====================
check_root

ACTION=${1:-"setup"}

case "$ACTION" in
    setup)
        setup
        echo ""
        test_connectivity
        ;;
    teardown)
        teardown
        ;;
    status)
        status
        ;;
    test)
        test_connectivity
        ;;
    set-impairment)
        shift
        set_impairment "$@"
        ;;
    *)
        echo "Usage: sudo $0 {setup|teardown|status|test|set-impairment <link> <delay> [jitter] [loss] [rate]}"
        exit 1
        ;;
esac
