# Phase 3 Deterministic Benchmark: The Hero Trace Experiment

This document provides a complete technical description and operational breakdown of the **Phase 3 Deterministic Micro-Benchmark ("Hero Trace")**. 

---

## 1. Overview & Experimental Motivation

While randomized stochastic evaluations ([`phase3_eval/STOCHASTIC_EXPERIMENT.md`](file:///home/ubuntu/papajohn/AMAZING_6G/PAPER/NEW_APPROACH/paper_repo/NEW%20EXPERIMENTS/phase3_eval/STOCHASTIC_EXPERIMENT.md)) demonstrate statistical robustness, a deterministic micro-benchmark is essential to **isolate and inspect the exact temporal mechanics** of the $2 \times 2$ policy matrix under identical, repeatable channel events.

The Hero Trace benchmark answers two critical operational questions:
1. **Transient Noise Resilience:** When a temporary wireless micro-burst occurs, does the policy absorb the transient disturbance or cause a costly premature false switch?
2. **Structural Degradation Responsiveness:** When the active link permanently breaches the latency SLA ceiling, how quickly does the policy detect the failure and re-anchor traffic to the backup path?

---

## 2. Experimental Setup & Channel Timeline

The experiment is executed on the 3-namespace Linux emulation platform ([`scripts/setup_phase2_topology.sh`](file:///home/ubuntu/papajohn/AMAZING_6G/PAPER/NEW_APPROACH/paper_repo/NEW%20EXPERIMENTS/scripts/setup_phase2_topology.sh)) over a synchronized **20.0-second timeline** at a 10 Hz telemetry polling cadence ($T_{\text{poll}} = 100\text{ ms}$).

### 2.1 Backhaul Path Characteristics

* **Link A (Backup Terrestrial Fiber):**
  * Bottleneck: $100\text{ Mbit/s}$ (HTB)
  * Delay: $2.0\text{ ms}$ one-way ($4.3\text{ ms}$ base RTT)
  * Loss: $0.0\%$
* **Link B (Initial Active Cellular Relay):**
  * Bottleneck: $50\text{ Mbit/s}$ (HTB)
  * Delay: $25.0\text{ ms}$ one-way ($50.7\text{ ms}$ base RTT)
  * Target SLA Latency Ceiling ($\tau$): **$65.0\text{ ms}$**

### 2.2 The 20.0-Second Channel Timeline

```
Time (s)     0.0          5.0   5.1                10.0                                20.0
             |------------|-----|-------------------|-----------------------------------|
Link B RTT:     ~50.7 ms   88.5ms     ~50.7 ms                    ~95.5 ms (Permanent)
Event:        [Nominal]  [Micro-Burst] [Nominal]        [Persistent Structural Step]
Goal:          Stay on B    Ignore      Stay on B        Switch to Link A (~4.3 ms)
```

1. **Phase 1: Nominal Baseline ($t = 0.0\text{ s} \to 5.0\text{ s}$):**
   Link B operates under nominal conditions ($RTT \approx 50.7\text{ ms} < \tau = 65.0\text{ ms}$). Active traffic flows over Link B.
2. **Phase 2: Transient Micro-Burst ($t = 5.0\text{ s} \to 5.1\text{ s}$):**
   A temporary queue buildup / radio fading spike injects $+38\text{ ms}$ delay for **$100\text{ ms}$**, pushing RTT to **$88.5\text{ ms}$**. At $t = 5.10\text{ s}$, Link B immediately returns to $50.7\text{ ms}$.
3. **Phase 3: Inter-Burst Recovery ($t = 5.1\text{ s} \to 10.0\text{ s}$):**
   Nominal channel conditions on Link B resume.
4. **Phase 4: Persistent Structural Step ($t = 10.0\text{ s} \to 20.0\text{ s}$):**
   Link B experiences permanent physical degradation (e.g., severe backhaul congestion or satellite beam handoff attenuation), stepping RTT to **$95.5\text{ ms}$** for the remainder of the session.
5. **Phase 5: Post-Handover Observation ($t = 10.2\text{ s} \to 20.0\text{ s}$):**
   Traffic migrated to Link A is observed for 10 seconds to verify zero route flapping.

---

## 3. Step-by-Step Policy Operation & Numbers

Each of the four policies from [`phase2_telemetry/steering_controller.py`](file:///home/ubuntu/papajohn/AMAZING_6G/PAPER/NEW_APPROACH/paper_repo/NEW%20EXPERIMENTS/phase2_telemetry/steering_controller.py) was evaluated across this timeline. The resulting telemetry traces are stored in [`ANALYSIS_RESULTS/PHASE3/`](file:///home/ubuntu/papajohn/AMAZING_6G/PAPER/NEW_APPROACH/paper_repo/NEW%20EXPERIMENTS/ANALYSIS_RESULTS/PHASE3/).

### Policy 1: Strict Raw (Single Sample, $K=1$)
* **Algorithmic Condition:** $\mathcal{C}_1(t) = \mathbb{I}(RTT(t) > \tau)$
* **Micro-Burst Reaction ($t = 5.0\text{ s}$):**
  * Probe at $t = 5.011\text{ s}$ records `rtt_sample = 88.44 ms` ($> 65.0\text{ ms}$).
  * **Result:** **Premature False Switch executed at $t = 5.011\text{ s}$** (Netlink route replacement delay $\Delta t_{\text{route}} = 5.24\text{ ms}$).
  * **Consequence:** The flow abandons Link B and resets transport state onto Link A, even though Link B recovered $89\text{ ms}$ later.
* **Structural Step Reaction ($t = 10.0\text{ s}$):**
  * N/A. The flow is already running on Link A ($4.3\text{ ms}$) due to the earlier false alarm.

### Policy 2: Raw + Hysteresis (Single Sample, $K=2$)
* **Algorithmic Condition:** $\mathcal{C}_2(t) = \prod_{j=0}^{1} \mathbb{I}(RTT(t-j) > \tau)$
* **Micro-Burst Reaction ($t = 5.0\text{ s}$):**
  * Tick 50 ($t = 5.010\text{ s}$): `rtt_sample = 88.55 ms` $> 65.0 \implies c_{\text{raw}} = 1$.
  * Tick 51 ($t = 5.111\text{ s}$): Burst has cleared; `rtt_sample = 50.48 ms` $< 65.0 \implies c_{\text{raw}} = 0$.
  * **Result:** **Micro-burst successfully absorbed.** No switch occurs. Flow remains on Link B.
* **Structural Step Reaction ($t = 10.0\text{ s}$):**
  * Tick 100 ($t = 10.021\text{ s}$): First high sample `95.67 ms` $> 65.0 \implies c_{\text{raw}} = 1$.
  * Tick 101 ($t = 10.122\text{ s}$): Second consecutive high sample `95.53 ms` $> 65.0 \implies c_{\text{raw}} = 2$.
  * **Result:** **Trigger executed at $t = 10.122\text{ s}$** ($\Delta t_{\text{route}} = 5.20\text{ ms}$).
  * **Detection Lag ($T$):** $10.122\text{ s} - 10.000\text{ s} = \mathbf{121.8\text{ ms}}$ ($21.8\text{ ms}$ until first probe + $100\text{ ms}$ confirmation interval).

### Policy 3: Rolling Window Average ($N=3, K=1$)
* **Algorithmic Condition:** $\mathcal{C}_3(t) = \mathbb{I}(\mu_{\mathcal{W}}(t) > \tau)$
* **Micro-Burst Reaction ($t = 5.0\text{ s}$):**
  * At $t = 5.011\text{ s}$, the 3-sample buffer contains $[50.59\text{ ms}, 50.59\text{ ms}, 88.57\text{ ms}]$.
  * Window mean: $\mu_{\mathcal{W}} = \frac{50.59 + 50.59 + 88.57}{3} = \mathbf{63.25\text{ ms}}$.
  * Since $63.25\text{ ms} < 65.0\text{ ms}$, the condition is not met.
  * **Result:** **Micro-burst diluted by buffer.** No switch occurs. Flow remains on Link B.
* **Structural Step Reaction ($t = 10.0\text{ s}$):**
  * At $t = 10.022\text{ s}$, the buffer contains $[50.96\text{ ms}, 50.91\text{ ms}, 95.60\text{ ms}]$.
  * Window mean: $\mu_{\mathcal{W}} = \frac{50.96 + 50.91 + 95.60}{3} = \mathbf{65.82\text{ ms}} > 65.0\text{ ms}$.
  * Since $K=1$, the first window breach triggers an immediate switch!
  * **Result:** **Trigger executed at $t = 10.022\text{ s}$** ($\Delta t_{\text{route}} = 6.01\text{ ms}$).
  * **Detection Lag ($T$):** $10.022\text{ s} - 10.000\text{ s} = \mathbf{22.3\text{ ms}}$.

### Policy 4: Window + Hysteresis ($N=3, K=2$)
* **Algorithmic Condition:** $\mathcal{C}_4(t) = \prod_{m=0}^{1} \mathbb{I}(\mu_{\mathcal{W}}(t-m) > \tau)$
* **Micro-Burst Reaction ($t = 5.0\text{ s}$):**
  * Moving average never exceeds $63.21\text{ ms} < 65.0\text{ ms}$.
  * **Result:** **Micro-burst fully suppressed.** No switch occurs. Flow remains on Link B.
* **Structural Step Reaction ($t = 10.0\text{ s}$):**
  * Tick 100 ($t = 10.021\text{ s}$): $\mu_{\mathcal{W}} = 65.50\text{ ms} > 65.0 \implies c_{\text{win}} = 1$.
  * Tick 101 ($t = 10.122\text{ s}$): $\mu_{\mathcal{W}} = 80.49\text{ ms} > 65.0 \implies c_{\text{win}} = 2$.
  * **Result:** **Trigger executed at $t = 10.122\text{ s}$** ($\Delta t_{\text{route}} = 5.68\text{ ms}$).
  * **Detection Lag ($T$):** $10.122\text{ s} - 10.000\text{ s} = \mathbf{121.7\text{ ms}}$.

---

## 4. Master Empirical Results Summary

The empirical metrics measured across the deterministic 20.0s benchmark:

| Policy ID | Evaluated Policy | Response to Micro-Burst ($t=5.0\text{ s}$) | Step Trigger Timestamp ($t$) | Netlink Switch Latency ($\Delta t_{\text{route}}$) | Detection Lag ($T$) | SLA Violation Duration | Final Active Link |
| :---: | :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **Policy 1** | **Strict Raw** ($K=1$) | **False Alarm** ($t = 5.011\text{ s}$) | N/A (Already switched) | $5.24\text{ ms}$ | N/A | $100\text{ ms}$ | Link A ($4.3\text{ ms}$) |
| **Policy 2** | **Raw + Hysteresis** ($K=2$) | **Filtered (Absorbed)** | **$10.122\text{ s}$** | $5.20\text{ ms}$ | **$121.8\text{ ms}$** | $300\text{ ms}$ | Link A ($4.3\text{ ms}$) |
| **Policy 3** | **Rolling Window** ($N=3$) | **Filtered (Diluted)** | **$10.022\text{ s}$** | $6.01\text{ ms}$ | **$22.3\text{ ms}$** | $200\text{ ms}$ | Link A ($4.3\text{ ms}$) |
| **Policy 4** | **Window + Hyst** ($N=3, K=2$) | **Filtered (Suppressed)** | **$10.122\text{ s}$** | $5.68\text{ ms}$ | **$121.7\text{ ms}$** | $300\text{ ms}$ | Link A ($4.3\text{ ms}$) |

---

## 5. Artifact Visualizations

Two graphical representations have been generated to illustrate these dynamics:

1. **5-Panel Detailed Subplot Figure:**  
   * Files: [`plots/hero_trace_5panel.png`](file:///home/ubuntu/papajohn/AMAZING_6G/PAPER/NEW_APPROACH/paper_repo/NEW%20EXPERIMENTS/plots/hero_trace_5panel.png) and [`plots/hero_trace_5panel.pdf`](file:///home/ubuntu/papajohn/AMAZING_6G/PAPER/NEW_APPROACH/paper_repo/NEW%20EXPERIMENTS/plots/hero_trace_5panel.pdf)
   * Layout:
     - Subplot (a): Channel Scenario (Raw RTT, Sliding Window Mean, and EWMA against $\tau = 65\text{ ms}$).
     - Subplot (b): Policy 1 trajectory showing premature false switch at $t = 5.01\text{ s}$.
     - Subplot (c): Policy 2 trajectory showing burst absorption and step trigger at $t = 10.12\text{ s}$.
     - Subplot (d): Policy 3 trajectory showing burst dilution and fast trigger at $t = 10.02\text{ s}$.
     - Subplot (e): Policy 4 trajectory showing conservative filtering and trigger at $t = 10.12\text{ s}$.
2. **1-Plot Compact Unified Figure:**  
   * Files: [`plots/hero_trace_compact.png`](file:///home/ubuntu/papajohn/AMAZING_6G/PAPER/NEW_APPROACH/paper_repo/NEW%20EXPERIMENTS/plots/hero_trace_compact.png) and [`plots/hero_trace_compact.pdf`](file:///home/ubuntu/papajohn/AMAZING_6G/PAPER/NEW_APPROACH/paper_repo/NEW%20EXPERIMENTS/plots/hero_trace_compact.pdf)
   * Layout: Single space-saving plot overlaying all 4 policy trajectories, shaded burst and step zones, and trigger markers ($t$ and $T$).

---

## 6. Key Operational Insights

1. **The Cost of Memoryless Decisions ($K=1$):**  
   Policy 1 illustrates why raw latency thresholding fails in wireless/satellite environments: a single $100\text{ ms}$ radio scheduling delay or MAC retransmission triggers a full route switch, needlessly discarding active TCP window state.
2. **Why Policy 2 Enforces $T \approx 122\text{ ms}$:**  
   With a $100\text{ ms}$ probe interval, the first probe arrives $\approx 22\text{ ms}$ after the step ($t = 10.022\text{ s}$). Enforcing $K=2$ requires exactly 1 additional probe interval ($+100\text{ ms}$), producing a deterministic detection lag of $22 + 100 \approx 122\text{ ms}$.
3. **Why Policy 3 Triggers in $22\text{ ms}$ on Large Steps:**  
   Because the structural step ($+45\text{ ms}$) was larger than the buffer dilution margin ($3 \times (65 - 50.7) = 42.9\text{ ms}$), a single sample was sufficient to pull the entire 3-sample average above $65\text{ ms}$ ($65.82 > 65.0$). Since $K=1$, Policy 3 fired on the very first probe sample.
4. **Clean Route Stability:**  
   In all four policies, once traffic was steered to Link A, the active RTT remained tightly bounded at $4.3\text{ ms}$ for the remainder of the 20.0s session, confirming zero post-handover route oscillation or flap.
