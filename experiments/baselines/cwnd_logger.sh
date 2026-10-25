#!/bin/bash
# cwnd_logger.sh - High-precision sender-side TCP cwnd logger

OUTPUT_FILE=${1:-${CWND_FILE:-${RESULTS_DIR:+"$RESULTS_DIR/real_kernel_cwnd_${FILE_SIZE_MB:-100}MB.csv"}}}
OUTPUT_FILE=${OUTPUT_FILE:-${OUTPUT_DIR:+"$OUTPUT_DIR/real_kernel_cwnd_${FILE_SIZE_MB:-100}MB.csv"}}
OUTPUT_FILE=${OUTPUT_FILE:-"./results/raw/real_kernel_cwnd_${FILE_SIZE_MB:-100}MB.csv"}
POLL_INTERVAL=${2:-"0.02"}
SESSION_LABEL=${SESSION_LABEL:-"Migration"}

# Ensure the output directory exists
mkdir -p "$(dirname "$OUTPUT_FILE")"

# Write header if the file does not exist or is empty
if [ ! -s "$OUTPUT_FILE" ]; then
    echo "timestamp,session_type,cwnd_segments" > "$OUTPUT_FILE"
fi

echo "Starting TCP cwnd logging in right-ns (sender side)..."
echo "Output file: $OUTPUT_FILE"
echo "Polling interval: ${POLL_INTERVAL}s"
echo "Session label mode: $SESSION_LABEL"

# Handle shutdown signals gracefully
cleanup() {
    echo "Stopping TCP cwnd logging..."
    exit 0
}
trap cleanup SIGINT SIGTERM

while true; do
    TIMESTAMP=$(date +"%s.%N")
    
    # Query TCP sockets in the sender namespace (right-ns)
    DATA=$(ip netns exec right-ns ss -t -i -n 2>/dev/null)
    
    # Determine the active subnet based on interface link states
    ACTIVE_SUBNET=""
    if ip netns exec right-ns ip link show veth-right1 2>/dev/null | grep -q "state UP"; then
        ACTIVE_SUBNET="10.0.1."
    elif ip netns exec right-ns ip link show veth-right2 2>/dev/null | grep -q "state UP"; then
        ACTIVE_SUBNET="10.0.2."
    fi
    
    if [ -n "$DATA" ]; then
        # Parse data using awk and append directly to the output file
        echo "$DATA" | awk -v ts="$TIMESTAMP" -v label="$SESSION_LABEL" -v active_subnet="$ACTIVE_SUBNET" '
        $1 == "ESTAB" {
            local_addr = $4
            peer_addr = $5
            
            split(local_addr, l, ":")
            split(peer_addr, p, ":")
            
            local_port = l[length(l)]
            peer_ip = p[1]
            
            # Ignore control channel (local port 2121 on server side)
            # and verify it matches the active subnet if one is detected
            if (local_port != "2121" && (active_subnet == "" || index(peer_ip, active_subnet) == 1)) {
                session_type = "Unknown"
                if (peer_ip ~ /^10\.0\.1\./) {
                    if (label == "Baseline") {
                        session_type = "Uninterrupted_Baseline"
                    } else {
                        session_type = "Phase1_Baseline"
                    }
                } else if (peer_ip ~ /^10\.0\.2\./) {
                    session_type = "Phase2_Migrated"
                }
                
                active_conn = 1
                curr_session = session_type
                next
            }
        }
        active_conn && /cwnd:/ {
            match($0, /cwnd:[0-9]+/)
            if (RSTART > 0) {
                cwnd_val = substr($0, RSTART + 5, RLENGTH - 5)
                print ts "," curr_session "," cwnd_val
            }
            active_conn = 0
        }
        $1 == "ESTAB" && active_conn {
            active_conn = 0
        }
        ' >> "$OUTPUT_FILE"
    fi
    
    sleep "$POLL_INTERVAL"
done
