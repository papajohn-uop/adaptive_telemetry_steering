#!/usr/bin/env python3
"""
phase3_eval/run_stochastic_eval.py

Part B: Randomized Stochastic Stress-Testing Suite
Evaluates statistical stability and operational boundaries across automated trials
(N = 100 independent runs per policy) under combined stochastic wireless channel dynamics
across a 20.0-second timeline:
  - Background Gaussian jitter: N(50 ms, 2.5^2)
  - Poisson noise micro-bursts: arrival rate lambda = 0.5 s^-1, duration 50-80 ms, amplitude 28-42 ms
  - Random structural step: onset uniformly distributed t_step ~ U[5.0, 9.0]s, elevating RTT permanently by +40 ms (to ~95.5 ms, violating tau = 65 ms)

Aggregates:
  1. Route Flapping Rate (handovers per run)
  2. False Positive Ratio (% of runs triggering before t_step)
  3. SLA Violation Detection Lag (T_detect = t_switch - t_step in ms)
  4. Cumulative SLA Violation Duration (ms)

Saves results to:
  - ANALYSIS_RESULTS/PHASE3/stochastic_trials.csv
  - ANALYSIS_RESULTS/PHASE3/stochastic_summary.json
"""

import os
import sys
import time
import json
import random
import threading
import argparse
import subprocess
import numpy as np
from typing import Dict, Any, List, Optional

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


OUTPUT_DIR = os.path.join(REPO_ROOT, "data", "stochastic")


def set_router_channel_direct(delay_ms: str = "25ms", jitter_ms: str = "0ms", loss_pct: str = "0%"):
    """Update tc netem qdisc on ns_router veth_router_b."""
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

    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class StochasticRunner:
    """
    Orchestrates the stochastic stress test across a 20.0s timeline.
    Injects stochastic Poisson noise and random structural step per trial.
    """

    def __init__(
        self,
        runs_per_policy: int = 30,
        run_duration: float = 20.0,
        tau: float = 65.0,
        interval: float = 0.1,
        seed: int = 42,
        output_dir: str = OUTPUT_DIR,
    ):
        ensure_ns_client("ns_client")
        self.runs_per_policy = runs_per_policy
        self.run_duration = run_duration
        self.tau = tau
        self.interval = interval
        self.seed = seed
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)
        random.seed(seed)
        np.random.seed(seed)

    def _generate_trial_profile(self) -> Dict[str, Any]:
        """Generate random Poisson bursts and random structural step onset across 20s timeline."""
        # 1. Structural step onset t_step ~ U[5.0, 9.0]s
        t_step = random.uniform(5.0, 9.0)

        # 2. Poisson noise micro-bursts (arrival rate lambda = 0.5 s^-1)
        # Generate arrivals in window [0.5, t_step - 0.2]
        bursts = []
        t_cur = 0.5
        while t_cur < (t_step - 0.2):
            interval = np.random.exponential(1.0 / 0.5)
            t_cur += interval
            if t_cur < (t_step - 0.2):
                duration = random.uniform(0.05, 0.08)  # 50-80 ms (strictly sub-polling tick, true transient micro-burst)
                amplitude = random.uniform(28.0, 42.0)  # +28 to +42 ms (RTT reaches 78-92 ms, matching Hero Trace)
                bursts.append({
                    "start": t_cur,
                    "end": t_cur + duration,
                    "amplitude": amplitude,
                })

        return {
            "t_step": t_step,
            "bursts": bursts,
        }

    def _channel_scheduler(
        self,
        profile: Dict[str, Any],
        t_start: float,
        stop_event: threading.Event,
    ):
        """
        Dynamically applies stochastic channel profile on ns_router:
          - Default: 25ms delay, 2.5ms jitter (yields ~50ms RTT with +-5ms jitter)
          - During burst: delay increases by amplitude
          - At t >= t_step: structural step (delay increases permanently by +40ms)
        """
        # Baseline cellular: 25ms delay, 2.5ms jitter, 0.5% loss
        set_router_channel_direct("25ms", "2.5ms", "0.5%")
        t_step = profile["t_step"]
        bursts = sorted(profile["bursts"], key=lambda b: b["start"])

        in_step = False
        active_burst = None

        while not stop_event.is_set():
            now_rel = time.perf_counter() - t_start
            if now_rel >= self.run_duration:
                break

            # Check structural step
            if not in_step and now_rel >= t_step:
                in_step = True
                # Permanent +40ms step (router delay becomes 65ms -> RTT ~90ms)
                set_router_channel_direct("65ms", "2.5ms", "0.5%")

            elif not in_step:
                # Check if inside any transient burst
                current_burst = None
                for b in bursts:
                    if b["start"] <= now_rel <= b["end"]:
                        current_burst = b
                        break

                if current_burst is not None and active_burst is None:
                    active_burst = current_burst
                    burst_delay = 25.0 + (current_burst["amplitude"] / 2.0)
                    set_router_channel_direct(f"{burst_delay:.1f}ms", "2.5ms", "0.5%")

                elif current_burst is None and active_burst is not None:
                    active_burst = None
                    set_router_channel_direct("25ms", "2.5ms", "0.5%")

            time.sleep(0.015)

    def run_trial(
        self,
        policy: PolicyType,
        trial_idx: int,
        profile: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Execute single stochastic trial."""
        t_step = profile["t_step"]
        controller = SteeringController(
            policy=policy,
            tau=self.tau,
            window_size=3,
            persistence=2,
            interval=self.interval,
            initial_link="B",
            dual_probing=False,
        )

        stop_event = threading.Event()
        t_start = time.perf_counter()

        scheduler = threading.Thread(
            target=self._channel_scheduler,
            args=(profile, t_start, stop_event),
            daemon=True,
        )
        scheduler.start()

        # Early stop callback: exit once post-step switch has been recorded
        # or after t_step + 1.5s has passed
        early_stop_called = False
        def check_early_stop(tick_idx, rel_time_s, tick_rec):
            nonlocal early_stop_called
            if rel_time_s >= (t_step + 1.5):
                early_stop_called = True
            elif rel_time_s >= t_step and tick_rec["handover_executed"]:
                early_stop_called = True

        try:
            records = []
            controller.reset_state("B")
            t_start = time.perf_counter()
            tick_idx = 0
            while True:
                loop_start = time.perf_counter()
                rel_time = loop_start - t_start
                if rel_time >= self.run_duration or early_stop_called:
                    break

                rec = controller.step(tick_idx, t_start)
                records.append(rec)
                check_early_stop(tick_idx, rel_time, rec)

                tick_idx += 1
                elapsed = time.perf_counter() - loop_start
                sleep_time = max(0.0, self.interval - elapsed)
                time.sleep(sleep_time)
        finally:
            stop_event.set()
            controller.close()
            # Reset router back to nominal
            set_router_channel_direct("25ms", "0ms", "0%")

        # Evaluate performance metrics
        handovers = [r for r in records if r["handover_executed"]]
        handover_count = len(handovers)

        # False alarm: any handover triggered before t_step
        false_alarms = [r for r in handovers if r["rel_time_s"] < (t_step - 0.05)]
        has_false_alarm = len(false_alarms) > 0

        # Detection lag: first handover occurring after t_step
        post_step_handovers = [r for r in handovers if r["rel_time_s"] >= (t_step - 0.05)]
        if post_step_handovers:
            first_post_switch = post_step_handovers[0]
            detection_lag_ms = max(0.0, (first_post_switch["rel_time_s"] - t_step) * 1000.0)
            switch_latency_ms = first_post_switch["switch_latency_ms"]
        elif has_false_alarm:
            # Already switched prematurely
            detection_lag_ms = 0.0
            switch_latency_ms = false_alarms[0]["switch_latency_ms"]
        else:
            detection_lag_ms = (self.run_duration - t_step) * 1000.0
            switch_latency_ms = 0.0

        # Cumulative SLA violation duration
        sla_viol_ticks = sum(1 for r in records if r["rtt_sample"] and r["rtt_sample"] > self.tau)
        sla_viol_duration_ms = sla_viol_ticks * (self.interval * 1000.0)

        return {
            "policy": int(policy),
            "policy_name": policy.name,
            "trial_idx": trial_idx,
            "t_step": t_step,
            "burst_count": len(profile["bursts"]),
            "handover_count": handover_count,
            "false_alarm": has_false_alarm,
            "detection_lag_ms": detection_lag_ms,
            "sla_viol_duration_ms": sla_viol_duration_ms,
            "switch_latency_ms": switch_latency_ms,
        }

    def run_suite(self) -> Dict[str, Any]:
        """Run all 120 trials across the 4 policies."""
        policies = [
            PolicyType.STRICT_RAW,
            PolicyType.RAW_HYSTERESIS,
            PolicyType.ROLLING_WINDOW,
            PolicyType.WINDOW_HYSTERESIS,
        ]

        all_results = []
        csv_file = os.path.join(self.output_dir, "stochastic_trials.csv")
        json_file = os.path.join(self.output_dir, "stochastic_summary.json")

        print("=" * 80)
        print("STARTING RANDOMIZED STOCHASTIC STRESS-TESTING SUITE (20.0s Timeline)")
        print(f"   {self.runs_per_policy} Runs/Policy x 4 Policies = {self.runs_per_policy * 4} Total Trials")
        print(f"   Output Files: {csv_file} | {json_file}")
        print("=" * 80)

        # Pre-generate identical random profiles for fair comparative evaluation
        profiles = [self._generate_trial_profile() for _ in range(self.runs_per_policy)]

        for pol in policies:
            print(f"\n[RUN] Running Policy {pol.value} ({pol.name}) [{self.runs_per_policy} runs]...")
            for idx in range(self.runs_per_policy):
                prof = profiles[idx]
                res = self.run_trial(pol, idx, prof)
                all_results.append(res)
                fa_mark = "[FA]" if res["false_alarm"] else "[OK]"
                sys.stdout.write(f"\r  Trial {idx+1}/{self.runs_per_policy}: Handovers={res['handover_count']}, Lag={res['detection_lag_ms']:.1f}ms {fa_mark}   ")
                sys.stdout.flush()
            print()

        # Save all individual trial records to CSV
        fieldnames = list(all_results[0].keys())
        with open(csv_file, "w") as f:
            f.write(",".join(fieldnames) + "\n")
            for r in all_results:
                row = [str(r[k]) for k in fieldnames]
                f.write(",".join(row) + "\n")

        # Compute statistical summary
        summary = {}
        for pol in policies:
            pol_id = int(pol)
            trials = [r for r in all_results if r["policy"] == pol_id]
            handovers = [r["handover_count"] for r in trials]
            false_alarms = [1.0 if r["false_alarm"] else 0.0 for r in trials]
            lags = [r["detection_lag_ms"] for r in trials]
            sla_viols = [r["sla_viol_duration_ms"] for r in trials]
            switch_lats = [r["switch_latency_ms"] for r in trials if r["switch_latency_ms"] > 0]

            summary[pol.name] = {
                "policy_id": pol_id,
                "n_runs": len(trials),
                "false_alarm_ratio_pct": float(np.mean(false_alarms) * 100.0),
                "false_alarm_ratio_std": float(np.std(false_alarms) * 100.0),
                "handovers_per_run_mean": float(np.mean(handovers)),
                "handovers_per_run_std": float(np.std(handovers)),
                "detection_lag_mean_ms": float(np.mean(lags)),
                "detection_lag_std_ms": float(np.std(lags)),
                "sla_viol_duration_mean_ms": float(np.mean(sla_viols)),
                "sla_viol_duration_std_ms": float(np.std(sla_viols)),
                "switch_latency_mean_ms": float(np.mean(switch_lats)) if switch_lats else 0.0,
            }

        with open(json_file, "w") as f:
            json.dump(summary, f, indent=2)

        print("\n" + "=" * 80)
        print(f"STOCHASTIC STRESS-TESTING AGGREGATE SUMMARY (N = {self.runs_per_policy} Runs/Policy)")
        print("=" * 80)
        print(f"{'Policy':<22} | {'False Handover (%)':<20} | {'Detection Delay (ms)':<22} | {'Handovers/Run':<15}")
        print("-" * 80)
        for pol_name, s in summary.items():
            fa_str = f"{s['false_alarm_ratio_pct']:.1f} ± {s['false_alarm_ratio_std']:.1f}%"
            lag_str = f"{s['detection_lag_mean_ms']:.1f} ± {s['detection_lag_std_ms']:.1f} ms"
            ho_str = f"{s['handovers_per_run_mean']:.2f} ± {s['handovers_per_run_std']:.2f}"
            print(f"{pol_name:<22} | {fa_str:<20} | {lag_str:<22} | {ho_str:<15}")
        print("=" * 80)

        return summary


def run_stochastic_suite(runs_per_policy: int = 100):
    runner = StochasticRunner(runs_per_policy=runs_per_policy, run_duration=20.0)
    return runner.run_suite()


def main():
    parser = argparse.ArgumentParser(description="Randomized Stochastic Stress-Testing Benchmark Runner")
    parser.add_argument("--runs", "-n", type=int, default=100, help="Runs per policy (default: 100)")
    parser.add_argument("--duration", "-d", type=float, default=20.0, help="Run duration in seconds (default: 20.0s)")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducibility")

    args = parser.parse_args()
    runner = StochasticRunner(runs_per_policy=args.runs, run_duration=args.duration, seed=args.seed)
    runner.run_suite()


if __name__ == "__main__":
    main()
