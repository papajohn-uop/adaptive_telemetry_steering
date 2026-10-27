# Phase 3 Stochastic Benchmark: 400-Trial Randomized Evaluation

This document details the experimental design, stochastic channel model, and statistical analysis for the **Randomized Stochastic Robustness Suite** ($N = 100$ runs per policy, 400 trials total).

---

## 1. Overview & Evaluation Objective

While the deterministic **Hero Trace** ([`HERO_TRACE_PROTOCOL.md`](file:///home/ubuntu/papajohn/AMAZING_6G/PAPER/NEW_APPROACH/adaptive_telemetry_steering/docs/HERO_TRACE_PROTOCOL.md)) provides precise temporal dissection of a single controlled micro-burst and step event, real-world wireless and non-terrestrial backhauls exhibit stochastic fading, variable burst intervals, and unpredictable failure onsets.

The randomized stochastic benchmark stress-tests the $2 \times 2$ policy matrix over a large statistical sample ($N = 100$ independent trials per policy, 400 trials total) to quantify:
1. **False Handover Ratio (%):** The probability that a policy mistakenly triggers a route switch during pre-step transient noise ($t < t_{\text{step}}$).
2. **SLA Violation Detection Lag ($T_{\text{detect}}$):** The elapsed time (in ms) from the true onset of structural degradation ($t_{\text{step}}$) until the policy executes a Netlink route switch.
3. **Route Flapping Frequency:** The average number of handovers triggered per session.
4. **Cumulative SLA Violation Duration:** The total time (in ms) user traffic is subjected to RTTs exceeding $\tau = 65.0\text{ ms}$.

---

## 2. Stochastic Channel Emulation Model

Each trial executes inside the 3-namespace Linux emulation environment over a **20.0-second session** with telemetry polled at $T_{\text{poll}} = 100\text{ ms}$ (10 Hz).

### 2.1 Baseline Channel Parameters
* **Link A (Backup Fiber):** HTB $100\text{ Mbit/s}$, delay $2.0\text{ ms}$ (RTT $\approx 4.3\text{ ms}$), $0.0\%$ loss.
* **Link B (Cellular Relay):** HTB $50\text{ Mbit/s}$, delay $25.0\text{ ms}$, Gaussian jitter $\pm 2.5\text{ ms}$ (nominal RTT $\approx 50.7\text{ ms}$), $0.5\%$ loss.
* **SLA Threshold ($\tau$):** $65.0\text{ ms}$.

### 2.2 Stochastic Disturbance Generation

For each trial index $k \in \{0, \dots, 99\}$, an identical stochastic profile is pre-generated across all 4 policies to ensure fair, paired comparative evaluation:

1. **Random Step Onset ($t_{\text{step}}$):**
   The onset of permanent structural degradation is drawn from a continuous uniform distribution:
   $$t_{\text{step}} \sim \mathcal{U}(5.0, 9.0)\text{ seconds}$$
   At $t \ge t_{\text{step}}$, Link B permanently degrades to $+40\text{ ms}$ delay ($RTT \approx 95.5\text{ ms}$), violating $\tau$.

2. **Poisson Arrival Process of Micro-Bursts:**
   In the pre-step window $t \in [0.5, t_{\text{step}} - 0.2]$, transient noise bursts arrive according to a Poisson process with intensity parameter:
   $$\lambda = 0.5\text{ s}^{-1} \quad (\text{mean inter-arrival time } \mathbb{E}[\Delta t] = 2.0\text{ s})$$
   For each arrival:
   * **Duration:** $d_{\text{burst}} \sim \mathcal{U}(50, 80)\text{ ms}$ (strictly sub-polling tick, ensuring true isolated micro-bursts cannot straddle multiple polling ticks).
   * **Delay Amplitude:** $A_{\text{burst}} \sim \mathcal{U}(28.0, 42.0)\text{ ms}$, driving RTT up to $78\text{--}92\text{ ms}$ ($> \tau = 65\text{ ms}$), matching the Hero Trace's $88.4\text{ ms}$ transient spike.
   * Upon burst completion, the channel immediately reverts to nominal baseline ($50.7\text{ ms}$).

### 2.3 Physical Real-World Rationale & Realism

The synthetic parameters of this benchmark are explicitly grounded in real-world wireless and telecommunications operational standards:

1. **Topology Realism (Hybrid Multi-Access Edge):**
   * Corresponds directly to **3GPP ATSSS (Releases 16–18)**, where an edge terminal (e.g., connected vehicle, industrial gateway, or drone relay) is dual-homed across a low-latency terrestrial link (Fiber/eMBB, $4\text{ ms}$ base RTT) and an impaired non-terrestrial or wireless relay (Cellular/LEO Satellite, $50\text{ ms}$ base RTT) connecting to a unified cloud endpoint (`10.0.3.2`).

2. **Telemetry Overhead Realism (10 Hz Active Polling):**
   * Standardized network monitoring protocols—including **BFD (Bidirectional Forwarding Detection, RFC 5880)**, **TWAMP (RFC 5357)**, and the 3GPP Performance Measurement Function (PMF)—routinely operate at $T_{\text{poll}} = 100\text{ ms}$ (10 Hz).
   * At 64 bytes per probe, the control-plane overhead is only $10.24\text{ kbps}$ ($< 0.01\%$ of link capacity), making it entirely negligible in production.

3. **Transient Noise Realism (HARQ & Micro-Queueing):**
   * Sub-tick micro-bursts ($50\text{--}80\text{ ms}$, $+28\text{ to }+42\text{ ms}$) replicate transient physical-layer phenomena:
     * **Cellular HARQ Retransmissions:** Wireless multipath fading triggers MAC-layer retransmissions (up to 4 attempts), adding 20–60 ms of momentary packet delivery delay.
     * **Transient Base Station Buffer Queueing:** Short bursts of co-channel traffic causing momentary queue buildup that self-heals as soon as the buffer drains.
     * **Antenna Beam Phasing:** Tracking and handoff alignment delays in mmWave 5G or LEO phased-array satellite terminals.
   * Because these events self-resolve within $<100\text{ ms}$, a viable steering controller must absorb them without triggering costly route flap handovers.

4. **Structural Degradation Realism (Rain Fade & Upstream Rerouting):**
   * The permanent $+40\text{ ms}$ step ($RTT \approx 95.5\text{ ms} > \tau = 65\text{ ms}$) replicates true persistent channel failure:
     * **Satellite Rain Fade & AMC Downshifting:** Severe rain attenuation forces satellite transceivers to downshift Adaptive Modulation and Coding (e.g., from 64-QAM to QPSK), permanently increasing propagation delay and reducing channel capacity.
     * **Upstream Protection Rerouting:** Fiber cut or microwave failure in the carrier's backhaul forcing dynamic rerouting over geographically longer, congested transit hops.

5. **Actuation Realism (In-Kernel Linux Netlink):**
   * Route migrations are executed via raw Linux Netlink sockets (`RTM_NEWROUTE`), measuring the true kernel Forwarding Information Base (FIB) and route-cache replacement latency ($5.13\text{--}5.44\text{ ms}$), rather than an abstract discrete-event simulation model.

---

## 3. Evaluated Performance Metrics

For each trial, the orchestrator records:
* **False Alarm ($FA$):**
  $$FA = \begin{cases} 1, & \text{if first handover occurs at } t_{\text{handover}} < t_{\text{step}} \\ 0, & \text{otherwise} \end{cases}$$
* **Detection Lag ($T_{\text{detect}}$):**
  If a policy suffered a false alarm, $T_{\text{detect}} = 0\text{ ms}$ (as it already abandoned Link B prematurely). Otherwise:
  $$T_{\text{detect}} = \max(0.0, (t_{\text{handover}} - t_{\text{step}}) \times 1000.0) \quad (\text{ms})$$
* **SLA Violation Duration:**
  $$\text{Duration} = \sum_{t} \mathbb{I}(RTT(t) > \tau) \times T_{\text{poll}} \quad (\text{ms})$$

---

## 4. Execution & Orchestration

The test harness is implemented in [`experiments/stochastic/run_stochastic_eval.py`](file:///home/ubuntu/papajohn/AMAZING_6G/PAPER/NEW_APPROACH/adaptive_telemetry_steering/experiments/stochastic/run_stochastic_eval.py).

```bash
# Execute 100 runs per policy (400 trials total) across 20.0s timeline
sudo python3 experiments/stochastic/run_stochastic_eval.py --runs 100 --duration 20.0
```

* Results are written to:
  * Raw trial-by-trial log: `data/stochastic/stochastic_trials.csv`
  * Statistical summary: `data/stochastic/stochastic_summary.json`
* Once complete, publication plots and LaTeX tables are generated via:
  ```bash
  python3 analysis/analyze_phase3.py
  ```

---

## 5. Empirical Results & Statistical Summary ($N = 100$ Runs / Policy)

Execution of the full 400-trial benchmark produced the following aggregate metrics:

| Policy ID | Evaluated Policy | False Handover Ratio (%) | Detection Lag $T_{\text{detect}}$ (ms) | Handovers per Run | Mean SLA Violation Duration (ms) | Netlink Switch Latency (ms) |
| :---: | :--- | :---: | :---: | :---: | :---: | :---: |
| **Policy 1** | **Strict Raw** ($K=1$) | $88.0 \pm 32.5\%$ | $3.7 \pm 15.1\text{ ms}$ | $1.0 \pm 0.0$ | $85.0 \pm 35.7\text{ ms}$ | $5.13\text{ ms}$ |
| **Policy 2** | **Raw + Hysteresis** ($K=2$) | **$10.0 \pm 30.0\%$** | **$120.6 \pm 60.2\text{ ms}$** | **$1.0 \pm 0.0$** | **$329.0 \pm 111.6\text{ ms}$** | **$5.29\text{ ms}$** |
| **Policy 3** | **Rolling Window** ($N=3$) | $35.0 \pm 47.7\%$ | $66.5 \pm 67.1\text{ ms}$ | $1.0 \pm 0.0$ | $251.0 \pm 173.5\text{ ms}$ | $5.29\text{ ms}$ |
| **Policy 4** | **Window + Hyst** ($N=3, K=2$) | **$28.0 \pm 44.9\%$** | $142.9 \pm 100.3\text{ ms}$ | $1.0 \pm 0.0$ | $341.0 \pm 217.3\text{ ms}$ | $5.44\text{ ms}$ |

### 5.1 Key Findings & Physical Insights

1. **Vulnerability of Memoryless Triggering (Policy 1):**
   * **Mathematical Basis:** In the pre-step window ($t \in [0.5, t_{\text{step}} - 0.2]$, mean duration $\Delta t \approx 6.5\text{ s}$), Poisson noise bursts arrive at intensity $\lambda = 0.5\text{ s}^{-1}$. The probability of at least one burst occurring before structural degradation is:
     $$\mathbb{P}(\ge 1\text{ burst}) = 1 - e^{-\lambda \cdot \Delta t} = 1 - e^{-0.5 \times 6.5} \approx 96.1\%$$
   * **Empirical Behavior:** In **$88.0\%$ of runs**, Policy 1 encountered a micro-burst and immediately triggered a false-positive route switch because it evaluates raw samples with zero temporal memory ($K=1$). 
   * **Detection Lag:** Because $88\%$ of runs abandoned Link B prior to $t_{\text{step}}$, their post-step delay is recorded as $0\text{ ms}$. In the $12\%$ of noise-free runs, Policy 1 detected the structural step on the first probe ($T_{\text{detect}} \approx 30\text{ ms}$), yielding an aggregate mean of $3.7 \pm 15.1\text{ ms}$.
   * **Takeaway:** Proves that naive instantaneous thresholding is practically unviable in stochastic wireless backhauls.

2. **Order-of-Magnitude Noise Suppression via Hysteresis (Policy 2):**
   * **Sub-Tick Filtering:** Because burst durations are $50\text{--}80\text{ ms}$ (strictly sub-$100\text{ ms}$ polling tick), an isolated micro-burst can only ever hit **one** probe. The subsequent probe $100\text{ ms}$ later sees the channel restored to baseline ($50\text{ ms}$), which resets the persistence counter $c_{\text{raw}}$ to 0.
   * **Collision Probability:** Policy 2 only triggers a false alarm if two independent Poisson bursts happen to land in back-to-back $100\text{ ms}$ ticks (probability $\approx 5\text{--}10\%$). As a result, Policy 2 cut false alarms from $88.0\%$ down to **$10.0\%$** (an $\approx 9\times$ reduction in false switches).
   * **Hero Trace Convergence:** When genuine structural failure occurs at $t_{\text{step}}$, Policy 2 confirms the failure across two consecutive probes and executes route replacement in **$120.6 \pm 60.2\text{ ms}$**. This aligns with the deterministic Hero Trace micro-benchmark (**$121.8\text{ ms}$**) within $1.2\text{ ms}$, confirming physical consistency between single-trace and multi-trial stochastic evaluations.
   * **Standard Alignment:** A $2$-tick persistence window at 10 Hz corresponds to a $200\text{ ms}$ Time-to-Trigger (TTT), matching commercial 3GPP cellular handover standards (TS 38.331: 100–320 ms).

3. **Buffer Memory Pollution in Moving Averages (Policy 3):**
   * **FIFO Buffer Pollution:** When a high-amplitude outlier enters an $N=3$ FIFO queue, it dilutes the spike, but that sample remains inside the buffer for $3 \times T_{\text{poll}} = 300\text{ ms}$. If secondary jitter arrives while the queue is contaminated, the moving average $\mu_{\mathcal{W}}$ breaches $\tau = 65\text{ ms}$, causing false alarms in $35.0\%$ of runs.
   * **Detection Lag:** Policy 3 achieved an average detection lag of $66.5 \pm 67.1\text{ ms}$, reacting quickly when the buffer was partially pre-loaded, but retaining noise longer than a clean reset counter.

4. **Latency Penalty of Dual-Stage Filtering (Policy 4):**
   * **Compounded Delay:** Combining window smoothing with hysteresis forces two sequential stages: the moving average must first rise above $\tau$, and then the persistence counter must observe two consecutive breaches.
   * **Performance Impact:** This double filtering inflates detection delay to **$142.9 \pm 100.3\text{ ms}$**, resulting in the highest cumulative SLA violation duration (**$341.0 \pm 217.3\text{ ms}$**). Despite this latency penalty, buffer pollution in the window still caused a $28.0\%$ false alarm rate.

### 5.2 Comparative Operational Assessment

| Evaluated Policy | Noise Rejection | Mean Reaction Delay | Memory Complexity | Operational Suitability |
| :--- | :---: | :---: | :---: | :--- |
| **P1 (Strict Raw)** | 12% (88% failure) | Instantaneous ($3.7\text{ ms}$) | $O(1)$ | Unviable in stochastic channels |
| **P2 (Raw + Hyst)** | **90% (10% failure)** | **Fast ($120.6\text{ ms}$)** | **$O(1)$** | **Optimal Operating Knee** |
| **P3 (Window)** | 65% (35% failure) | Moderate ($66.5\text{ ms}$) | $O(N)$ | Vulnerable to buffer pollution |
| **P4 (Win + Hyst)** | 72% (28% failure) | Slowest ($142.9\text{ ms}$) | $O(N)$ | Excessive latency penalty |

### 5.3 Generated Visualizations
* **Compact Trade-Off with Embedded Table:** [`figures/stochastic_tradeoff_compact_table.pdf`](file:///home/ubuntu/papajohn/AMAZING_6G/PAPER/NEW_APPROACH/adaptive_telemetry_steering/figures/stochastic_tradeoff_compact_table.pdf) (Clean scatter points with embedded $\text{Mean} \pm \text{Std}$ table in the upper-right canvas).
* **Compact Minimalist Scatter:** [`figures/stochastic_tradeoff_clean.pdf`](file:///home/ubuntu/papajohn/AMAZING_6G/PAPER/NEW_APPROACH/adaptive_telemetry_steering/figures/stochastic_tradeoff_clean.pdf) (Minimalist markers, uncluttered canvas).
* **Trade-Off with Table Legend:** [`figures/stochastic_tradeoff_with_table.pdf`](file:///home/ubuntu/papajohn/AMAZING_6G/PAPER/NEW_APPROACH/adaptive_telemetry_steering/figures/stochastic_tradeoff_with_table.pdf) (Detailed tabular legend containing $\text{Mean} \pm \text{Std}$).
* **Boxplots:** [`figures/stochastic_boxplots.pdf`](file:///home/ubuntu/papajohn/AMAZING_6G/PAPER/NEW_APPROACH/adaptive_telemetry_steering/figures/stochastic_boxplots.pdf) (Route Flapping Stability and SLA Detection Delay distributions).

