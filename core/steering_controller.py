#!/usr/bin/env python3
"""
phase2_telemetry/steering_controller.py

Active Flow Steering Controller for Heterogeneous 5G/6G Multi-Backhaul Networks.
Implements the 2x2 Policy Taxonomy (Filtering Depth vs. Temporal Persistence):
  - Policy 1: Strict Raw (Memoryless Single Sample, K=1)
  - Policy 2: Raw + Hysteresis (Temporal Persistence, K=2) [Pareto-Optimal]
  - Policy 3: Rolling Window Only (Sample Averaging, N=3, K=1)
  - Policy 4: Rolling Window + Hysteresis (Full Protection, N=3, K=2)

Integrates:
  - Out-of-band active ICMP probing via RawICMPProber
  - Atomic Linux Netlink default route replacement (`ip route replace default via ...`)
  - Sub-millisecond route replacement latency measurement
  - High-precision telemetry and decision logging
"""

import os
import sys
import time
import socket
import argparse
import subprocess
from enum import IntEnum
from collections import deque
from typing import Optional, Dict, Any, List, Callable

# Support both module import and direct script execution
if __package__ is None or __package__ == "":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from telemetry_daemon import RawICMPProber, ensure_ns_client
else:
    from .telemetry_daemon import RawICMPProber, ensure_ns_client


class PolicyType(IntEnum):
    STRICT_RAW = 1          # Immediate trigger on single RTT(t) > tau
    RAW_HYSTERESIS = 2      # Trigger on RTT(t) > tau for K consecutive ticks
    ROLLING_WINDOW = 3      # Trigger on Mean(W_N) > tau
    WINDOW_HYSTERESIS = 4   # Trigger on Mean(W_N) > tau for K consecutive ticks

    @classmethod
    def from_str(cls, val: Any) -> "PolicyType":
        if isinstance(val, int):
            return cls(val)
        val_str = str(val).strip().lower()
        mapping = {
            "1": cls.STRICT_RAW,
            "strict_raw": cls.STRICT_RAW,
            "raw": cls.STRICT_RAW,
            "2": cls.RAW_HYSTERESIS,
            "raw_hysteresis": cls.RAW_HYSTERESIS,
            "hysteresis": cls.RAW_HYSTERESIS,
            "3": cls.ROLLING_WINDOW,
            "rolling_window": cls.ROLLING_WINDOW,
            "window": cls.ROLLING_WINDOW,
            "4": cls.WINDOW_HYSTERESIS,
            "window_hysteresis": cls.WINDOW_HYSTERESIS,
            "win_hyst": cls.WINDOW_HYSTERESIS,
        }
        if val_str in mapping:
            return mapping[val_str]
        raise ValueError(f"Unknown policy '{val}'. Must be 1, 2, 3, or 4.")


# Backhaul Link Definitions
LINK_CONFIG = {
    "A": {
        "name": "Link A (Fiber)",
        "dev": "veth_client_a",
        "gw": "10.0.1.2",
        "src_ip": "10.0.1.1",
        "table": 101,
        "nominal_rtt": 4.0,
    },
    "B": {
        "name": "Link B (Cellular)",
        "dev": "veth_client_b",
        "gw": "10.0.2.2",
        "src_ip": "10.0.2.1",
        "table": 102,
        "nominal_rtt": 50.0,
    },
}


class SteeringController:
    """
    Unified 2x2 Steering Controller and Telemetry Engine.
    Implements Algorithm 1 from the conference paper.
    """

    def __init__(
        self,
        policy: Any = PolicyType.RAW_HYSTERESIS,
        tau: float = 65.0,
        window_size: int = 3,
        persistence: int = 2,
        interval: float = 0.1,  # 100 ms
        initial_link: str = "B",
        dst_ip: str = "10.0.3.2",
        dual_probing: bool = True,
        alpha: float = 0.125,
    ):
        ensure_ns_client("ns_client")

        self.policy = PolicyType.from_str(policy)
        self.tau = float(tau)
        self.window_size = int(window_size)
        self.persistence = int(persistence)
        self.interval = float(interval)
        self.initial_link = initial_link.upper()
        self.active_link = self.initial_link
        self.dst_ip = dst_ip
        self.dual_probing = dual_probing
        self.alpha = float(alpha)

        # Probers for individual paths
        self.probers = {
            "A": RawICMPProber(LINK_CONFIG["A"]["src_ip"], dst_ip),
            "B": RawICMPProber(LINK_CONFIG["B"]["src_ip"], dst_ip),
        }

        # Algorithm 1 state variables
        self.window: deque = deque(maxlen=self.window_size)
        self.c_raw: int = 0
        self.c_win: int = 0
        self.srtt: Optional[float] = None
        self.records: List[Dict[str, Any]] = []
        self.handover_count: int = 0
        self.first_handover_time: Optional[float] = None
        self.first_handover_tick: Optional[int] = None

    def reset_state(self, initial_link: Optional[str] = None):
        """Reset internal metrics and counters for a fresh run."""
        if initial_link:
            self.initial_link = initial_link.upper()
        self.active_link = self.initial_link
        self.window.clear()
        self.c_raw = 0
        self.c_win = 0
        self.srtt = None
        self.records.clear()
        self.handover_count = 0
        self.first_handover_time = None
        self.first_handover_tick = None
        # Apply initial route
        self.apply_default_route(self.initial_link)

    def apply_default_route(self, link_id: str) -> float:
        """
        Execute atomic Netlink route replacement:
          `ip route replace default via <GW> dev <DEV>`
        Returns route replacement duration in milliseconds.
        """
        cfg = LINK_CONFIG[link_id]
        cmd = ["ip", "route", "replace", "default", "via", cfg["gw"], "dev", cfg["dev"]]

        t0 = time.perf_counter()
        res = subprocess.run(cmd, capture_output=True, text=True)
        t1 = time.perf_counter()

        if res.returncode != 0:
            raise RuntimeError(f"Failed to replace default route: {res.stderr}")

        duration_ms = (t1 - t0) * 1000.0
        self.active_link = link_id
        return duration_ms

    def evaluate_trigger(self, effective_rtt: float, rolling_mean: float) -> bool:
        """
        Evaluates the trigger condition according to Algorithm 1:
          Policy 1: rtt > tau
          Policy 2: c_raw >= K
          Policy 3: mu_W > tau
          Policy 4: c_win >= K
        """
        if self.policy == PolicyType.STRICT_RAW:
            return effective_rtt > self.tau
        elif self.policy == PolicyType.RAW_HYSTERESIS:
            return self.c_raw >= self.persistence
        elif self.policy == PolicyType.ROLLING_WINDOW:
            return rolling_mean > self.tau
        elif self.policy == PolicyType.WINDOW_HYSTERESIS:
            return self.c_win >= self.persistence
        return False

    def step(self, tick_idx: int, t_start: float) -> Dict[str, Any]:
        """
        Execute single polling interval step:
          1. Probe active link (and alternate if dual_probing enabled)
          2. Update window and temporal persistence counters
          3. Evaluate policy trigger
          4. Execute atomic route handover if triggered
        """
        now = time.time()
        rel_time = time.perf_counter() - t_start

        # 1. Probe active link
        active_prober = self.probers[self.active_link]
        rtt = active_prober.ping()

        # Alternate link probe if enabled
        alt_link = "A" if self.active_link == "B" else "B"
        rtt_alt = self.probers[alt_link].ping() if self.dual_probing else None

        # Treat packet loss / timeout as severe degradation
        effective_rtt = rtt if rtt is not None else 999.0

        # Update EWMA SRTT
        if self.srtt is None:
            self.srtt = effective_rtt
        else:
            self.srtt = (1.0 - self.alpha) * self.srtt + self.alpha * effective_rtt

        # Update sliding window
        self.window.append(effective_rtt)
        rolling_mean = sum(self.window) / len(self.window)

        # Update persistence counters
        if effective_rtt > self.tau:
            self.c_raw += 1
        else:
            self.c_raw = 0

        if rolling_mean > self.tau:
            self.c_win += 1
        else:
            self.c_win = 0

        # Evaluate policy trigger
        trigger_fired = self.evaluate_trigger(effective_rtt, rolling_mean)
        handover_executed = False
        switch_latency_ms = 0.0

        # Trigger handover if condition met and currently on Link B
        if trigger_fired and self.active_link == "B":
            switch_latency_ms = self.apply_default_route("A")
            handover_executed = True
            self.handover_count += 1
            if self.first_handover_time is None:
                self.first_handover_time = rel_time
                self.first_handover_tick = tick_idx

        record = {
            "timestamp": now,
            "rel_time_s": rel_time,
            "tick": tick_idx,
            "active_link": self.active_link,
            "rtt_sample": rtt,
            "srtt": self.srtt,
            "rolling_mean": rolling_mean,
            "c_raw": self.c_raw,
            "c_win": self.c_win,
            "policy": int(self.policy),
            "policy_name": self.policy.name,
            "tau": self.tau,
            "trigger_fired": trigger_fired,
            "handover_executed": handover_executed,
            "switch_latency_ms": switch_latency_ms,
            "rtt_alt": rtt_alt,
        }
        self.records.append(record)
        return record

    def run(
        self,
        duration: float = 20.0,
        stop_on_trigger: bool = False,
        tick_callback: Optional[Callable[[int, float, Dict[str, Any]], None]] = None,
        verbose: bool = True,
        log_file: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Run steering controller session for given duration (in seconds).
        Args:
            duration: Total duration to execute in seconds.
            stop_on_trigger: If True, halts loop immediately after first handover.
            tick_callback: Optional callback(tick_idx, rel_time_s, tick_data) called each tick.
            verbose: Print live updates to console.
            log_file: Optional CSV path to save execution trace.
        Returns:
            List of per-tick record dictionaries.
        """
        self.reset_state(self.initial_link)
        t_start = time.perf_counter()

        if verbose:
            print(f"[INFO] Steering Controller Active [Policy {self.policy.value}: {self.policy.name}]")
            print(f"   Config: tau={self.tau}ms, N={self.window_size}, K={self.persistence}, Interval={self.interval*1000:.0f}ms")
            print(f"{'Tick':<5} | {'RelTime':<8} | {'Active':<6} | {'RTT':<10} | {'Mean(W)':<10} | {'c_raw':<5} | {'c_win':<5} | {'Action':<15}")
            print("-" * 76)

        tick_idx = 0
        try:
            while True:
                loop_start = time.perf_counter()
                rel_time = loop_start - t_start

                if rel_time >= duration:
                    break

                rec = self.step(tick_idx, t_start)

                if tick_callback:
                    tick_callback(tick_idx, rel_time, rec)

                if verbose:
                    rtt_str = f"{rec['rtt_sample']:.2f} ms" if rec["rtt_sample"] is not None else "LOST"
                    action_str = ""
                    if rec["handover_executed"]:
                        action_str = f"SWITCH ({rec['switch_latency_ms']:.2f}ms)"
                    elif rec["trigger_fired"]:
                        action_str = "TRIGGERED"

                    print(
                        f"{rec['tick']:<5} | {rec['rel_time_s']:<8.3f} | {rec['active_link']:<6} | "
                        f"{rtt_str:<10} | {rec['rolling_mean']:<10.2f} | {rec['c_raw']:<5} | "
                        f"{rec['c_win']:<5} | {action_str:<15}"
                    )

                if stop_on_trigger and rec["handover_executed"]:
                    if verbose:
                        print(f"[INFO] Stopping session after handover at tick {tick_idx} (t={rec['rel_time_s']:.3f}s)")
                    break

                tick_idx += 1
                elapsed = time.perf_counter() - loop_start
                sleep_time = max(0.0, self.interval - elapsed)
                time.sleep(sleep_time)

        except KeyboardInterrupt:
            if verbose:
                print("\n[INFO] Controller interrupted by user.")
        finally:
            if log_file:
                self.save_csv(log_file)

        return self.records

    def save_csv(self, filename: str):
        """Export session execution records to CSV format."""
        if not self.records:
            return
        os.makedirs(os.path.dirname(os.path.abspath(filename)), exist_ok=True)
        keys = list(self.records[0].keys())
        with open(filename, "w") as f:
            f.write(",".join(keys) + "\n")
            for r in self.records:
                row = [str(r[k]) if r[k] is not None else "" for k in keys]
                f.write(",".join(row) + "\n")

    def get_summary(self) -> Dict[str, Any]:
        """Compute aggregate summary metrics of the completed session."""
        if not self.records:
            return {}

        total_ticks = len(self.records)
        handovers = sum(1 for r in self.records if r["handover_executed"])
        sla_violations = sum(1 for r in self.records if r["rtt_sample"] and r["rtt_sample"] > self.tau)
        violation_duration_ms = sla_violations * (self.interval * 1000.0)

        return {
            "policy": int(self.policy),
            "policy_name": self.policy.name,
            "total_ticks": total_ticks,
            "total_duration_s": self.records[-1]["rel_time_s"] if self.records else 0.0,
            "handover_count": handovers,
            "first_handover_time": self.first_handover_time,
            "first_handover_tick": self.first_handover_tick,
            "sla_violation_count": sla_violations,
            "sla_violation_duration_ms": violation_duration_ms,
        }

    def close(self):
        for p in self.probers.values():
            p.close()


def main():
    parser = argparse.ArgumentParser(description="2x2 Steering Controller Engine")
    parser.add_argument(
        "--policy",
        "-p",
        default="2",
        help="Steering policy: 1 (Strict Raw), 2 (Raw+Hyst), 3 (Window), 4 (Win+Hyst)",
    )
    parser.add_argument("--tau", type=float, default=65.0, help="SLA threshold ceiling in ms (default: 65.0)")
    parser.add_argument("--window", "-N", type=int, default=3, help="Rolling window size N (default: 3)")
    parser.add_argument("--persistence", "-K", type=int, default=2, help="Hysteresis persistence counter K (default: 2)")
    parser.add_argument("--interval", "-i", type=float, default=0.1, help="Polling period in seconds (default: 0.1s)")
    parser.add_argument("--duration", "-d", type=float, default=20.0, help="Session duration in seconds (default: 20.0s)")
    parser.add_argument("--initial", default="B", choices=["A", "B"], help="Initial active link (default: B)")
    parser.add_argument("--stop-on-trigger", action="store_true", help="Halt immediately after first route switch")
    parser.add_argument("--log", type=str, default=None, help="Path to write CSV execution log")
    parser.add_argument("--quiet", action="store_true", help="Suppress console tabular updates")

    args = parser.parse_args()

    controller = SteeringController(
        policy=args.policy,
        tau=args.tau,
        window_size=args.window,
        persistence=args.persistence,
        interval=args.interval,
        initial_link=args.initial,
    )

    try:
        controller.run(
            duration=args.duration,
            stop_on_trigger=args.stop_on_trigger,
            verbose=not args.quiet,
            log_file=args.log,
        )
        summary = controller.get_summary()
        print("\n--- Session Summary ---")
        for k, v in summary.items():
            print(f"   {k}: {v}")
    finally:
        controller.close()


if __name__ == "__main__":
    main()
