#!/bin/bash
# lftp-migration-test.sh
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Ensure background workers are cleanly terminated on exit or interrupt
cleanup_all() {
    if [ -n "$CWND_LOGGER_PID" ]; then
        kill $CWND_LOGGER_PID 2>/dev/null || true
        wait $CWND_LOGGER_PID 2>/dev/null || true
    fi
    if [ -n "$LFTP_PID" ]; then
        kill $LFTP_PID 2>/dev/null || true
    fi
    if [ -n "$FTP_SERVER_PID" ]; then
        kill $FTP_SERVER_PID 2>/dev/null || true
        pkill -P $FTP_SERVER_PID 2>/dev/null || true
    fi
    if [ -n "$FILENAME" ]; then
        rm -f "$FILENAME" "migrated_$FILENAME" "baseline_$FILENAME" 2>/dev/null || true
    fi
}
trap cleanup_all EXIT INT TERM

# ==================== PARAMETERS & DEFAULTS ====================
# Flexible Syntax Detection:
#   1. Custom file only          -> sudo ./lftp-migration-test.sh my_experiment
#   2. Full parameters           -> sudo ./lftp-migration-test.sh 30mbit 20ms 5ms 1% my_experiment [output_dir] [filesize_mb]

if [[ -n "$1" && ! "$1" =~ (mbit|kbit|bps|gbit|[0-9]+$) ]]; then
    # Auto-detect: First argument is a name, not a network speed!
    BASE_OUT_NAME=$1
    RATE="50mbit"
    LATENCY=""
    JITTER=""
    LOSS=""
    if [[ "$2" =~ ^[0-9]+(MB|mb|m|M)?$ ]]; then
        FILE_SIZE_MB=$2
        OUTPUT_DIR=${3:-${RESULTS_DIR:-${OUTPUT_DIR:-"./results/raw"}}}
        CWND_MODE=${4:-${CWND_MODE:-"cubic"}}
    else
        OUTPUT_DIR=${2:-${RESULTS_DIR:-${OUTPUT_DIR:-"./results/raw"}}}
        FILE_SIZE_MB=${3:-${FILESIZE:-100}}
        CWND_MODE=${4:-${CWND_MODE:-"cubic"}}
    fi
else
    # Standard positional assignment
    RATE=${1:-"50mbit"}
    LATENCY=${2:-""}
    JITTER=${3:-""}
    LOSS=${4:-""}
    BASE_OUT_NAME=${5:-"migration_results"}
    if [[ "$6" =~ ^[0-9]+(MB|mb|m|M)?$ ]]; then
        FILE_SIZE_MB=$6
        OUTPUT_DIR=${7:-${RESULTS_DIR:-${OUTPUT_DIR:-"./results/raw"}}}
        CWND_MODE=${8:-${CWND_MODE:-"cubic"}}
    else
        OUTPUT_DIR=${6:-${RESULTS_DIR:-${OUTPUT_DIR:-"./results/raw"}}}
        FILE_SIZE_MB=${7:-${FILESIZE:-100}}
        CWND_MODE=${8:-${CWND_MODE:-"cubic"}}
    fi
fi

# Clean and normalize FILE_SIZE_MB to pure integer
FILE_SIZE_MB="${FILE_SIZE_MB//[^0-9]/}"
FILE_SIZE_MB=${FILE_SIZE_MB:-100}

# Normalize and validate CWND_MODE (reno or cubic, default cubic)
CWND_MODE=$(echo "${CWND_MODE:-cubic}" | tr '[:upper:]' '[:lower:]')
if [[ "$CWND_MODE" != "reno" && "$CWND_MODE" != "cubic" ]]; then
    echo "[WARNING] Warning: Unsupported cwnd mode '$CWND_MODE'. Falling back to 'cubic'."
    CWND_MODE="cubic"
fi

# ==================== CONFIGURATION ====================
NS_LEFT="left-ns"
NS_RIGHT="right-ns"
PORT="2121" 
FILENAME="testfile_${FILE_SIZE_MB}MB.bin"

# NEW REALISTIC IP MAPPINGS (Two entirely separate subnets)
IP_LEFT_1="10.0.1.1"
IP_RIGHT_1="10.0.1.2"  # Target Server IP for Path 1

IP_LEFT_2="10.0.2.1"
IP_RIGHT_2="10.0.2.2"  # Target Server IP for Path 2

# OUTPUT DATA CONFIGURATION
OUTPUT_DIR="${OUTPUT_DIR:-./results/raw}"

# Incorporate filesize in result filenames if not already present
if [[ "$BASE_OUT_NAME" =~ _[0-9]+(MB|mb|M|m)$ ]]; then
    RESULT_PREFIX="${BASE_OUT_NAME}"
else
    RESULT_PREFIX="${BASE_OUT_NAME}_${FILE_SIZE_MB}MB"
fi

CSV_FILE="${OUTPUT_DIR}/${RESULT_PREFIX}.csv"
CWND_FILE="${OUTPUT_DIR}/real_kernel_cwnd_${RESULT_PREFIX}.csv"

mkdir -p "$OUTPUT_DIR"

# ==================== PRE-FLIGHT CHECKS ====================
if [ "$EUID" -ne 0 ]; then
    echo " This script must be run as root (sudo)."
    exit 1
fi

echo "  Configuring Link Parameters:"
echo "   • Throttling Rate : $RATE"
echo "   • Network Latency : ${LATENCY:-0ms}"
echo "   • Latency Jitter  : ${JITTER:-0ms}"
echo "   • Packet Loss     : ${LOSS:-0%}"
echo "   • File Size       : ${FILE_SIZE_MB}MB"
echo "   • CWND Mode (CCA) : ${CWND_MODE}"
echo "   • Output File     : ${CSV_FILE}"
echo "   • CWND Trace File : ${CWND_FILE}"
echo ""

if ! command -v lftp &> /dev/null; then
    echo "Installing lftp..."
    apt-get update && apt-get install -y lftp
fi

if ! python3 -m pyftpdlib --help &> /dev/null; then
    echo "pyftpdlib not found. Installing via pip3..."
    apt-get update && apt-get install -y python3-pip
    pip3 install pyftpdlib
fi

EXPECTED_BYTES=$((FILE_SIZE_MB * 1024 * 1024))
if [ ! -f "$FILENAME" ] || [ $(stat -c%s "$FILENAME" 2>/dev/null || echo 0) -ne "$EXPECTED_BYTES" ]; then
    if [ "$FILE_SIZE_MB" -eq 100 ] && [ -f "testfile.bin" ] && [ $(stat -c%s "testfile.bin" 2>/dev/null || echo 0) -eq "$EXPECTED_BYTES" ]; then
        cp -f "testfile.bin" "$FILENAME" 2>/dev/null || ln -sf "testfile.bin" "$FILENAME"
    else
        echo " Creating a ${FILE_SIZE_MB}MB dummy file ($FILENAME) for testing..."
        dd if=/dev/urandom of="$FILENAME" bs=1M count=$FILE_SIZE_MB 2>/dev/null
    fi
fi

FILE_SIZE_BYTES=$(stat -c%s "$FILENAME")
TARGET_HALF_BYTES=$((FILE_SIZE_BYTES / 2))

# ==================== ENV CLEANUP & SETUP ====================
echo " Cleaning old network namespaces and active FTP servers..."
pkill -f "pyftpdlib -p $PORT" || true
ip netns del $NS_LEFT 2>/dev/null || true
ip netns del $NS_RIGHT 2>/dev/null || true
rm -f "migrated_$FILENAME" "baseline_$FILENAME"

echo " Creating isolated namespaces..."
ip netns add $NS_LEFT
ip netns add $NS_RIGHT
ip -n $NS_LEFT link set lo up
ip -n $NS_RIGHT link set lo up

echo " Provisioning dual veth paths..."
ip link add veth-left1 type veth peer name veth-right1
ip link add veth-left2 type veth peer name veth-right2

ip link set veth-left1 netns $NS_LEFT
ip link set veth-right1 netns $NS_RIGHT
ip link set veth-left2 netns $NS_LEFT
ip link set veth-right2 netns $NS_RIGHT

# Assigning completely distinct subnets to avoid layout overlapping
ip -n $NS_LEFT addr add ${IP_LEFT_1}/24 dev veth-left1
ip -n $NS_LEFT addr add ${IP_LEFT_2}/24 dev veth-left2
ip -n $NS_RIGHT addr add ${IP_RIGHT_1}/24 dev veth-right1
ip -n $NS_RIGHT addr add ${IP_RIGHT_2}/24 dev veth-right2

# ==================== CONGESTION CONTROL ALGORITHM ====================
echo " Setting TCP congestion control mode to '$CWND_MODE' on namespaces and veth routes..."
ip netns exec $NS_LEFT sysctl -w net.ipv4.tcp_congestion_control=$CWND_MODE > /dev/null 2>&1 || true
ip netns exec $NS_RIGHT sysctl -w net.ipv4.tcp_congestion_control=$CWND_MODE > /dev/null 2>&1 || true

# Apply congestion control algorithm directly to the subnet routes on each veth pair
ip -n $NS_LEFT route change 10.0.1.0/24 dev veth-left1 congctl $CWND_MODE 2>/dev/null || \
  ip -n $NS_LEFT route replace 10.0.1.0/24 dev veth-left1 congctl $CWND_MODE 2>/dev/null || true
ip -n $NS_LEFT route change 10.0.2.0/24 dev veth-left2 congctl $CWND_MODE 2>/dev/null || \
  ip -n $NS_LEFT route replace 10.0.2.0/24 dev veth-left2 congctl $CWND_MODE 2>/dev/null || true
ip -n $NS_RIGHT route change 10.0.1.0/24 dev veth-right1 congctl $CWND_MODE 2>/dev/null || \
  ip -n $NS_RIGHT route replace 10.0.1.0/24 dev veth-right1 congctl $CWND_MODE 2>/dev/null || true
ip -n $NS_RIGHT route change 10.0.2.0/24 dev veth-right2 congctl $CWND_MODE 2>/dev/null || \
  ip -n $NS_RIGHT route replace 10.0.2.0/24 dev veth-right2 congctl $CWND_MODE 2>/dev/null || true

# ==================== ENHANCED CACHE FLUSHING ====================
echo " Erasing kernel TCP metrics caching for clean iterations..."
# 1. Disable historical TCP saving inside the test environments
ip netns exec $NS_LEFT sysctl -w net.ipv4.tcp_no_metrics_save=1 > /dev/null 2>&1 || true
ip netns exec $NS_RIGHT sysctl -w net.ipv4.tcp_no_metrics_save=1 > /dev/null 2>&1 || true

# 2. Flush routing tables completely
ip netns exec $NS_LEFT ip route flush cache || true
ip netns exec $NS_RIGHT ip route flush cache || true


# ==================== BITRATE & EMULATION LIMITS ====================
echo " Applying Traffic Control (tc) settings..."

limit_bandwidth() {
    local ns=$1 dev=$2
    
    echo "Configuring $ns:$dev..."
    
    # Reset root qdisc safely
    ip netns exec $ns tc qdisc del dev $dev root 2>/dev/null || true
    
    # Construct the netem argument string dynamically depending on input parameters
    local netem_args=""
    if [ -n "$LATENCY" ]; then
        netem_args="delay $LATENCY"
        if [ -n "$JITTER" ]; then
            netem_args="$netem_args $JITTER"
        fi
    fi
    if [ -n "$LOSS" ]; then
        netem_args="$netem_args loss $LOSS"
    fi

    if [ -n "$netem_args" ]; then
        # If any netem rule applies, nest HTB inside netem root
        # echo "   [CMD] ip netns exec $ns tc qdisc replace dev $dev root handle 1: netem $netem_args"
        ip netns exec $ns tc qdisc replace dev $dev root handle 1: netem $netem_args
        
        # echo "   [CMD] ip netns exec $ns tc qdisc add dev $dev parent 1: handle 2: htb default 10"
        ip netns exec $ns tc qdisc add dev $dev parent 1: handle 2: htb default 10
        
        # 'quantum 1500' added to avoid kernel scheduling warnings
        # echo "   [CMD] ip netns exec $ns tc class add dev $dev parent 2: classid 2:10 htb rate $RATE ceil $RATE quantum 1500"
        ip netns exec $ns tc class add dev $dev parent 2: classid 2:10 htb rate $RATE ceil $RATE quantum 1500
        
        # echo "   [CMD] ip netns exec $ns tc qdisc add dev $dev parent 2:10 handle 100: fq_codel"
        ip netns exec $ns tc qdisc add dev $dev parent 2:10 handle 100: fq_codel
    else
        # Standard pure HTB configuration if no impairments are defined
        # echo "   [CMD] ip netns exec $ns tc qdisc replace dev $dev root handle 1: htb default 10"
        ip netns exec $ns tc qdisc replace dev $dev root handle 1: htb default 10
        
        # 'quantum 1500' added here as well
        # echo "   [CMD] ip netns exec $ns tc class add dev $dev parent 1: classid 1:10 htb rate $RATE ceil $RATE quantum 1500"
        ip netns exec $ns tc class add dev $dev parent 1: classid 1:10 htb rate $RATE ceil $RATE quantum 1500
        
        # echo "   [CMD] ip netns exec $ns tc qdisc add dev $dev parent 1:10 handle 100: fq_codel"
        ip netns exec $ns tc qdisc add dev $dev parent 1:10 handle 100: fq_codel
    fi
}

limit_bandwidth $NS_LEFT veth-left1
limit_bandwidth $NS_LEFT veth-left2
limit_bandwidth $NS_RIGHT veth-right1
limit_bandwidth $NS_RIGHT veth-right2

# ==================== START FTP SERVER ====================
echo -e "\n Spawning isolated Python FTP daemon inside $NS_RIGHT..."
# CRITICAL FIX: Binding to 0.0.0.0 tells pyftpdlib to map sockets across both subnets (10.0.1.2 and 10.0.2.2) simultaneously
ip netns exec $NS_RIGHT python3 -m pyftpdlib -i 0.0.0.0 -p $PORT -w -d . > /dev/null 2>&1 &
FTP_SERVER_PID=$!
sleep 1.5

if ! ps -p $FTP_SERVER_PID > /dev/null; then
    echo " Error: Failed to start pyftpdlib server inside namespace."
    exit 1
fi

# ============================================================
# EXPERIMENT 1: THE MIGRATION RUN (Stop at 50%, Switch IP, Resume)
# ============================================================
echo -e "\n============================================="
echo " RUN 1: STARTING REALISTIC IP MIGRATION TEST (Subnet 1 -> Subnet 2)"
echo -e "=============================================\n"

ip -n $NS_LEFT link set veth-left1 up
ip -n $NS_RIGHT link set veth-right1 up
ip -n $NS_LEFT link set veth-left2 down
ip -n $NS_RIGHT link set veth-right2 down

START_MIGRATE=$(date +%s.%N)

# Start background congestion window telemetry (right-ns sender side)
SESSION_LABEL="Migration" "$SCRIPT_DIR/cwnd_logger.sh" "$CWND_FILE" 0.02 &
CWND_LOGGER_PID=$!

# Initialize data connection over Subnet 1 Target
ip netns exec $NS_LEFT lftp -c "
  set ftp:passive-mode true;
  open ftp://${IP_RIGHT_1}:${PORT};
  get $FILENAME -o migrated_$FILENAME;
" &
LFTP_PID=$!

echo "[WAIT] Monitoring download progress... Will halt at 50% (~$((TARGET_HALF_BYTES / 1024 / 1024)) MB)"
while true; do
    if ! ps -p $LFTP_PID > /dev/null; then
        echo " Error: lftp process died before reaching 50% threshold."
        kill $CWND_LOGGER_PID 2>/dev/null || true
        wait $CWND_LOGGER_PID 2>/dev/null || true
        exit 1
    fi
    if [ -f "migrated_$FILENAME" ]; then
        CURRENT_SIZE=$(stat -c%s "migrated_$FILENAME" 2>/dev/null || echo 0)
        if [ "$CURRENT_SIZE" -ge "$TARGET_HALF_BYTES" ]; then
            echo " Hit 50% ($((CURRENT_SIZE / 1024 / 1024)) MB downloaded). Interrupting Subnet 1 Link..."
            kill $LFTP_PID 2>/dev/null || true
            wait $LFTP_PID 2>/dev/null || true
            break
        fi
    fi
    sleep 0.05
done

echo "[FAILOVER] HARD FAILOVER: Destroying Subnet 1 Link  -> Activating Subnet 2 Link "
ip -n $NS_LEFT link set veth-left1 down
ip -n $NS_RIGHT link set veth-right1 down

ip -n $NS_LEFT link set veth-left2 up
ip -n $NS_RIGHT link set veth-right2 up
# sleep 0.5 # Allow virtual interfaces to settle

echo " Resuming LFTP targeting Subnet 2 Server IP (${IP_RIGHT_2})..."
# Re-open session targeting the Subnet 2 server IP with continuation flag
ip netns exec $NS_LEFT lftp -c "
  set ftp:passive-mode true;
  open ftp://${IP_RIGHT_2}:${PORT};
  get -c $FILENAME -o migrated_$FILENAME;
"

# Terminate the cwnd logger for the migration run
kill $CWND_LOGGER_PID 2>/dev/null || true
wait $CWND_LOGGER_PID 2>/dev/null || true

END_MIGRATE=$(date +%s.%N)
TOTAL_MIGRATE=$(echo "$END_MIGRATE - $START_MIGRATE" | bc)
echo "Migration run finished!"

# ============================================================
# EXPERIMENT 2: THE BASELINE RUN (Uninterrupted Path 1)
# ============================================================
echo -e "\n============================================="
echo " RUN 2: STARTING BASELINE TEST (Uninterrupted Subnet 1)"
echo -e "=============================================\n"

ip -n $NS_LEFT link set veth-left2 down
ip -n $NS_RIGHT link set veth-right2 down
ip -n $NS_LEFT link set veth-left1 up
ip -n $NS_RIGHT link set veth-right1 up
sleep 0.5

START_BASELINE=$(date +%s.%N)

# Start background congestion window telemetry (right-ns sender side)
SESSION_LABEL="Baseline" "$SCRIPT_DIR/cwnd_logger.sh" "$CWND_FILE" 0.02 &
CWND_LOGGER_PID=$!

# Baseline executes uninterrupted exclusively on Subnet 1
ip netns exec $NS_LEFT lftp -c "
  set ftp:passive-mode true;
  open ftp://${IP_RIGHT_1}:${PORT};
  get $FILENAME -o baseline_$FILENAME;
"

# Terminate the cwnd logger for the baseline run
kill $CWND_LOGGER_PID 2>/dev/null || true
wait $CWND_LOGGER_PID 2>/dev/null || true

END_BASELINE=$(date +%s.%N)
TOTAL_BASELINE=$(echo "$END_BASELINE - $START_BASELINE" | bc)
echo "[SUCCESS] Baseline run finished!"

# ==================== CLEANUP SERVER & TEST FILES ====================
echo "[CLEANUP] Tearing down background FTP server and cleaning temporary files..."
kill $FTP_SERVER_PID 2>/dev/null || true
pkill -f "pyftpdlib -p $PORT" || true
rm -f "$FILENAME" "migrated_$FILENAME" "baseline_$FILENAME" 2>/dev/null || true

# Calculate metrics
OVERHEAD=$(echo "$TOTAL_MIGRATE - $TOTAL_BASELINE" | bc)
TIMESTAMP=$(date +"%Y-%m-%d %H:%M:%S")

# Format metrics safely for string rendering
D_LATENCY=${LATENCY:-"0ms"}
D_JITTER=${JITTER:-"0ms"}
D_LOSS=${LOSS:-"0%"}

# ==================== FILE LOGGING (CSV) ====================
if [ ! -f "$CSV_FILE" ]; then
    echo "timestamp,configured_rate,configured_latency,configured_jitter,configured_loss,cwnd_mode,file_size_mb,baseline_time_sec,migration_time_sec,overhead_sec" > "$CSV_FILE"
fi

# Write record conforming to header columns
if head -n 1 "$CSV_FILE" | grep -q "cwnd_mode"; then
    echo "${TIMESTAMP},${RATE},${D_LATENCY},${D_JITTER},${D_LOSS},${CWND_MODE},${FILE_SIZE_MB},${TOTAL_BASELINE},${TOTAL_MIGRATE},${OVERHEAD}" >> "$CSV_FILE"
else
    echo "${TIMESTAMP},${RATE},${D_LATENCY},${D_JITTER},${D_LOSS},${FILE_SIZE_MB},${TOTAL_BASELINE},${TOTAL_MIGRATE},${OVERHEAD}" >> "$CSV_FILE"
fi

chmod -R 777 "$OUTPUT_DIR"

# ==================== FINAL TERMINAL REPORT ====================
echo -e "\n============================================="
echo "[METRICS] EXPERIMENT RESULTS ($RATE | Latency: $D_LATENCY | Jitter: $D_JITTER | Loss: $D_LOSS | Mode: $CWND_MODE | FileSize: ${FILE_SIZE_MB}MB)"
echo "============================================="
echo -e "Uninterrupted Baseline Time : \033[1;32m${TOTAL_BASELINE} seconds\033[0m"
echo -e "Stopped & Migrated Path Time: \033[1;31m${TOTAL_MIGRATE} seconds\033[0m"
echo -e "Total Migration Overhead    : \033[1;33m${OVERHEAD} seconds\033[0m"
echo "============================================="
echo "[LOG] Data recorded inside ${OUTPUT_DIR}/"
echo "   ->  $CSV_FILE (Appended)"
echo "   ->  $CWND_FILE (CWND Telemetry)"
echo "============================================="