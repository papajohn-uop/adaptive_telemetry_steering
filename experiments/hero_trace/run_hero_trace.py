#!/usr/bin/env python3
"""
phase3_eval/run_hero_trace.py

Part A: Deterministic Micro-Benchmark (Hero Trace Runner)
Evaluates all four flow steering policies across a 20.0-second timeline under two
precisely controlled netem impairment events on Link B:
  1. Transient Micro-Burst (t = 5.0s -> 5.2s): Link B RTT spikes to ~88 ms for 200 ms.
  2. Persistent Structural Degradation (t = 10.0s): Link B RTT permanently steps to ~95 ms.

Records synchronized high-resolution time series for:
  - Policy 1: Strict Raw (K=1)
  - Policy 2: Raw + Hysteresis (K=2) [Pareto-Optimal]
  - Policy 3: Rolling Window Only (N=3, K=1)
  - Policy 4: Window + Hysteresis (N=3, K=2)

Outputs CSV datasets to ANALYSIS_RESULTS/PHASE3/hero_trace_policy_{1,2,3,4}.csv
"""

import os
import sys
import time
import threading
import argparse
import subprocess
from typing import Dict, Any, List

# Ensure parent package imports
CUR_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(CUR_DIR, "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

try:
    from core.steering_controller import SteeringController, PolicyType
    from core.telemetry_daemon import ensure_ns_client
except ImportError:
    from phase2_telemetry.steering_controller import SteeringController, PolicyType
    from phase2_telemetry.telemetry_daemon import ensure_ns_client


OUTPUT_DIR = os.path.join(REPO_ROOT, "data", "hero_trace")


def set_router_channel(delay_ms: str = "25ms", jitter_ms: str = "0ms", loss_pct: str = "0%"):
    """
    Directly update tc netem on ns_router veth_router_b interface.
    Base RTT = 25ms (client egress) + delay_ms (router egress).
      - delay_ms="25ms" -> 50 ms base RTT (Nominal)
      - delay_ms="63ms" -> 88 ms base RTT (Transient Spike)
      - delay_ms="70ms" -> 95 ms base RTT (Structural Step)
    """
    netem_args = ["delay", delay_ms]
    if jitter_ms != "0ms":
        netem_args += [jitter_ms, "distribution", "normal"]
    if loss_pct != "0%":
        netem_args += ["loss", loss_pct]

    cmd = [
        "ip", "netns", "exec", "ns_router",
        "tc", "qdisc", "change", "dev", "veth_router_b",
        "handle", "1:", "netem"
    ] + netem_args

    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"tc qdisc change failed: {res.stderr}")


class HeroTraceRunner:
    """
    Orchestrates the deterministic Hero Trace benchmark across 20.0 seconds.
    Synchronizes background impairment injection with client steering execution.
    """

    def __init__(
        self,
        duration: float = 20.0,
        tau: float = 65.0,
        interval: float = 0.1,  # 100 ms
        output_dir: str = OUTPUT_DIR,
    ):
        ensure_ns_client("ns_client")
        self.duration = duration
        self.tau = tau
        self.interval = interval
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)

    def _schedule_hero_impairments(self, t_start: float, stop_event: threading.Event):
        """
        Background thread that executes the 20.0s Hero Trace timeline:
          t = 0.0s  -> Nominal baseline (25ms router netem = 50ms RTT)
          t = 5.0s  -> Transient Spike (63ms router netem = 88ms RTT)
          t = 5.1s  -> Revert to Nominal baseline (25ms router netem = 50ms RTT)
          t = 10.0s -> Structural Step (70ms router netem = 95ms RTT, SLA breached)
        """
        events = [
            (5.0, "63ms", "[EVENT] Injecting transient spike (88ms RTT)"),
            (5.1, "25ms", "[EVENT] Reverting to nominal (50ms RTT)"),
            (10.0, "70ms", "[EVENT] Injecting structural step (95ms RTT - SLA breached)"),
        ]

        for target_rel_t, delay_val, desc in events:
            while not stop_event.is_set():
                now_rel = time.perf_counter() - t_start
                remaining = target_rel_t - now_rel
                if remaining <= 0.001:
                    set_router_channel(delay_ms=delay_val)
                    break
                time.sleep(min(0.01, remaining / 2.0))

    def run_single_policy(self, policy: PolicyType, verbose: bool = True) -> List[Dict[str, Any]]:
        """Run single Hero Trace test for specified policy."""
        # Reset testbed Link B to baseline nominal
        set_router_channel(delay_ms="25ms")

        controller = SteeringController(
            policy=policy,
            tau=self.tau,
            window_size=3,
            persistence=2,
            interval=self.interval,
            initial_link="B",
            dual_probing=True,
        )

        stop_event = threading.Event()
        t_start = time.perf_counter()

        # Start impairment scheduler thread
        scheduler_thread = threading.Thread(
            target=self._schedule_hero_impairments,
            args=(t_start, stop_event),
            daemon=True,
        )
        scheduler_thread.start()

        csv_file = os.path.join(self.output_dir, f"hero_trace_policy_{int(policy)}.csv")

        try:
            records = controller.run(
                duration=self.duration,
                stop_on_trigger=False,
                verbose=verbose,
                log_file=csv_file,
            )
        finally:
            stop_event.set()
            controller.close()
            # Clean up testbed back to nominal
            set_router_channel(delay_ms="25ms")

        return records

    def run_all(self, verbose: bool = True) -> Dict[int, Dict[str, Any]]:
        """Execute Hero Trace sequentially across all four policies."""
        summaries = {}
        policies = [
            PolicyType.STRICT_RAW,
            PolicyType.RAW_HYSTERESIS,
            PolicyType.ROLLING_WINDOW,
            PolicyType.WINDOW_HYSTERESIS,
        ]

        print("=" * 80)
        print("STARTING DETERMINISTIC HERO TRACE BENCHMARK SUITE (20.0s Timeline)")
        print(f"   Timeline: 20.0s | Transient Burst: t=5.0-5.1s (88ms) | Structural Step: t=10.0s (95ms)")
        print(f"   Output Directory: {self.output_dir}")
        print("=" * 80)

        for pol in policies:
            print(f"\n[RUN] Running Hero Trace: Policy {pol.value} ({pol.name})...")
            records = self.run_single_policy(pol, verbose=verbose)

            # Analyze key events
            handovers = [r for r in records if r["handover_executed"]]
            first_switch = handovers[0] if handovers else None

            # Check transient spike trigger (did it switch at t < 9.0s?)
            transient_false_alarm = any(r["handover_executed"] and r["rel_time_s"] < 9.0 for r in records)
            
            # Detection lag from structural step (t_step = 10.0s)
            structural_switch = next((r for r in handovers if r["rel_time_s"] >= 9.9), None)
            detection_lag_ms = None
            if structural_switch:
                detection_lag_ms = (structural_switch["rel_time_s"] - 10.0) * 1000.0

            summaries[pol.value] = {
                "policy_name": pol.name,
                "total_ticks": len(records),
                "handover_count": len(handovers),
                "transient_false_alarm": transient_false_alarm,
                "first_switch_time_s": first_switch["rel_time_s"] if first_switch else None,
                "first_switch_latency_ms": first_switch["switch_latency_ms"] if first_switch else None,
                "structural_detection_lag_ms": detection_lag_ms,
                "csv_path": os.path.join(self.output_dir, f"hero_trace_policy_{pol.value}.csv"),
            }

            time.sleep(0.5)

        print("\n" + "=" * 80)
        print("HERO TRACE DETERMINISTIC BENCHMARK SUMMARY (20.0s Timeline)")
        print("=" * 80)
        print(f"{'Policy':<22} | {'Transient Spike':<18} | {'First Trigger (s)':<18} | {'Detection Lag':<15}")
        print("-" * 80)
        for pol_id, s in summaries.items():
            spike_status = "False Trigger" if s["transient_false_alarm"] else "Filtered"
            trig_str = f"{s['first_switch_time_s']:.3f} s" if s['first_switch_time_s'] else "None"
            lag_str = f"{s['structural_detection_lag_ms']:.1f} ms" if s['structural_detection_lag_ms'] else "N/A (Early switch)"
            print(f"Policy {pol_id} ({s['policy_name'][:12]}..) | {spike_status:<18} | {trig_str:<18} | {lag_str:<15}")
        print("=" * 80)

        return summaries


def run_hero_trace_all():
    runner = HeroTraceRunner(duration=20.0)
    return runner.run_all(verbose=True)


def main():
    parser = argparse.ArgumentParser(description="Deterministic Hero Trace Benchmark Runner")
    parser.add_argument("--policy", "-p", default="all", help="Policy (1, 2, 3, 4, or 'all')")
    parser.add_argument("--duration", "-d", type=float, default=20.0, help="Timeline duration in seconds (default: 20.0s)")
    parser.add_argument("--tau", type=float, default=65.0, help="SLA latency ceiling in ms")
    parser.add_argument("--quiet", action="store_true", help="Suppress per-tick stdout")

    args = parser.parse_args()
    runner = HeroTraceRunner(duration=args.duration, tau=args.tau)

    if args.policy.lower() == "all":
        runner.run_all(verbose=not args.quiet)
    else:
        pol = PolicyType.from_str(args.policy)
        runner.run_single_policy(pol, verbose=not args.quiet)


if __name__ == "__main__":
    main()
