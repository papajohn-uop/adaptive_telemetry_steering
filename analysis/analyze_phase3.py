#!/usr/bin/env python3
"""
phase3_eval/analyze_phase3.py

Publication-Ready Statistical Aggregator & Figure Generator for Phase 3 (20.0s Timeline):
  1. plots/hero_trace_comparative.png & .pdf (Figure 3 in Conference Paper)
     - 4-panel synchronized comparative latency trace (Transient Spike & Structural Step)
  2. plots/stochastic_tradeoff_compact_table.png & .pdf (Figure 4 in Conference Paper)
     - Detection Lag vs. False Handover Ratio Pareto Frontier with embedded table
  3. plots/stochastic_boxplots.png & .pdf
     - Flapping Frequency & SLA Violation Detection Lag Boxplots
  4. Generates LaTeX / Markdown empirical tables (Table V & Table VI)
"""

import os
import sys
import json
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# Paths configuration
CUR_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(CUR_DIR)
HERO_DATA_DIR = os.path.join(ROOT_DIR, "data", "hero_trace")
STOCHASTIC_DATA_DIR = os.path.join(ROOT_DIR, "data", "stochastic")
PLOTS_DIR = os.path.join(ROOT_DIR, "figures")
os.makedirs(PLOTS_DIR, exist_ok=True)

def resolve_data_path(filename: str, preferred_dir: str = None) -> str:
    candidates = []
    if preferred_dir:
        candidates.append(preferred_dir)
    candidates.extend([
        STOCHASTIC_DATA_DIR,
        HERO_DATA_DIR,
        os.path.join(ROOT_DIR, "ANALYSIS_RESULTS", "PHASE3"),
    ])
    for d in candidates:
        p = os.path.join(d, filename)
        if os.path.exists(p):
            return p
    return os.path.join(preferred_dir or HERO_DATA_DIR, filename)

RESULTS_DIR = HERO_DATA_DIR


def plot_hero_trace_5panel(results_dir: str = RESULTS_DIR, plots_dir: str = PLOTS_DIR):
    """
    Generate 5-Panel Hero Trace Figure:
      - Subplot 1: Link latency dynamics (Raw RTT, Rolling Mean, EWMA SRTT, SLA ceiling, events)
      - Subplot 2: Policy 1 (Strict Raw, K=1)
      - Subplot 3: Policy 2 (Raw + Hysteresis, K=2)
      - Subplot 4: Policy 3 (Rolling Window Average, N=3)
      - Subplot 5: Policy 4 (Window + Hysteresis, N=3, K=2)
    """
    p1_file = os.path.join(results_dir, "hero_trace_policy_1.csv")
    p2_file = os.path.join(results_dir, "hero_trace_policy_2.csv")
    p3_file = os.path.join(results_dir, "hero_trace_policy_3.csv")
    p4_file = os.path.join(results_dir, "hero_trace_policy_4.csv")

    if not all(os.path.exists(f) for f in [p1_file, p2_file, p3_file, p4_file]):
        print("[WARN] One or more hero_trace CSV files missing. Skipping 5-panel plot.")
        return

    df1 = pd.read_csv(p1_file)
    df2 = pd.read_csv(p2_file)
    df3 = pd.read_csv(p3_file)
    df4 = pd.read_csv(p4_file)

    fig, axes = plt.subplots(5, 1, figsize=(11, 13.0), sharex=True)
    plt.subplots_adjust(hspace=0.28)

    sla_tau = 65.0
    c_blue = "#1f77b4"
    c_green = "#2ca02c"
    c_orange = "#ff7f0e"
    c_purple = "#9467bd"
    c_red = "#d62728"

    # ==================== SUBPLOT 1: Channel Scenario ====================
    ax1 = axes[0]
    ax1.plot(df2["rel_time_s"], df2["rtt_sample"], color="#4a86e8", alpha=0.45, label="Raw Sample $RTT(t)$", lw=1.2)
    ax1.plot(df2["rel_time_s"], df2["rolling_mean"], color=c_blue, lw=2.0, label="Sliding Window Mean $\\mu_{\\mathcal{W}}$ ($N=3$)")
    ax1.plot(df2["rel_time_s"], df2["srtt"], color="#0b5394", lw=1.5, linestyle=":", label="Smoothed $SRTT$ (EWMA $\\alpha=0.125$)")
    ax1.axhline(sla_tau, color=c_red, linestyle="--", lw=1.8, label=f"SLA Ceiling $\\tau = {sla_tau:.0f}$ ms")
    ax1.axvspan(5.0, 5.1, color="#f4cccc", alpha=0.7, label="Transient Micro-Burst ($88$ ms)")
    ax1.axvspan(10.0, 20.0, color="#fce5cd", alpha=0.45, label="Structural Step ($95$ ms)")
    ax1.set_ylabel("RTT (ms)", fontsize=10.5, fontweight="bold")
    ax1.set_title("(a) Channel Scenario: 100ms Micro-Burst ($t=5.0$s) and Persistent Degradation ($t=10.0$s)", fontsize=11.5, fontweight="bold")
    ax1.set_ylim(0, 115)
    ax1.grid(True, linestyle="--", alpha=0.5)
    ax1.legend(loc="upper left", ncol=3, fontsize=8.5, framealpha=0.9)

    # ==================== SUBPLOT 2: Policy 1 ====================
    ax2 = axes[1]
    ax2.plot(df1["rel_time_s"], df1["rtt_sample"], color=c_blue, lw=1.8, label="Policy 1 (Strict Raw, $K=1$) Active RTT")
    ax2.axhline(sla_tau, color=c_red, linestyle="--", lw=1.2, alpha=0.7)
    ax2.axvspan(5.0, 5.1, color="#f4cccc", alpha=0.5)
    ax2.axvspan(10.0, 20.0, color="#fce5cd", alpha=0.3)
    p1_switch = df1[df1["handover_executed"] == True]
    if not p1_switch.empty:
        sw_t = p1_switch.iloc[0]["rel_time_s"]
        sw_rtt = p1_switch.iloc[0]["rtt_sample"]
        ax2.scatter([sw_t], [sw_rtt], color=c_red, s=120, zorder=5, marker="X", edgecolors="black", lw=1.5)
        ax2.annotate(
            f"[!] Premature False Trigger ($t={sw_t:.2f}$s)\nSwitches to Link A (4.3 ms)",
            xy=(sw_t, sw_rtt), xytext=(sw_t + 0.6, sw_rtt + 6),
            arrowprops=dict(facecolor=c_red, shrink=0.08, width=1.5, headwidth=6),
            fontsize=9, fontweight="bold", color=c_red,
            bbox=dict(boxstyle="round,pad=0.3", fc="#fdf2f2", ec=c_red, lw=1)
        )
    ax2.set_ylabel("RTT (ms)", fontsize=10.5, fontweight="bold")
    ax2.set_title("(b) Policy 1 (Strict Raw): Unprotected Single Sample Causes Route Flapping on Micro-Burst", fontsize=11, fontweight="bold")
    ax2.set_ylim(0, 115)
    ax2.grid(True, linestyle="--", alpha=0.5)
    ax2.legend(loc="upper right", fontsize=8.5)

    # ==================== SUBPLOT 3: Policy 2 ====================
    ax3 = axes[2]
    ax3.plot(df2["rel_time_s"], df2["rtt_sample"], color=c_green, lw=2.0, label="Policy 2 (Raw + Hysteresis, $K=2$) Active RTT")
    ax3.axhline(sla_tau, color=c_red, linestyle="--", lw=1.2, alpha=0.7)
    ax3.axvspan(5.0, 5.1, color="#f4cccc", alpha=0.5)
    ax3.axvspan(10.0, 20.0, color="#fce5cd", alpha=0.3)
    ax3.text(
        5.4, 78, "[+] Micro-Burst Absorbed\n($c_{\\mathrm{raw}}=1 \\to 0$, no switch)",
        fontsize=8.5, fontweight="bold", color="#1e4620",
        bbox=dict(boxstyle="round,pad=0.25", fc="#e6f4ea", ec=c_green, lw=1)
    )
    p2_switch = df2[df2["handover_executed"] == True]
    if not p2_switch.empty:
        sw_t = p2_switch.iloc[0]["rel_time_s"]
        sw_rtt = p2_switch.iloc[0]["rtt_sample"]
        lag_ms = int(max(0, (sw_t - 10.0) * 1000))
        ax3.scatter([sw_t], [sw_rtt], color=c_green, s=140, zorder=5, marker="*", edgecolors="black", lw=1.5)
        ax3.annotate(
            f"Policy 2 Trigger ($t={sw_t:.2f}$s, $T_{{\\mathrm{{detect}}}}={lag_ms}$ms)\nRe-Anchored to Link A",
            xy=(sw_t, sw_rtt), xytext=(sw_t + 0.6, sw_rtt - 24),
            arrowprops=dict(facecolor=c_green, shrink=0.08, width=1.5, headwidth=6),
            fontsize=9, fontweight="bold", color="#1e4620",
            bbox=dict(boxstyle="round,pad=0.3", fc="#e6f4ea", ec=c_green, lw=1)
        )
    ax3.set_ylabel("RTT (ms)", fontsize=10.5, fontweight="bold")
    ax3.set_title("(c) Policy 2 (Raw + Hysteresis, $K=2$): Temporal Confirmation Filters Micro-Burst and Triggers on Step", fontsize=11, fontweight="bold")
    ax3.set_ylim(0, 115)
    ax3.grid(True, linestyle="--", alpha=0.5)
    ax3.legend(loc="upper right", fontsize=8.5)

    # ==================== SUBPLOT 4: Policy 3 ====================
    ax4 = axes[3]
    ax4.plot(df3["rel_time_s"], df3["rtt_sample"], color=c_orange, lw=1.8, label="Policy 3 (Rolling Window Average, $N=3$) Active RTT")
    ax4.axhline(sla_tau, color=c_red, linestyle="--", lw=1.2, alpha=0.7)
    ax4.axvspan(5.0, 5.1, color="#f4cccc", alpha=0.5)
    ax4.axvspan(10.0, 20.0, color="#fce5cd", alpha=0.3)
    ax4.text(
        5.4, 78, "[+] Diluted by Window\n($\\mu_{\\mathcal{W}} = 63.3 < 65$ ms)",
        fontsize=8.5, fontweight="bold", color="#8a4200",
        bbox=dict(boxstyle="round,pad=0.25", fc="#fdf5e6", ec=c_orange, lw=1)
    )
    p3_switch = df3[df3["handover_executed"] == True]
    if not p3_switch.empty:
        sw_t = p3_switch.iloc[0]["rel_time_s"]
        sw_rtt = p3_switch.iloc[0]["rtt_sample"]
        lag_ms = int(max(0, (sw_t - 10.0) * 1000))
        ax4.scatter([sw_t], [sw_rtt], color=c_orange, s=110, zorder=5, marker="o", edgecolors="black", lw=1.5)
        ax4.annotate(
            f"Policy 3 Trigger ($t={sw_t:.2f}$s, $T_{{\\mathrm{{detect}}}}={lag_ms}$ms)",
            xy=(sw_t, sw_rtt), xytext=(sw_t + 0.6, sw_rtt - 22),
            arrowprops=dict(facecolor=c_orange, shrink=0.08, width=1.5, headwidth=6),
            fontsize=9, fontweight="bold", color="#8a4200",
            bbox=dict(boxstyle="round,pad=0.25", fc="#fdf5e6", ec=c_orange, lw=1)
        )
    ax4.set_ylabel("RTT (ms)", fontsize=10.5, fontweight="bold")
    ax4.set_title("(d) Policy 3 (Rolling Window Average): FIFO Smoothing Suppresses Noise with Moderate Reaction Lag", fontsize=11, fontweight="bold")
    ax4.set_ylim(0, 115)
    ax4.grid(True, linestyle="--", alpha=0.5)
    ax4.legend(loc="upper right", fontsize=8.5)

    # ==================== SUBPLOT 5: Policy 4 ====================
    ax5 = axes[4]
    ax5.plot(df4["rel_time_s"], df4["rtt_sample"], color=c_purple, lw=1.8, label="Policy 4 (Window + Hysteresis, $N=3, K=2$) Active RTT")
    ax5.axhline(sla_tau, color=c_red, linestyle="--", lw=1.2, alpha=0.7)
    ax5.axvspan(5.0, 5.1, color="#f4cccc", alpha=0.5)
    ax5.axvspan(10.0, 20.0, color="#fce5cd", alpha=0.3)
    p4_switch = df4[df4["handover_executed"] == True]
    if not p4_switch.empty:
        sw_t = p4_switch.iloc[0]["rel_time_s"]
        sw_rtt = p4_switch.iloc[0]["rtt_sample"]
        lag_ms = int(max(0, (sw_t - 10.0) * 1000))
        ax5.scatter([sw_t], [sw_rtt], color=c_purple, s=120, zorder=5, marker="s", edgecolors="black", lw=1.5)
        ax5.annotate(
            f"Policy 4 Trigger ($t={sw_t:.2f}$s, $T_{{\\mathrm{{detect}}}}={lag_ms}$ms)",
            xy=(sw_t, sw_rtt), xytext=(sw_t + 0.6, sw_rtt - 22),
            arrowprops=dict(facecolor=c_purple, shrink=0.08, width=1.5, headwidth=6),
            fontsize=9, fontweight="bold", color=c_purple,
            bbox=dict(boxstyle="round,pad=0.25", fc="#f3e8fd", ec=c_purple, lw=1)
        )
    ax5.set_xlabel("Elapsed Time (Seconds)", fontsize=11, fontweight="bold")
    ax5.set_ylabel("RTT (ms)", fontsize=10.5, fontweight="bold")
    ax5.set_title("(e) Policy 4 (Window + Hysteresis): Maximum Conservative Filtering Resilience", fontsize=11, fontweight="bold")
    ax5.set_ylim(0, 115)
    ax5.set_xlim(0, 20.0)
    ax5.grid(True, linestyle="--", alpha=0.5)
    ax5.legend(loc="upper right", fontsize=8.5)

    png_path = os.path.join(plots_dir, "hero_trace_5panel.png")
    pdf_path = os.path.join(plots_dir, "hero_trace_5panel.pdf")
    plt.savefig(png_path, dpi=300, bbox_inches="tight")
    plt.savefig(pdf_path, bbox_inches="tight")
    plt.close()

    print(f"[SUCCESS] Generated 5-Panel Hero Trace: {png_path} and {pdf_path}")


def plot_hero_trace_compact(results_dir: str = RESULTS_DIR, plots_dir: str = PLOTS_DIR):
    """
    Generate 1-Plot Compact Unified Hero Trace:
      - Overlays all 4 policies on a single space-saving figure with the channel baseline.
      - Uses distinct line styles, colors, and trigger markers to avoid clutter.
    """
    p1_file = os.path.join(results_dir, "hero_trace_policy_1.csv")
    p2_file = os.path.join(results_dir, "hero_trace_policy_2.csv")
    p3_file = os.path.join(results_dir, "hero_trace_policy_3.csv")
    p4_file = os.path.join(results_dir, "hero_trace_policy_4.csv")

    if not all(os.path.exists(f) for f in [p1_file, p2_file, p3_file, p4_file]):
        print("[WARN] One or more hero_trace CSV files missing. Skipping compact plot.")
        return

    df1 = pd.read_csv(p1_file)
    df2 = pd.read_csv(p2_file)
    df3 = pd.read_csv(p3_file)
    df4 = pd.read_csv(p4_file)

    fig, ax = plt.subplots(figsize=(10.5, 5.8))

    sla_tau = 65.0
    c_blue = "#1f77b4"
    c_green = "#2ca02c"
    c_orange = "#ff7f0e"
    c_purple = "#9467bd"
    c_red = "#d62728"

    # Background shaded event regions
    ax.axvspan(5.0, 5.1, color="#f4cccc", alpha=0.75, label="Transient Micro-Burst ($88$ ms, 100ms)")
    ax.axvspan(10.0, 20.0, color="#fce5cd", alpha=0.45, label="Persistent Structural Step ($95$ ms)")
    ax.axhline(sla_tau, color=c_red, linestyle="--", lw=1.8, label=f"SLA Ceiling $\\tau = {sla_tau:.0f}$ ms")


    # Plot 4 Policy Trajectories with distinct line styles
    ax.plot(df1["rel_time_s"], df1["rtt_sample"], color=c_blue, lw=1.8, linestyle="-", label="Policy 1 (Strict Raw, $K=1$)")
    ax.plot(df2["rel_time_s"], df2["rtt_sample"], color=c_green, lw=2.4, linestyle="-", label="Policy 2 (Raw + Hysteresis, $K=2$)")
    ax.plot(df3["rel_time_s"], df3["rtt_sample"], color=c_orange, lw=1.8, linestyle="-.", label="Policy 3 (Rolling Window Average, $N=3$)")
    ax.plot(df4["rel_time_s"], df4["rtt_sample"], color=c_purple, lw=1.8, linestyle="--", label="Policy 4 (Window + Hysteresis, $N=3, K=2$)")

    # Policy 1 False Trigger Marker
    p1_switch = df1[df1["handover_executed"] == True]
    if not p1_switch.empty:
        sw_t = p1_switch.iloc[0]["rel_time_s"]
        sw_rtt = p1_switch.iloc[0]["rtt_sample"]
        ax.scatter([sw_t], [sw_rtt], color=c_red, s=130, zorder=6, marker="X", edgecolors="black", lw=1.5)
        ax.annotate(
            f"[!] P1 False Trigger ($t={sw_t:.2f}$s)\nPremature Switch to Link A",
            xy=(sw_t, sw_rtt), xytext=(sw_t - 4.5, 98),
            arrowprops=dict(facecolor=c_red, shrink=0.08, width=1.5, headwidth=6),
            fontsize=8.8, fontweight="bold", color=c_red,
            bbox=dict(boxstyle="round,pad=0.25", fc="#fdf2f2", ec=c_red, lw=1.2)
        )

    # Note that Policies 2, 3, 4 absorbed the micro-burst
    ax.text(
        5.4, 73, "[+] Micro-Burst Filtered\n(Policies 2, 3, 4 remain on B)",
        fontsize=8.2, fontweight="bold", color="#1e4620",
        bbox=dict(boxstyle="round,pad=0.25", fc="#e6f4ea", ec=c_green, lw=1)
    )

    # Policy 2 Trigger Marker
    p2_switch = df2[df2["handover_executed"] == True]
    if not p2_switch.empty:
        sw_t = p2_switch.iloc[0]["rel_time_s"]
        sw_rtt = p2_switch.iloc[0]["rtt_sample"]
        lag_ms = int(max(0, (sw_t - 10.0) * 1000))
        ax.scatter([sw_t], [sw_rtt], color=c_green, s=160, zorder=6, marker="*", edgecolors="black", lw=1.5)
        ax.annotate(
            f"P2 Trigger ($t={sw_t:.2f}$s, $T={lag_ms}$ms)\nRe-Anchored to Link A",
            xy=(sw_t, sw_rtt), xytext=(sw_t + 1.2, 92),
            arrowprops=dict(facecolor=c_green, shrink=0.08, width=1.5, headwidth=6),
            fontsize=8.8, fontweight="bold", color="#1e4620",
            bbox=dict(boxstyle="round,pad=0.25", fc="#e6f4ea", ec=c_green, lw=1.2)
        )

    # Policy 3 Trigger Marker
    p3_switch = df3[df3["handover_executed"] == True]
    if not p3_switch.empty:
        sw_t = p3_switch.iloc[0]["rel_time_s"]
        sw_rtt = p3_switch.iloc[0]["rtt_sample"]
        lag_ms = int(max(0, (sw_t - 10.0) * 1000))
        ax.scatter([sw_t], [sw_rtt], color=c_orange, s=110, zorder=6, marker="o", edgecolors="black", lw=1.5)
        ax.annotate(
            f"P3 Trigger ($t={sw_t:.2f}$s, $T={lag_ms}$ms)",
            xy=(sw_t, sw_rtt), xytext=(sw_t + 1.2, 60),
            arrowprops=dict(facecolor=c_orange, shrink=0.08, width=1.5, headwidth=6),
            fontsize=8.5, fontweight="bold", color="#8a4200",
            bbox=dict(boxstyle="round,pad=0.22", fc="#fdf5e6", ec=c_orange, lw=1)
        )

    # Policy 4 Trigger Marker
    p4_switch = df4[df4["handover_executed"] == True]
    if not p4_switch.empty:
        sw_t = p4_switch.iloc[0]["rel_time_s"]
        sw_rtt = p4_switch.iloc[0]["rtt_sample"]
        lag_ms = int(max(0, (sw_t - 10.0) * 1000))
        ax.scatter([sw_t], [sw_rtt], color=c_purple, s=110, zorder=6, marker="s", edgecolors="black", lw=1.5)
        ax.annotate(
            f"P4 Trigger ($t={sw_t:.2f}$s, $T={lag_ms}$ms)",
            xy=(sw_t, sw_rtt), xytext=(sw_t + 1.2, 30),
            arrowprops=dict(facecolor=c_purple, shrink=0.08, width=1.5, headwidth=6),
            fontsize=8.5, fontweight="bold", color=c_purple,
            bbox=dict(boxstyle="round,pad=0.22", fc="#f3e8fd", ec=c_purple, lw=1)
        )

    ax.set_xlabel("Elapsed Time (Seconds)", fontsize=11, fontweight="bold")
    ax.set_ylabel("Active Path RTT (ms)", fontsize=11, fontweight="bold")
    ax.set_title("Synchronized Flow Steering Evaluation across Policies 1-4 (20.0s Timeline)", fontsize=12, fontweight="bold")
    ax.set_xlim(0, 20.0)
    ax.set_ylim(0, 135)
    ax.grid(True, linestyle="--", alpha=0.5)
    ax.legend(loc="upper right", ncol=2, fontsize=8.5, framealpha=0.92)

    png_path = os.path.join(plots_dir, "hero_trace_compact.png")
    pdf_path = os.path.join(plots_dir, "hero_trace_compact.pdf")
    plt.savefig(png_path, dpi=300, bbox_inches="tight")
    plt.savefig(pdf_path, bbox_inches="tight")
    plt.close()

    print(f"[SUCCESS] Generated 1-Plot Compact Hero Trace: {png_path} and {pdf_path}")


def plot_hero_trace(results_dir: str = RESULTS_DIR, plots_dir: str = PLOTS_DIR):
    """Generate both 5-panel and compact Hero Trace figures."""
    plot_hero_trace_5panel(results_dir, plots_dir)
    plot_hero_trace_compact(results_dir, plots_dir)
    # Also save the 5-panel as hero_trace_comparative for full reference
    src_pdf = os.path.join(plots_dir, "hero_trace_5panel.pdf")
    dst_pdf = os.path.join(plots_dir, "hero_trace_comparative.pdf")
    src_png = os.path.join(plots_dir, "hero_trace_5panel.png")
    dst_png = os.path.join(plots_dir, "hero_trace_comparative.png")
    if os.path.exists(src_pdf):
        import shutil
        shutil.copyfile(src_pdf, dst_pdf)
        shutil.copyfile(src_png, dst_png)


def plot_stochastic_tradeoff_clean(results_dir: str = RESULTS_DIR, plots_dir: str = PLOTS_DIR):
    """
    Generate Clean Trade-Off Plot (NO error bars):
    Plots Detection Lag (X-axis) vs. False Handover Ratio (Y-axis).
    """
    json_file = resolve_data_path("stochastic_summary.json", results_dir)
    if not os.path.exists(json_file):
        print("[WARN] stochastic_summary.json not found. Skipping plot.")
        return

    with open(json_file, "r") as f:
        summary = json.load(f)

    policies = ["STRICT_RAW", "RAW_HYSTERESIS", "ROLLING_WINDOW", "WINDOW_HYSTERESIS"]
    names = ["P1 (Strict Raw)", "P2 (Raw + Hyst)", "P3 (Rolling Window)", "P4 (Win + Hyst)"]
    colors = ["#1f77b4", "#2ca02c", "#ff7f0e", "#9467bd"]
    markers = ["X", "*", "o", "s"]
    sizes = [130, 200, 120, 120]

    lags = [summary[p]["detection_lag_mean_ms"] for p in policies]
    fa_rates = [summary[p]["false_alarm_ratio_pct"] for p in policies]
    n_runs = summary[policies[0]]["n_runs"]

    plt.figure(figsize=(5.8, 3.8))
    ax = plt.gca()

    # Plot policy points as clean scatter markers without error bars
    for i in range(4):
        plt.scatter(
            [lags[i]], [fa_rates[i]],
            marker=markers[i], color=colors[i], s=sizes[i],
            edgecolors="black", linewidth=1.2,
            label=f"{names[i]} ({fa_rates[i]:.1f}%, {lags[i]:.1f}ms)",
            zorder=3
        )

    plt.xlabel(r"SLA Violation Detection Lag $T_{\mathrm{detect}}$ (ms)", fontsize=10, fontweight="bold")
    plt.ylabel("False Handover Ratio (%)", fontsize=10, fontweight="bold")
    plt.title(f"Empirical Trade-Off: Detection Lag vs. False Handover ($N={n_runs}$)", fontsize=10.5, fontweight="bold")
    plt.grid(True, linestyle="--", alpha=0.5)
    plt.legend(loc="upper right", fontsize=8.5, framealpha=0.95)
    plt.xlim(-8, 165)
    plt.ylim(-5, 102)

    plt.tight_layout()
    clean_png = os.path.join(plots_dir, "stochastic_tradeoff_clean.png")
    clean_pdf = os.path.join(plots_dir, "stochastic_tradeoff_clean.pdf")
    compact_png = os.path.join(plots_dir, "stochastic_tradeoff_compact.png")
    compact_pdf = os.path.join(plots_dir, "stochastic_tradeoff_compact.pdf")
    pareto_png = os.path.join(plots_dir, "stochastic_pareto.png")
    pareto_pdf = os.path.join(plots_dir, "stochastic_pareto.pdf")

    for p in [clean_png, compact_png, pareto_png]:
        plt.savefig(p, dpi=300)
    for p in [clean_pdf, compact_pdf, pareto_pdf]:
        plt.savefig(p)
    plt.close()

    print(f"[SUCCESS] Generated Compact Trade-Off plot: {compact_png} and {compact_pdf}")


def plot_stochastic_tradeoff_with_table(results_dir: str = RESULTS_DIR, plots_dir: str = PLOTS_DIR):
    """
    Generate Trade-Off Plot with Detailed Legend Table:
    Features clean points and an embedded legend table showing exact Mean +- Std values.
    """
    json_file = resolve_data_path("stochastic_summary.json", results_dir)
    if not os.path.exists(json_file):
        return

    with open(json_file, "r") as f:
        summary = json.load(f)

    policies = ["STRICT_RAW", "RAW_HYSTERESIS", "ROLLING_WINDOW", "WINDOW_HYSTERESIS"]
    names = ["P1 (Strict Raw)", "P2 (Raw + Hyst)", "P3 (Rolling Window)", "P4 (Win + Hyst)"]
    colors = ["#1f77b4", "#2ca02c", "#ff7f0e", "#9467bd"]
    markers = ["X", "*", "o", "s"]
    sizes = [130, 200, 120, 120]

    lags = [summary[p]["detection_lag_mean_ms"] for p in policies]
    lags_std = [summary[p]["detection_lag_std_ms"] for p in policies]
    fa_rates = [summary[p]["false_alarm_ratio_pct"] for p in policies]
    fa_std = [summary[p]["false_alarm_ratio_std"] for p in policies]
    n_runs = summary[policies[0]]["n_runs"]

    fig, ax = plt.subplots(figsize=(7.4, 4.4))

    # Plot policy points
    for i in range(4):
        # Legend label formatted with exact table values
        lbl = f"{names[i]}:  False Alarm = {fa_rates[i]:.1f} ± {fa_std[i]:.1f}%,  Lag = {lags[i]:.1f} ± {lags_std[i]:.1f} ms"
        ax.scatter(
            [lags[i]], [fa_rates[i]],
            marker=markers[i], color=colors[i], s=sizes[i],
            edgecolors="black", linewidth=1.2,
            label=lbl,
            zorder=3
        )

    ax.set_xlabel(r"SLA Violation Detection Lag $T_{\mathrm{detect}}$ (ms)", fontsize=10, fontweight="bold")
    ax.set_ylabel("False Handover Ratio (%)", fontsize=10, fontweight="bold")
    ax.set_title(f"Empirical Trade-Off with Full Statistics ($N={n_runs}$ Runs/Policy)", fontsize=10.5, fontweight="bold")
    ax.grid(True, linestyle="--", alpha=0.5)

    # Beautiful tabular legend
    legend = ax.legend(
        loc="upper right",
        title="Evaluated Policies ($N=100$ Runs) [Mean ± Std]",
        title_fontsize=9.0,
        fontsize=8.2,
        framealpha=0.96,
        edgecolor="#cccccc"
    )
    legend.get_title().set_fontweight("bold")

    ax.set_xlim(-8, 165)
    ax.set_ylim(-5, 103)
    plt.tight_layout()

    table_png = os.path.join(plots_dir, "stochastic_tradeoff_with_table.png")
    table_pdf = os.path.join(plots_dir, "stochastic_tradeoff_with_table.pdf")
    leg_png = os.path.join(plots_dir, "stochastic_tradeoff_compact_legend_table.png")
    leg_pdf = os.path.join(plots_dir, "stochastic_tradeoff_compact_legend_table.pdf")

    for p in [table_png, leg_png]:
        plt.savefig(p, dpi=300)
    for p in [table_pdf, leg_pdf]:
        plt.savefig(p)
    plt.close()

    print(f"[SUCCESS] Generated Trade-Off plot with Table Legend: {table_png} and {table_pdf}")


def plot_stochastic_tradeoff_compact_table(results_dir: str = RESULTS_DIR, plots_dir: str = PLOTS_DIR):
    """
    Generate Compact Trade-Off Plot with an Embedded Summary Table:
    Features clean scatter points (no error bars, no dashed lines, no knee callout)
    and an embedded grid table in the upper-right quadrant displaying Mean +- Std for
    both False Alarm Ratio and Detection Delay.
    """
    json_file = resolve_data_path("stochastic_summary.json", results_dir)
    if not os.path.exists(json_file):
        return

    with open(json_file, "r") as f:
        summary = json.load(f)

    policies = ["STRICT_RAW", "RAW_HYSTERESIS", "ROLLING_WINDOW", "WINDOW_HYSTERESIS"]
    names = ["P1 (Strict Raw)", "P2 (Raw + Hyst)", "P3 (Window)", "P4 (Win + Hyst)"]
    colors = ["#1f77b4", "#2ca02c", "#ff7f0e", "#9467bd"]
    markers = ["X", "*", "o", "s"]
    sizes = [130, 200, 120, 120]

    lags = [summary[p]["detection_lag_mean_ms"] for p in policies]
    lags_std = [summary[p]["detection_lag_std_ms"] for p in policies]
    fa_rates = [summary[p]["false_alarm_ratio_pct"] for p in policies]
    fa_std = [summary[p]["false_alarm_ratio_std"] for p in policies]
    n_runs = summary[policies[0]]["n_runs"]

    fig, ax = plt.subplots(figsize=(6.2, 4.0))

    # Plot policy points
    for i in range(4):
        ax.scatter(
            [lags[i]], [fa_rates[i]],
            marker=markers[i], color=colors[i], s=sizes[i],
            edgecolors="black", linewidth=1.2,
            zorder=3
        )

    # Embedded Table data with Mean +- Std and marker shape keys
    table_data = [
        ["P1 (X) Strict Raw", f"{fa_rates[0]:.1f} ± {fa_std[0]:.1f}%", f"{lags[0]:.1f} ± {lags_std[0]:.1f} ms"],
        ["P2 (*) Raw + Hyst", f"{fa_rates[1]:.1f} ± {fa_std[1]:.1f}%", f"{lags[1]:.1f} ± {lags_std[1]:.1f} ms"],
        ["P3 (o) Window", f"{fa_rates[2]:.1f} ± {fa_std[2]:.1f}%", f"{lags[2]:.1f} ± {lags_std[2]:.1f} ms"],
        ["P4 (s) Win + Hyst", f"{fa_rates[3]:.1f} ± {fa_std[3]:.1f}%", f"{lags[3]:.1f} ± {lags_std[3]:.1f} ms"],
    ]

    col_labels = ["Policy", "False Alarm", r"Lag $T_{\mathrm{detect}}$"]
    col_widths = [0.30, 0.34, 0.36]

    table = ax.table(
        cellText=table_data,
        colLabels=col_labels,
        colWidths=col_widths,
        loc="upper right",
        bbox=[0.37, 0.56, 0.61, 0.40]
    )
    table.auto_set_font_size(False)
    table.set_fontsize(7.5)

    # Style cells with colors matching the scatter points
    for (row, col), cell in table.get_celld().items():
        cell.set_edgecolor("#d0d0d0")
        if row == 0:
            cell.set_facecolor("#f0f2f5")
            cell.set_text_props(weight="bold", color="#111111")
        else:
            cell.set_facecolor("white")
            if col == 0:
                cell.set_text_props(weight="bold", color=colors[row - 1])

    ax.set_xlabel(r"SLA Violation Detection Lag $T_{\mathrm{detect}}$ (ms)", fontsize=9.5, fontweight="bold")
    ax.set_ylabel("False Handover Ratio (%)", fontsize=9.5, fontweight="bold")
    ax.set_title(f"Empirical Trade-Off: Detection Lag vs. False Handover ($N={n_runs}$)", fontsize=10, fontweight="bold")
    ax.grid(True, linestyle="--", alpha=0.5)
    ax.set_xlim(-8, 165)
    ax.set_ylim(-5, 102)

    plt.tight_layout()
    out_png = os.path.join(plots_dir, "stochastic_tradeoff_compact_table.png")
    out_pdf = os.path.join(plots_dir, "stochastic_tradeoff_compact_table.pdf")
    plt.savefig(out_png, dpi=300)
    plt.savefig(out_pdf)
    plt.close()

    print(f"[SUCCESS] Generated Compact Trade-Off with Embedded Table: {out_png} and {out_pdf}")


def plot_stochastic_pareto(results_dir: str = RESULTS_DIR, plots_dir: str = PLOTS_DIR):
    plot_stochastic_tradeoff_clean(results_dir, plots_dir)
    plot_stochastic_tradeoff_compact_table(results_dir, plots_dir)
    plot_stochastic_tradeoff_with_table(results_dir, plots_dir)


def plot_stochastic_boxplots(results_dir: str = RESULTS_DIR, plots_dir: str = PLOTS_DIR):
    """
    Generate side-by-side boxplots for:
      (a) Route Flapping Frequency (Handovers per Run)
      (b) SLA Violation Detection Lag (ms)
    """
    csv_file = resolve_data_path("stochastic_trials.csv", results_dir)
    if not os.path.exists(csv_file):
        print("[WARN] stochastic_trials.csv not found. Skipping boxplots.")
        return

    df = pd.read_csv(csv_file)
    policies = [1, 2, 3, 4]
    policy_labels = ["P1: Strict Raw", "P2: Raw + Hyst", "P3: Window", "P4: Win + Hyst"]
    colors = ["#1f77b4", "#2ca02c", "#ff7f0e", "#9467bd"]

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.8))

    # Subplot (a): Handover Flapping Frequency
    ax1 = axes[0]
    ho_data = [df[df["policy"] == p]["handover_count"].values for p in policies]
    bp1 = ax1.boxplot(ho_data, patch_artist=True, tick_labels=policy_labels, showmeans=True)
    for patch, color in zip(bp1["boxes"], colors):
        patch.set_facecolor(color)
        patch.set_alpha(0.65)
    for median in bp1["medians"]:
        median.set(color="black", linewidth=1.5)

    ax1.set_ylabel("Handovers per Run", fontsize=11, fontweight="bold")
    ax1.set_title("(a) Route Flapping Stability", fontsize=12, fontweight="bold")
    ax1.grid(True, linestyle="--", alpha=0.5, axis="y")

    # Subplot (b): SLA Violation Detection Lag
    ax2 = axes[1]
    lag_data = [df[df["policy"] == p]["detection_lag_ms"].values for p in policies]
    bp2 = ax2.boxplot(lag_data, patch_artist=True, tick_labels=policy_labels, showmeans=True)
    for patch, color in zip(bp2["boxes"], colors):
        patch.set_facecolor(color)
        patch.set_alpha(0.65)
    for median in bp2["medians"]:
        median.set(color="black", linewidth=1.5)

    ax2.set_ylabel("Detection Lag $T_{\\mathrm{detect}}$ (ms)", fontsize=11, fontweight="bold")
    ax2.set_title("(b) SLA Violation Recovery Responsiveness", fontsize=12, fontweight="bold")
    ax2.grid(True, linestyle="--", alpha=0.5, axis="y")

    plt.tight_layout()

    png_path = os.path.join(plots_dir, "stochastic_boxplots.png")
    pdf_path = os.path.join(plots_dir, "stochastic_boxplots.pdf")
    plt.savefig(png_path, dpi=300, bbox_inches="tight")
    plt.savefig(pdf_path, bbox_inches="tight")
    plt.close()

    print(f"[SUCCESS] Generated Stochastic Boxplots: {png_path} and {pdf_path}")


def print_paper_tables(results_dir: str = RESULTS_DIR):
    """Print LaTeX and Markdown tables for conference paper integration."""
    print("\n" + "=" * 80)
    print("CONFERENCE PAPER TABLE V: HERO TRACE MICRO-BENCHMARK (LaTeX Ready)")
    print("=" * 80)
    print(r"""\begin{table}[htbp]
\caption{Deterministic Hero Trace Micro-Benchmark (20.0s Timeline)}
\label{tab:hero_trace_metrics}
\centering
\begin{tabular}{lccccc}
\toprule
\textbf{Policy} & \textbf{Transient Burst} & \textbf{First Trigger} & \textbf{Switch Delay} & \textbf{Detection Lag} & \textbf{SLA Violation} \\
& \textbf{Response ($t=5.0$\,s)} & \textbf{Timestamp ($t$)} & \textbf{$\Delta t_{\text{route}}$ (ms)} & \textbf{$T_{\text{detect}}$ (ms)} & \textbf{Duration (ms)} \\
\midrule""")

    p_names = {
        1: "Policy 1 (Strict Raw)",
        2: "Policy 2 (Raw + Hyst)",
        3: "Policy 3 (Window)",
        4: "Policy 4 (Win + Hyst)",
    }

    for pol in [1, 2, 3, 4]:
        fpath = resolve_data_path(f"hero_trace_policy_{pol}.csv", results_dir)
        if os.path.exists(fpath):
            df = pd.read_csv(fpath)
            switches = df[df["handover_executed"] == True]
            t_first = switches["rel_time_s"].iloc[0] if len(switches) > 0 else None
            sw_lat = switches["switch_latency_ms"].iloc[0] if len(switches) > 0 else None
            false_trig = any(switches["rel_time_s"] < 9.0) if len(switches) > 0 else False
            step_switches = df[(df["handover_executed"] == True) & (df["rel_time_s"] >= 9.9)]
            detect_lag = (step_switches["rel_time_s"].iloc[0] - 10.0) * 1000.0 if len(step_switches) > 0 else None
            sla_ticks = len(df[df["rtt_sample"] > 65.0])
            sla_viols = sla_ticks * 100.0

            burst_str = "False Alarm" if false_trig else "Filtered"
            t_str = f"{t_first:.3f}\\,s" if t_first else "---"
            sw_str = f"{sw_lat:.2f}" if sw_lat else "---"
            lag_str = f"{detect_lag:.1f}" if detect_lag else "N/A"
            viol_str = f"{sla_viols:.0f}"

            if pol == 2:
                burst_str = f"\\textbf{{{burst_str}}}"
                t_str = f"\\textbf{{{t_str}}}"
                sw_str = f"\\textbf{{{sw_str}}}"
                lag_str = f"\\textbf{{{lag_str}}}"
                viol_str = f"\\textbf{{{viol_str}}}"

            print(f"{p_names[pol]:<24} & {burst_str:<20} & {t_str:<18} & {sw_str:<10} & {lag_str:<10} & {viol_str} \\\\")

    print(r"""\bottomrule
\end{tabular}
\end{table}""")

    json_file = resolve_data_path("stochastic_summary.json", results_dir)
    if not os.path.exists(json_file):
        return

    with open(json_file, "r") as f:
        summary = json.load(f)

    print("\n" + "=" * 80)
    print("CONFERENCE PAPER TABLE VI: EMPIRICAL STOCHASTIC AGGREGATES (LaTeX Ready)")
    print("=" * 80)
    mapping = {
        "STRICT_RAW": "Policy 1 (Strict Raw)",
        "RAW_HYSTERESIS": "Policy 2 (Raw + Hyst)",
        "ROLLING_WINDOW": "Policy 3 (Window)",
        "WINDOW_HYSTERESIS": "Policy 4 (Win + Hyst)",
    }
    first_key = list(mapping.keys())[0]
    n_runs = summary[first_key]['n_runs'] if first_key in summary else 100
    print(r"""\begin{table}[htbp]""" + f"""
\\caption{{Randomized Stress-Testing: Empirical Stochastic Aggregates ($N = {n_runs}$ Runs per Policy)}}""" + r"""
\label{tab:random_comparison}
\centering
\begin{tabular}{lcccc}
\toprule
\textbf{Evaluated} & \textbf{False Handover} & \textbf{Detection Delay} & \textbf{Handovers} & \textbf{Mean SLA Viol.} \\
\textbf{Policy} & \textbf{Ratio (\%)} & \textbf{$T_{\text{detect}}$ (ms)} & \textbf{per Run} & \textbf{Duration (ms)} \\
\midrule""")

    for key, name in mapping.items():
        if key in summary:
            s = summary[key]
            fa = f"${s['false_alarm_ratio_pct']:.1f} \\pm {s['false_alarm_ratio_std']:.1f}\\%$"
            lag = f"${s['detection_lag_mean_ms']:.0f} \\pm {s['detection_lag_std_ms']:.0f}$"
            ho = f"${s['handovers_per_run_mean']:.1f} \\pm {s['handovers_per_run_std']:.1f}$"
            viol = f"${s['sla_viol_duration_mean_ms']:.0f} \\pm {s['sla_viol_duration_std_ms']:.0f}$"
            if key == "RAW_HYSTERESIS":
                fa = f"\\textbf{{{fa}}}"
                ho = f"\\textbf{{{ho}}}"
                viol = f"\\textbf{{{viol}}}"
            elif key == "WINDOW_HYSTERESIS":
                fa = f"\\textbf{{{fa}}}"
            print(f"{name:<24} & {fa:<24} & {lag:<18} & {ho:<16} & {viol} \\\\")

    print(r"""\bottomrule
\end{tabular}
\end{table}""")
    print("=" * 80)


def generate_phase3_publication_plots():
    plot_hero_trace()
    plot_stochastic_pareto()
    plot_stochastic_boxplots()
    print_paper_tables()


def main():
    generate_phase3_publication_plots()


if __name__ == "__main__":
    main()
