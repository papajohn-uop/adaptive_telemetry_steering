#!/usr/bin/env python3
"""
phase2_telemetry/telemetry_daemon.py

High-Performance Out-of-Band Active ICMP Prober & Dual-Path Telemetry Engine.
Monitors path health across Link A (Fiber: 10.0.1.1) and Link B (Cellular: 10.0.2.1)
toward the invariant server destination (10.0.3.2).

Computes:
  - Instantaneous sample latency RTT(t)
  - EWMA smoothed RTT (SRTT) with alpha = 0.125
  - Sliding window mean mu_W across N = 3 samples
  - Temporal persistence counters (c_raw and c_win) against SLA threshold tau = 65 ms
"""

import os
import sys
import time
import socket
import struct
import select
import ctypes
import argparse
from collections import deque
from typing import Optional, Dict, Any, Tuple


# Linux network namespace switching constants
CLONE_NEWNET = 0x40000000


def ensure_ns_client(target_ns: str = "ns_client") -> bool:
    """
    Ensure the current process executes inside the target network namespace.
    If already in the namespace (i.e. 'veth_client_a' interface is visible), returns True.
    Otherwise attempts to switch namespace via libc setns().
    """
    try:
        if_names = [name for _, name in socket.if_nameindex()]
        if "veth_client_a" in if_names or "veth_client_b" in if_names:
            return True
    except Exception:
        pass

    ns_path = f"/var/run/netns/{target_ns}"
    if not os.path.exists(ns_path):
        raise FileNotFoundError(
            f"Network namespace file '{ns_path}' does not exist. "
            f"Please run 'sudo ./topology/setup_topology.sh setup' first."
        )

    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        fd = os.open(ns_path, os.O_RDONLY)
        ret = libc.setns(fd, CLONE_NEWNET)
        os.close(fd)
        if ret != 0:
            errno = ctypes.get_errno()
            raise OSError(errno, f"libc setns() failed with errno {errno}. Root privileges required.")
        return True
    except PermissionError:
        raise PermissionError(
            "Root privileges (sudo) are required to enter network namespaces and open raw sockets."
        )


class RawICMPProber:
    """
    Lightweight, high-frequency raw ICMP Echo prober.
    Uses AF_INET / SOCK_RAW with IPPROTO_ICMP for microsecond timing resolution
    without subprocess spawning overhead.
    """

    def __init__(self, src_ip: str, dst_ip: str = "10.0.3.2", timeout: float = 0.5):
        self.src_ip = src_ip
        self.dst_ip = dst_ip
        self.timeout = timeout
        self.sock: Optional[socket.socket] = None
        self.seq = 0
        self.pid = os.getpid() & 0xFFFF
        self._init_socket()

    def _init_socket(self):
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP)
            self.sock.bind((self.src_ip, 0))
            self.sock.setblocking(False)
        except PermissionError:
            raise PermissionError(
                f"Permission denied creating raw ICMP socket for {self.src_ip}. "
                f"Run with sudo or assign CAP_NET_RAW capability."
            )

    @staticmethod
    def _checksum(data: bytes) -> int:
        """RFC 1071 standard internet checksum."""
        csum = 0
        count_to = (len(data) // 2) * 2
        count = 0
        while count < count_to:
            val = data[count + 1] * 256 + data[count]
            csum += val
            csum &= 0xFFFFFFFF
            count += 2
        if count_to < len(data):
            csum += data[-1]
            csum &= 0xFFFFFFFF
        csum = (csum >> 16) + (csum & 0xFFFF)
        csum += (csum >> 16)
        csum = ~csum & 0xFFFF
        return (csum >> 8) | ((csum << 8) & 0xFF00)

    def ping(self) -> Optional[float]:
        """
        Send single ICMP Echo Request and wait for corresponding Echo Reply.
        Returns:
            RTT in milliseconds (float), or None if timed out / lost.
        """
        if self.sock is None:
            self._init_socket()

        self.seq = (self.seq + 1) & 0xFFFF
        t_send = time.perf_counter()
        payload = struct.pack("!d", t_send)
        header = struct.pack("!BBHHH", 8, 0, 0, self.pid, self.seq)
        csum = self._checksum(header + payload)
        pkt = struct.pack("!BBHHH", 8, 0, csum, self.pid, self.seq) + payload

        try:
            self.sock.sendto(pkt, (self.dst_ip, 0))
        except OSError:
            return None

        deadline = t_send + self.timeout
        while True:
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                return None

            try:
                r, _, _ = select.select([self.sock], [], [], remaining)
            except (ValueError, OSError):
                return None

            if not r:
                return None

            try:
                rec_pkt, _ = self.sock.recvfrom(1024)
            except OSError:
                return None

            t_recv = time.perf_counter()

            # IPv4 header is typically 20 bytes (IHL * 4)
            if len(rec_pkt) < 28:
                continue

            ihl = (rec_pkt[0] & 0x0F) * 4
            icmp_header = rec_pkt[ihl : ihl + 8]
            if len(icmp_header) < 8:
                continue

            icmp_type, icmp_code, _, icmp_id, icmp_seq = struct.unpack("!BBHHH", icmp_header)
            # Echo Reply is type 0, code 0
            if icmp_type == 0 and icmp_id == self.pid and icmp_seq == self.seq:
                return (t_recv - t_send) * 1000.0

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None


class PathTelemetry:
    """
    Maintains running latency statistics and decision counters for a single path.
    Implements equations (5) through (10) from the conference paper.

    How it works:
      1. Out-of-band ICMP Probing: Uses RawICMPProber bound to a specific source IP
         (e.g., 10.0.1.1 for Link A or 10.0.2.1 for Link B) to send ICMP Echo Requests
         to the destination without interfering with application TCP sockets.
      2. Multi-metric Latency Tracking:
         - Instantaneous RTT: The exact round-trip duration of the most recent probe.
         - EWMA Smoothed RTT (SRTT): RFC 6298 smoothed latency filter (alpha = 0.125).
         - Sliding Window Mean (mu_W): Simple moving average across the last N samples.
      3. Persistence Hysteresis Counters:
         - c_raw: Number of consecutive raw RTT samples exceeding SLA ceiling tau (65 ms).
         - c_win: Number of consecutive window means exceeding SLA ceiling tau.
         Counters reset to 0 as soon as a sample/mean falls back below tau.
    """

    def __init__(
        self,
        name: str,
        src_ip: str,
        dst_ip: str = "10.0.3.2",
        tau: float = 65.0,
        window_size: int = 3,
        alpha: float = 0.125,
    ):
        self.name = name
        self.src_ip = src_ip
        self.dst_ip = dst_ip
        self.tau = tau                      # SLA latency violation threshold ceiling (e.g. 65.0 ms)
        self.window_size = window_size      # Window depth N (e.g., 3 samples = 300 ms at 10 Hz)
        self.alpha = alpha                  # EWMA smoothing weight (alpha = 0.125 = 1/8)

        # Raw ICMP socket prober bound to source IP (steers probe via policy routing)
        self.prober = RawICMPProber(src_ip, dst_ip)

        # O(1) Ring buffer for sliding window average: automatically discards oldest item on append
        self.window: deque = deque(maxlen=window_size)

        # State variables
        self.srtt: Optional[float] = None
        self.last_rtt: Optional[float] = None
        self.c_raw: int = 0                 # Consecutive raw breaches counter
        self.c_win: int = 0                 # Consecutive window breaches counter
        self.total_probes: int = 0
        self.lost_probes: int = 0

    def sample(self) -> Tuple[Optional[float], Optional[float], Optional[float], int, int]:
        """
        Executes one probe tick, updates window and counters.

        Workflow:
          Step 1: Emit an ICMP Echo Request and await response to measure RTT.
          Step 2: If a timeout/drop occurs, record probe loss and treat as severe degradation (999.0 ms).
          Step 3: Update EWMA Smoothed RTT: SRTT = (1 - alpha) * SRTT + alpha * RTT.
          Step 4: Push sample into the O(1) FIFO ring buffer and calculate rolling mean mu_W.
          Step 5: Update persistence counters: increment if > tau, reset to 0 if <= tau.

        Returns:
            (raw_rtt, srtt, rolling_mean, c_raw, c_win)
        """
        # Step 1: Send ICMP Echo Request with timestamp payload and await Echo Reply
        rtt = self.prober.ping()
        self.total_probes += 1
        self.last_rtt = rtt

        # Step 2: Handle packet loss / timeouts
        if rtt is None:
            self.lost_probes += 1
            # Treat loss as severe degradation to penalize degraded path
            effective_rtt = 999.0
        else:
            effective_rtt = rtt

        # Step 3: Update EWMA Smoothed RTT (RFC 6298 estimator)
        if self.srtt is None:
            self.srtt = effective_rtt
        else:
            self.srtt = (1.0 - self.alpha) * self.srtt + self.alpha * effective_rtt

        # Step 4: Update FIFO sliding window (deque with maxlen acts as an O(1) ring buffer)
        self.window.append(effective_rtt)
        rolling_mean = sum(self.window) / len(self.window)

        # Step 5: Update temporal persistence counters (hysteresis filters)
        # Raw RTT persistence counter (resets immediately if condition clears)
        if effective_rtt > self.tau:
            self.c_raw += 1
        else:
            self.c_raw = 0

        # Sliding window persistence counter (resets immediately if window mean clears)
        if rolling_mean > self.tau:
            self.c_win += 1
        else:
            self.c_win = 0

        return rtt, self.srtt, rolling_mean, self.c_raw, self.c_win

    def close(self):
        """Release raw socket resources."""
        self.prober.close()


class TelemetryDaemon:
    """
    High-level telemetry coordinator concurrently monitoring:
      - Link A (Fiber: 10.0.1.1 -> 10.0.3.2)
      - Link B (Cellular: 10.0.2.1 -> 10.0.3.2)

    How it works:
      1. Namespace Alignment: Ensures execution inside 'ns_client' network namespace
         so that virtual interfaces (veth_client_a, veth_client_b) and Linux policy
         routing rules (lookup tables 101/102) are directly accessible.
      2. Dual-Path Management: Instantiates two independent PathTelemetry objects:
         - link_a: Probes the pristine fiber backhaul (bound to 10.0.1.1, ~4.3 ms RTT).
         - link_b: Probes the cellular/wireless backhaul (bound to 10.0.2.1, ~50.7 ms RTT).
      3. Synchronized Probing (probe_once): Every polling period, queries both paths
         in succession, aggregating raw RTT, smoothed SRTT, rolling mean, and hysteresis counters.
      4. Periodic Execution Loop (run):
         - Controls sampling cadence (e.g., 100 ms interval = 10 Hz polling).
         - Compensates for probe computation time using monotonic clock delta to prevent timing drift.
         - Streams structured telemetry metrics to console and optional CSV log file.
    """

    def __init__(
        self,
        interval: float = 0.1,  # Default: 100 ms polling cadence (10 Hz)
        tau: float = 65.0,      # Default: 65.0 ms SLA latency ceiling
        window_size: int = 3,   # Default: 3-sample sliding window depth
        dst_ip: str = "10.0.3.2",
    ):
        # Step 1: Switch / verify execution inside the client network namespace
        ensure_ns_client("ns_client")
        self.interval = interval
        self.tau = tau
        self.window_size = window_size
        self.dst_ip = dst_ip

        # Step 2: Initialize dedicated telemetry monitors for Link A and Link B
        # Policy routing rules direct probes bound to 10.0.1.1 over Link A and 10.0.2.1 over Link B
        self.link_a = PathTelemetry("Link A (Fiber)", "10.0.1.1", dst_ip, tau, window_size)
        self.link_b = PathTelemetry("Link B (Cellular)", "10.0.2.1", dst_ip, tau, window_size)
        self.running = False

    def probe_once(self) -> Dict[str, Any]:
        """
        Probe both links synchronously once and return an aggregated metric dictionary.

        Workflow:
          - Captures high-precision epoch timestamp.
          - Samples Link A (returns raw RTT, EWMA SRTT, rolling mean, and counters).
          - Samples Link B (returns raw RTT, EWMA SRTT, rolling mean, and counters).
          - Packages all metrics into a structured dictionary for the steering controller.
        """
        t_epoch = time.time()
        # Sample Link A (Fiber path)
        rtt_a, srtt_a, mean_a, c_raw_a, c_win_a = self.link_a.sample()
        # Sample Link B (Cellular / primary path)
        rtt_b, srtt_b, mean_b, c_raw_b, c_win_b = self.link_b.sample()

        return {
            "timestamp": t_epoch,
            "link_a": {
                "rtt": rtt_a,
                "srtt": srtt_a,
                "mean": mean_a,
                "c_raw": c_raw_a,
                "c_win": c_win_a,
            },
            "link_b": {
                "rtt": rtt_b,
                "srtt": srtt_b,
                "mean": mean_b,
                "c_raw": c_raw_b,
                "c_win": c_win_b,
            },
        }

    def run(self, count: Optional[int] = None, log_file: Optional[str] = None, verbose: bool = True):
        """
        Run continuous telemetry loop for specified count or indefinitely.

        Workflow:
          1. Opens CSV output log (if path specified) and writes schema header.
          2. Loops while self.running is True:
             a. Marks loop start via high-precision monotonic clock (time.perf_counter).
             b. Executes probe_once() to poll both links.
             c. Logs row to CSV and displays real-time console table.
             d. Increments tick counter; halts if count limit reached.
             e. Calculates remaining interval (interval - elapsed) and sleeps to prevent drift.
          3. On exit or interrupt, ensures file flush and closes socket descriptors.
        """
        self.running = True
        fh = open(log_file, "w") if log_file else None
        if fh:
            fh.write(
                "timestamp,tick,link_a_rtt,link_a_srtt,link_a_mean,"
                "link_b_rtt,link_b_srtt,link_b_mean,link_b_c_raw,link_b_c_win\n"
            )
            fh.flush()

        if verbose:
            print(f"[INFO] Telemetry Daemon Started [Interval: {self.interval*1000:.0f}ms, SLA: {self.tau}ms]")
            print(f"{'Tick':<6} | {'Link A (Fiber)':<20} | {'Link B (Cellular)':<32} | {'Counters (B)':<10}")
            print("-" * 76)

        tick = 0
        t_start = time.perf_counter()

        try:
            while self.running:
                loop_start = time.perf_counter()
                
                # Poll both links
                data = self.probe_once()
                la = data["link_a"]
                lb = data["link_b"]

                # Format metrics for console display
                rtt_a_str = f"{la['rtt']:.2f} ms" if la["rtt"] is not None else "LOST"
                rtt_b_str = f"{lb['rtt']:.2f} ms" if lb["rtt"] is not None else "LOST"
                mean_b_str = f"{lb['mean']:.2f} ms" if lb["mean"] is not None else "N/A"

                if verbose:
                    cnt_str = f"c_raw={lb['c_raw']} c_win={lb['c_win']}"
                    b_detail = f"{rtt_b_str} (mean: {mean_b_str})"
                    print(f"{tick:<6} | {rtt_a_str:<20} | {b_detail:<32} | {cnt_str:<10}")

                # Append record to CSV log
                if fh:
                    fh.write(
                        f"{data['timestamp']:.6f},{tick},"
                        f"{la['rtt'] or ''},{la['srtt'] or ''},{la['mean'] or ''},"
                        f"{lb['rtt'] or ''},{lb['srtt'] or ''},{lb['mean'] or ''},"
                        f"{lb['c_raw']},{lb['c_win']}\n"
                    )
                    fh.flush()

                tick += 1
                if count and tick >= count:
                    break

                # Precise sleep calculation: subtracts probe execution time from interval to eliminate drift
                elapsed = time.perf_counter() - loop_start
                sleep_time = max(0.0, self.interval - elapsed)
                time.sleep(sleep_time)

        except KeyboardInterrupt:
            if verbose:
                print("\n[INFO] Telemetry Daemon stopped by user.")
        finally:
            if fh:
                fh.close()
            self.close()

    def close(self):
        """Cleanly close raw sockets on both path monitors."""
        self.link_a.close()
        self.link_b.close()


def main():
    parser = argparse.ArgumentParser(description="Active Dual-Path Telemetry Prober")
    parser.add_argument("--interval", type=float, default=0.1, help="Sampling interval in seconds (default: 0.1s)")
    parser.add_argument("--tau", type=float, default=65.0, help="SLA latency ceiling in ms (default: 65.0)")
    parser.add_argument("--window", type=int, default=3, help="Sliding window sample count N (default: 3)")
    parser.add_argument("--count", type=int, default=None, help="Total probes to send (default: unlimited)")
    parser.add_argument("--log", type=str, default=None, help="Path to write CSV telemetry log")
    parser.add_argument("--quiet", action="store_true", help="Suppress real-time stdout table")

    args = parser.parse_args()

    daemon = TelemetryDaemon(
        interval=args.interval,
        tau=args.tau,
        window_size=args.window,
    )
    daemon.run(count=args.count, log_file=args.log, verbose=not args.quiet)


if __name__ == "__main__":
    main()
