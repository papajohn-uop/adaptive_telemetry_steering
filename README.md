# Adaptive Telemetry-Driven Path Steering for High-Assurance 6G Networks

This repository contains the complete open-source replication package, empirical datasets, and camera-ready manuscript sources for the paper:
> **"Deterministic Verification and Stochastic Evaluation of Real-Time Telemetry Steering in Multi-Backhaul 6G Networks"**

---

## 1. Overview and Core Contributions

Modern multi-homed 6G architectures require real-time path steering across heterogeneous links (e.g., terrestrial fiber vs. low-latency wireless backhaul) to satisfy strict ultra-reliable low-latency communication (URLLC) Service Level Agreements (SLAs). 

This work addresses the fundamental trade-off between **detection delay** and **route-flapping stability** through three key contributions:

1. **Phase 1: Baseline Migration Overhead & Transport Sensitivity**
   - Empirical validation of application-level session migration across file sizes (50 MB – 500 MB).
   - Proves migration overhead is strictly $O(1)$ constant ($55 \pm 4$ ms) regardless of transfer volume.
   - Characterizes TCP Reno vs. CUBIC performance under emulated latency, loss, and jitter sweeps.

2. **Phase 2: Asynchronous Decoupled Telemetry Daemon**
   - Non-blocking kernel-space ICMP prober utilizing an $O(1)$ ring buffer.
   - Consistently achieves $< 0.05\%$ CPU overhead at 100 ms probing intervals.
   - Provides fresh, non-blocking telemetry samples without head-of-line socket blocking.

3. **Phase 3: Empirical Disturbance Benchmarking**
   - **Deterministic Micro-Benchmark (Hero Trace):** A 20.0-second controlled timeline evaluating transient micro-bursts (5.0s) and permanent structural link degradation (10.0s).
   - **Randomized Stochastic Stress-Testing:** 400 empirical trials (100 runs per policy) under randomized netem delay, jitter, and burst duration distributions.
   - Demonstrates that **Policy 2 (Raw Telemetry + Hysteresis Counter)** achieves Pareto optimality, eliminating **88.6% of false handovers** while adding only 53 ms of detection delay compared to aggressive raw triggers.

---

## 2. Repository Layout

```text
adaptive_telemetry_steering/
├── core/                              # Core implementation code
│   ├── __init__.py
│   ├── telemetry_daemon.py            # Asynchronous kernel ICMP daemon (ring buffer, O(1))
│   └── steering_controller.py         # 4-policy reactive steering state machine
│
├── topology/                          # Environment setup & network emulation
│   ├── setup_topology.sh              # 3-namespace multi-homed veth routing topology
│   └── teardown_topology.sh           # Clean namespace & interface teardown script
│
├── experiments/                       # Benchmark runners
│   ├── baselines/                     # Phase 1: TCP Reno vs CUBIC migration benchmarks
│   ├── hero_trace/                    # Phase 3: Deterministic hero trace micro-benchmark
│   └── stochastic/                    # Phase 3: Randomized disturbance stress-testing suite
│
├── analysis/                          # Data processing & publication figures
│   ├── analyze_phase3.py              # Generates publication plots and prints LaTeX Tables V & VI
│   ├── analyze_baselines.py           # Analyzes baseline payload transfer datasets
│   └── analyze_impairments.py         # Analyzes network impairment sweeps
│
├── data/                              # Curated empirical datasets
│   ├── baselines/                     # Master CSV summaries of Phase 1 experiments
│   ├── hero_trace/                    # High-resolution time-series CSVs (Policies 1-4)
│   └── stochastic/                    # 400-trial dataset (stochastic_trials.csv & stochastic_summary.json)
│
├── figures/                           # Publication-grade vector (PDF) and bitmap (PNG) figures
│   ├── hero_trace/                    # 5-panel and compact hero trace plots
│   ├── stochastic/                    # Trade-off, boxplot, and Pareto plots
│   └── baselines/                     # Reno vs CUBIC comparative transfer plots
│
├── paper/                             # Camera-ready manuscript sources
│   ├── main.tex                       # IEEE conference manuscript
│   ├── phase2_telemetry.tex           # Architecture and theoretical telemetry bounds
│   ├── hero_trace.tex                 # Hero trace micro-benchmark section
│   ├── stochastic_eval.tex            # Randomized stochastic evaluation section
│   └── references.bib                 # BibTeX references
│
└── docs/                              # Detailed experiment specifications
    ├── HERO_TRACE_PROTOCOL.md         # Deterministic impairment schedule & metrics
    ├── STOCHASTIC_EVAL_SPEC.md        # Randomized parameter distributions & statistical bounds
    └── PHASE2_TELEMETRY.md            # Daemon architecture and ring-buffer sizing
```

---

## 3. System Architecture & Call Hierarchy ("Who Calls What")

The table and diagram below detail how components interact across evaluation phases:

```text
               +--------------------------------------------------------+
               |         core/telemetry_daemon.py: RawICMPProber        |
               |       (Low-level raw ICMP socket prober in ns_client)  |
               +---------------------------+----------------------------+
                                           |
                 +-------------------------+-------------------------+
                 |                                                   |
                 v                                                   v
+------------------------------------+             +------------------------------------+
|     core/telemetry_daemon.py:      |             |     core/steering_controller.py:   |
|         TelemetryDaemon            |             |          SteeringController        |
|------------------------------------|             |------------------------------------|
| • Role: Passive Monitor / Observer |             | • Role: Active Closed-Loop Control |
| • Embeds: PathTelemetry (Link A/B) |             | • Embeds: RawICMPProber (Link A/B) |
| • Features: EWMA, sliding window,  |             | • Features: Evaluates Policies 1-4 |
|   persistence counters, CSV log    |             | • Actuation: Atomic Netlink route  |
| • Routing: Read-only (NO changes)  |             |   switching (`ip route replace`)   |
| • Scope: Phase 2 Telemetry PoC     |             | • Scope: Phase 2/3 Steering Core   |
+------------------------------------+             +-----------------+------------------+
                                                                     |
                                         +---------------------------+---------------------------+
                                         |                                                       |
                                         v                                                       v
                       +-----------------------------------+                   +-----------------------------------+
                       | experiments/hero_trace/           |                   | experiments/stochastic/           |
                       |       run_hero_trace.py           |                   |       run_stochastic_eval.py      |
                       |-----------------------------------|                   |-----------------------------------|
                       | • Phase 3 Deterministic Benchmark |                   | • Phase 3 400-Trial Stochastic    |
                       | • Applies netem burst & step      |                   | • Applies Poisson fading & bursts |
                       | • Calls SteeringController        |                   | • Calls SteeringController        |
                       |   for Policies 1-4                |                   |   for 100 runs per policy         |
                       | • Exports data/hero_trace/*.csv   |                   | • Exports data/stochastic/*.csv   |
                       +-----------------------------------+                   +-----------------------------------+
```

### Key Functional Distinctions:
1. **`TelemetryDaemon` is a Passive Monitor (Phase 2 Standalone PoC):**
   - Validates **Phase 2 telemetry feasibility**: Proves that 10 Hz out-of-band ICMP probing operates with $< 0.05\%$ CPU overhead and zero interference with payload TCP sockets.
   - It **never modifies kernel routes**. It is used standalone for continuous dual-path health inspection and logging.
2. **`SteeringController` is the Active Decision & Actuation Engine:**
   - Embeds `RawICMPProber` for latency monitoring while implementing the $2 \times 2$ policy decision taxonomy.
   - Upon confirming an SLA breach, executes atomic Linux Netlink default route replacements (`ip route replace default via ... dev ...`) within sub-6 milliseconds.
3. **Phase 3 Benchmarks invoke `SteeringController` directly:**
   - Both `run_hero_trace.py` and `run_stochastic_eval.py` import `SteeringController` because evaluating reaction delay, false handovers, and route-flapping requires the full closed loop: **Sense** (ICMP) $\to$ **Decide** (Policy state machine) $\to$ **Actuate** (Netlink route replacement).

---

## 4. Reproduction & Execution Guide

### Prerequisites & Dependencies
- Linux OS (Kernel 5.15+ with network namespaces `ip netns` and Traffic Control `tc`).
- Python 3.10+ with `requirements.txt` installed (`matplotlib`, `pandas`, `numpy`, `scipy`).
- System packages: `iproute2`, `lftp`, and `python3-pyftpdlib` (required for Phase 1 file transfer migration tests).
- Root privileges (`sudo`) for Linux network namespace management, Netlink route modification, and raw ICMP sockets.

```bash
git clone https://github.com/<your-org>/adaptive-telemetry-steering.git
cd adaptive-telemetry-steering
pip install -r requirements.txt
```

---

### Step 1: Initialize Emulation Topology
The 3-namespace network topology must be configured prior to executing live experiments:
```bash
sudo ./topology/setup_topology.sh setup
```
To inspect the virtual interfaces, routing tables, and traffic control queuing disciplines:
```bash
sudo ./topology/setup_topology.sh status
```
To verify end-to-end multi-homed path connectivity and latency:
```bash
sudo ./topology/setup_topology.sh test
```

---

### Step 2: Phase 1 — Baseline Migration & Transport Sensitivity
Phase 1 benchmarks application-level session migration and evaluates TCP Reno vs. CUBIC under emulated link impairments (latency, loss, jitter).

#### 2.1 Single Migration Benchmark
Run a single file transfer migration test (spawns an internal FTP server inside `ns_server`, transfers a payload, triggers a route switch, and measures reconnection overhead):
```bash
# Syntax: sudo ./experiments/baselines/lftp-migration-test.sh [RATE] [LATENCY] [JITTER] [LOSS] [NAME] [OUTPUT_DIR] [FILE_SIZE_MB] [CWND_MODE]
# Example: 100 MB transfer, 50 Mbit rate, 25 ms delay, 5 ms jitter, 0.5% loss using CUBIC:
sudo ./experiments/baselines/lftp-migration-test.sh 50mbit 25ms 5ms 0.5% single_test ./results 100 cubic
```

#### 2.2 Automated Batch Parametric Sweeps
To run an automated suite of parametric sweeps (latency sweep 10–160 ms, loss sweep 0.1–5%, jitter sweep 2–20 ms across multiple iterations):
```bash
sudo python3 experiments/baselines/batch_runner.py experiments/baselines/config.json
```
*(Pre-compiled master summary datasets from Phase 1 sweeps are already provided under `data/baselines/`)*.

---

### Step 3: Phase 2 — Active Telemetry Daemon & Flow Steering

#### 3.1 Running the Telemetry Daemon Standalone
The asynchronous telemetry daemon ([`core/telemetry_daemon.py`](file:///home/ubuntu/papajohn/AMAZING_6G/PAPER/NEW_APPROACH/adaptive_telemetry_steering/core/telemetry_daemon.py)) monitors path health concurrently across Link A (Fiber: `10.0.1.1`) and Link B (Cellular: `10.0.2.1`) toward the invariant destination (`10.0.3.2`) without head-of-line socket blocking.

To execute the daemon live with real-time console updates:
```bash
# Run for 50 probes at 100 ms intervals (10 Hz) with SLA threshold tau = 65.0 ms
sudo python3 core/telemetry_daemon.py --interval 0.1 --tau 65.0 --window 3 --count 50 --log data/telemetry.csv
```

**Daemon Command-Line Flags:**
| Flag | Default | Description |
| :--- | :--- | :--- |
| `--interval` | `0.1` | Telemetry polling period $T_{\text{poll}}$ in seconds (0.1s = 100 ms / 10 Hz). |
| `--tau` | `65.0` | Target SLA latency ceiling in milliseconds. |
| `--window` | `3` | Sliding window sample count $N$ for moving average calculation. |
| `--count` | `None` | Number of probe ticks to execute (defaults to running indefinitely until `Ctrl+C`). |
| `--log` | `None` | Path to export real-time CSV telemetry metrics. |
| `--quiet` | `False` | Suppress console tabular output (for background/headless execution). |

**Real-Time Output Stream:**
```text
[INFO] Telemetry Daemon Started [Interval: 100ms, SLA: 65.0ms]
Tick   | Link A (Fiber)       | Link B (Cellular)                | Counters (B)
----------------------------------------------------------------------------
0      | 4.25 ms              | 50.59 ms (mean: 50.59 ms)        | c_raw=0 c_win=0
1      | 4.48 ms              | 50.57 ms (mean: 50.58 ms)        | c_raw=0 c_win=0
2      | 4.49 ms              | 50.52 ms (mean: 50.56 ms)        | c_raw=0 c_win=0
```
* `c_raw`: Temporal persistence counter tracking consecutive ticks where $RTT(t) > \tau$.
* `c_win`: Persistence counter tracking consecutive ticks where sliding window mean $\mu_{\mathcal{W}}(t) > \tau$.

#### 3.2 Running the Reactive Steering Controller Standalone
The flow steering controller ([`core/steering_controller.py`](file:///home/ubuntu/papajohn/AMAZING_6G/PAPER/NEW_APPROACH/adaptive_telemetry_steering/core/steering_controller.py)) combines live ICMP telemetry with atomic Linux Netlink default route replacement (`ip route replace default via ...`):
```bash
# Run Policy 2 (Raw + Hysteresis, K=2) for 20.0 seconds
sudo python3 core/steering_controller.py --policy 2 --tau 65.0 --interval 0.1 --duration 20.0
```
**Supported Policies (`--policy`):**
* `1`: **Strict Raw** ($K=1$, memoryless trigger).
* `2`: **Raw + Hysteresis** ($K=2$, Pareto-optimal confirmation filter).
* `3`: **Rolling Window** ($N=3, K=1$, moving average smoothing).
* `4`: **Window + Hysteresis** ($N=3, K=2$, dual-stage conservative filter).

---

### Step 4: Phase 3 — Live Benchmark Suites

#### 4.1 Deterministic Hero Trace (Micro-Benchmark)
Runs a 20.0-second controlled timeline evaluating transient micro-bursts ($t = 5.0\text{ s}$) and persistent structural link failure ($t = 10.0\text{ s}$):
```bash
# Evaluate all 4 policies sequentially and export time-series datasets to data/hero_trace/
sudo python3 experiments/hero_trace/run_hero_trace.py --policy all --duration 20.0
```

#### 4.2 Randomized Stochastic Stress-Testing (400 Trials)
Evaluates 100 independent randomized Monte Carlo runs per policy across Poisson noise bursts, Rayleigh fading, and variable step failure onsets:
```bash
# Run full 400-trial suite with fixed seed for statistical reproducibility
sudo python3 experiments/stochastic/run_stochastic_eval.py --runs 100 --duration 20.0 --seed 42
```
*(Datasets will be saved to `data/stochastic/stochastic_trials.csv` and `data/stochastic/stochastic_summary.json`)*.

---

### Step 5: Reproduce Manuscript Figures & LaTeX Tables
All empirical datasets from our experiments are pre-included under `data/`. To re-generate all publication-grade plots and print LaTeX Tables V and VI:
```bash
python3 analysis/analyze_phase3.py
```
**Outputs:**
- Publication-ready vector PDFs and PNGs saved into `figures/`.
- Ready-to-paste LaTeX code for **Table V (Deterministic Hero Trace Metrics)** and **Table VI (Empirical Stochastic Aggregates)** printed to stdout.

---

### Step 6: Teardown Emulation Topology
When finished, cleanly remove the virtual network namespaces and veth pairs:
```bash
sudo ./topology/setup_topology.sh teardown
```

---

## 5. Evaluated Steering Policies

| ID | Policy Name | Mechanism | Characteristics |
| :---: | :--- | :--- | :--- |
| **P1** | **Strict Raw** | Instantaneous trigger ($K=1$) | Fastest reaction; high route-flapping under transient bursts (88% false handovers). |
| **P2** | **Raw + Hysteresis** | Consecutive confirmation ($K=2$) | **Pareto Optimal**: Filters transient bursts; 121 ms detection lag; 10% false handovers. |
| **P3** | **Rolling Window** | Sliding average ($N=3, K=1$) | Fast reaction (67 ms); moderate vulnerability to heavy bursts (35% false handovers). |
| **P4** | **Window + Hysteresis** | Sliding average + hysteresis | Ultra-conservative; 143 ms detection lag; lowest false alarm rate under non-bursty noise. |

---

## 6. License
This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
