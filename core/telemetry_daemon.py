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
            f"Please run 'sudo ./scripts/setup_phase2_topology.sh setup' first."
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
        self.tau = tau
        self.window_size = window_size
        self.alpha = alpha

        self.prober = RawICMPProber(src_ip, dst_ip)
        self.window: deque = deque(maxlen=window_size)
        self.srtt: Optional[float] = None
        self.last_rtt: Optional[float] = None
        self.c_raw: int = 0
        self.c_win: int = 0
        self.total_probes: int = 0
        self.lost_probes: int = 0

    def sample(self) -> Tuple[Optional[float], Optional[float], Optional[float], int, int]:
        """
        Executes one probe tick, updates window and counters.
        Returns:
            (raw_rtt, srtt, rolling_mean, c_raw, c_win)
        """
        rtt = self.prober.ping()
        self.total_probes += 1
        self.last_rtt = rtt

        if rtt is None:
            self.lost_probes += 1
            # Treat loss as severe degradation
            effective_rtt = 999.0
        else:
            effective_rtt = rtt

        # 1. Update EWMA Smoothed RTT (SRTT)
        if self.srtt is None:
            self.srtt = effective_rtt
        else:
            self.srtt = (1.0 - self.alpha) * self.srtt + self.alpha * effective_rtt

        # 2. Update FIFO sliding window
        self.window.append(effective_rtt)
        rolling_mean = sum(self.window) / len(self.window)

        # 3. Update temporal persistence counters
        if effective_rtt > self.tau:
            self.c_raw += 1
        else:
            self.c_raw = 0

        if rolling_mean > self.tau:
            self.c_win += 1
        else:
            self.c_win = 0

        return rtt, self.srtt, rolling_mean, self.c_raw, self.c_win

    def close(self):
        self.prober.close()


class TelemetryDaemon:
    """
    High-level telemetry coordinator concurrently monitoring:
      - Link A (Fiber: 10.0.1.1 -> 10.0.3.2)
      - Link B (Cellular: 10.0.2.1 -> 10.0.3.2)
    """

    def __init__(
        self,
        interval: float = 0.1,  # 100 ms
        tau: float = 65.0,
        window_size: int = 3,
        dst_ip: str = "10.0.3.2",
    ):
        ensure_ns_client("ns_client")
        self.interval = interval
        self.tau = tau
        self.window_size = window_size
        self.dst_ip = dst_ip

        self.link_a = PathTelemetry("Link A (Fiber)", "10.0.1.1", dst_ip, tau, window_size)
        self.link_b = PathTelemetry("Link B (Cellular)", "10.0.2.1", dst_ip, tau, window_size)
        self.running = False

    def probe_once(self) -> Dict[str, Any]:
        """Probe both links synchronously once and return metric dictionary."""
        t_epoch = time.time()
        rtt_a, srtt_a, mean_a, c_raw_a, c_win_a = self.link_a.sample()
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
        """Run telemetry loop for specified count or indefinitely."""
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
                data = self.probe_once()
                la = data["link_a"]
                lb = data["link_b"]

                rtt_a_str = f"{la['rtt']:.2f} ms" if la["rtt"] is not None else "LOST"
                rtt_b_str = f"{lb['rtt']:.2f} ms" if lb["rtt"] is not None else "LOST"
                mean_b_str = f"{lb['mean']:.2f} ms" if lb["mean"] is not None else "N/A"

                if verbose:
                    cnt_str = f"c_raw={lb['c_raw']} c_win={lb['c_win']}"
                    b_detail = f"{rtt_b_str} (mean: {mean_b_str})"
                    print(f"{tick:<6} | {rtt_a_str:<20} | {b_detail:<32} | {cnt_str:<10}")

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

                # Precise sleep accounting for execution time
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
