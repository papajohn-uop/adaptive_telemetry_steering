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

## 3. Quick Start & Reproduction

### Prerequisites
- Linux OS (kernel 5.15+ with network namespaces and `iproute2` installed).
- Python 3.10+.
- Root privileges (`sudo`) for Linux network namespace (`ip netns`) and raw ICMP socket management.

### Installation
```bash
git clone https://github.com/<your-org>/adaptive-telemetry-steering.git
cd adaptive-telemetry-steering
pip install -r requirements.txt
```

### Step 1: Set Up Emulation Topology
```bash
sudo ./topology/setup_topology.sh setup
```
This creates:
- `ns_client`: Multi-homed client on Link A (`10.0.1.1`) and Link B (`10.0.2.1`).
- `ns_router`: Middlebox routing Link A (`10.0.1.2`), Link B (`10.0.2.2`), and Sink (`10.0.3.1`).
- `ns_server`: Destination endpoint (`10.0.3.2`).

### Step 2: Reproduce Publication Figures & Tables
All empirical datasets are pre-included under `data/`. To re-generate all publication plots and print LaTeX Tables V and VI:
```bash
python3 analysis/analyze_phase3.py
```
Outputs:
- Figures saved to `figures/*.pdf` and `figures/*.png`.
- LaTeX Tables V (Hero Trace) and VI (Stochastic Aggregates) printed directly to stdout.

### Step 3: Run Deterministic Hero Trace (Live)
```bash
sudo python3 experiments/hero_trace/run_hero_trace.py
```

### Step 4: Run Randomized Stochastic Benchmark Suite (Live)
```bash
sudo python3 experiments/stochastic/run_stochastic_eval.py --runs 100 --duration 20.0 --seed 42
```

### Step 5: Teardown Topology
```bash
sudo ./topology/setup_topology.sh teardown
```

---

## 4. Evaluated Steering Policies

| ID | Policy Name | Mechanism | Characteristics |
| :---: | :--- | :--- | :--- |
| **P1** | **Strict Raw** | Instantaneous trigger ($K=1$) | Fastest reaction; high route-flapping under transient bursts (88% false handovers). |
| **P2** | **Raw + Hysteresis** | Consecutive confirmation ($K=2$) | **Pareto Optimal**: Filters transient bursts; 121 ms detection lag; 10% false handovers. |
| **P3** | **Rolling Window** | Sliding average ($N=3, K=1$) | Fast reaction (67 ms); moderate vulnerability to heavy bursts (35% false handovers). |
| **P4** | **Window + Hysteresis** | Sliding average + hysteresis | Ultra-conservative; 143 ms detection lag; lowest false alarm rate under non-bursty noise. |

---

## 5. License
This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
