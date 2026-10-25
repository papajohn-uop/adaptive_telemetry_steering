import json
import subprocess
import time
import os
import sys
import signal

def cleanup_subprocesses(sig=None, frame=None):
    print("\n[Batch Runner] Received stop signal. Cleaning up background daemons...")
    try:
        subprocess.run(["sudo", "pkill", "-9", "-f", "cwnd_logger.sh"], stderr=subprocess.DEVNULL)
        subprocess.run(["sudo", "pkill", "-9", "-f", "pyftpdlib"], stderr=subprocess.DEVNULL)
    except Exception:
        pass
    if sig is not None:
        sys.exit(130)

signal.signal(signal.SIGINT, cleanup_subprocesses)
signal.signal(signal.SIGTERM, cleanup_subprocesses)

def run_batch(config_file_path, iterations_override=None, cooldown_override=None, results_override=None, filesize_override=None, cwnd_mode_override=None):
    print(f"Config file: {config_file_path}")
    if not os.path.exists(config_file_path):
        print("Error: config file missing.")
        return
        
    config_dir = os.path.dirname(os.path.abspath(config_file_path))

    with open(config_file_path, 'r') as f:
        config = json.load(f)
        
    g_settings = config.get("global_settings", {})
    iterations = iterations_override if iterations_override is not None else g_settings.get("iterations_per_config", 10)
    cooldown = cooldown_override if cooldown_override is not None else g_settings.get("cooldown_seconds", 5)
    core_script = g_settings.get("core_script_path", "./lftp-migration-test.sh")
    default_cwnd_mode = str(cwnd_mode_override or g_settings.get("cwnd_mode", "cubic")).lower()

    # Resolve core_script path
    if not os.path.isabs(core_script):
        cand_cwd = os.path.normpath(os.path.join(os.getcwd(), core_script))
        cand_cfg = os.path.normpath(os.path.join(config_dir, core_script))
        if os.path.exists(cand_cwd):
            core_script = cand_cwd
        elif os.path.exists(cand_cfg):
            core_script = cand_cfg

    # Determine results directory location
    results_dir = results_override or g_settings.get("results_dir") or g_settings.get("results_path") or g_settings.get("output_dir")
    if results_dir:
        if not os.path.isabs(results_dir):
            cand_cwd = os.path.normpath(os.path.join(os.getcwd(), results_dir))
            cand_cfg = os.path.normpath(os.path.join(config_dir, results_dir))
            if os.path.exists(cand_cwd):
                results_dir = cand_cwd
            else:
                results_dir = cand_cfg
    else:
        # Default to a 'results' directory inside the experiment folder
        results_dir = os.path.join(config_dir, "results")

    os.makedirs(results_dir, exist_ok=True)
    try:
        os.chmod(results_dir, 0o777)
    except Exception:
        pass

    print(f"Starting Automated Link Migration Sweep Loop...")
    print(f"Profile: {iterations} iterations per target with a {cooldown}s cool-down loop.")
    print(f"Default CWND Mode: {default_cwnd_mode}")
    print(f"Core Script: {core_script}")
    print(f"Results Directory: {results_dir}")
    
    # ============================================================
    # 0. PROCESSING PURE BASELINE CONTROL (Fixed & Explicitly Called)
    # ============================================================
    if "baseline_control" in config:
        print("\nLaunching Pure Baseline Control Group...")
        for b_name, params in config["baseline_control"].items():
            rate = params["rate"]
            lat = params["latency"]
            jit = params["jitter"]
            loss = params["loss"]
            out_name = params["output_name"]
            cwnd_mode = str(params.get("cwnd_mode", default_cwnd_mode)).lower()
            
            # Extract filesize or list of filesizes
            if filesize_override:
                fs_list = filesize_override
            else:
                fs_list = params.get("filesize") or params.get("filesizes") or g_settings.get("filesize", 100)
            if not isinstance(fs_list, list):
                fs_list = [fs_list]

            for fs in fs_list:
                print(f"\n   Target File Size: {fs}MB (Mode: {cwnd_mode})")
                for i in range(1, iterations + 1):
                    print(f"   -> Control Group [{b_name}] | Mode: {cwnd_mode} | File Size: {fs}MB | Iteration {i}/{iterations}...")
                    env = os.environ.copy()
                    env["CWND_MODE"] = cwnd_mode
                    subprocess.run(["sudo", core_script, rate, lat, jit, loss, out_name, results_dir, str(fs), cwnd_mode], check=True, env=env)
                    time.sleep(cooldown)


    # 1. Processing Parametric Sweeps
    if "parametric_sweeps" in config:
        for sweep_name, params in config["parametric_sweeps"].items():
            print(f"\nLaunching Sweep Strategy: {sweep_name}")
            rate = params["rate"]
            cwnd_mode = str(params.get("cwnd_mode", default_cwnd_mode)).lower()
            
            if filesize_override:
                fs_list = filesize_override
            else:
                fs_list = params.get("filesize") or params.get("filesizes") or g_settings.get("filesize", 100)
            if not isinstance(fs_list, list):
                fs_list = [fs_list]

            if sweep_name == "latency_sweep":
                var_list = [(v, params["jitter"], params["loss"]) for v in params["latency_values"]]
            elif sweep_name == "loss_sweep":
                var_list = [(params["latency"], params["jitter"], v) for v in params["loss_values"]]
            elif sweep_name == "jitter_sweep":
                var_list = [(params["latency"], v, params["loss"]) for v in params["jitter_values"]]
                
            for fs in fs_list:
                print(f"\n   [DIR] Sweep File Size: {fs}MB (Mode: {cwnd_mode})")
                for lat, jit, loss in var_list:
                    profile_name = f"sweep_{sweep_name}_{lat}_{jit}_{loss}".replace('%','').replace('.','')
                    for i in range(1, iterations + 1):
                        print(f"   -> Iteration {i}/{iterations} for Config [Rate: {rate} | Latency: {lat} | Jitter: {jit} | Loss: {loss} | Mode: {cwnd_mode} | FileSize: {fs}MB]")
                        env = os.environ.copy()
                        env["CWND_MODE"] = cwnd_mode
                        subprocess.run(["sudo", core_script, rate, lat, jit, loss, profile_name, results_dir, str(fs), cwnd_mode], check=True, env=env)
                        time.sleep(cooldown)

    # 2. Processing Scenario Profiles
    if "scenario_profiles" in config:
        print("\nLaunching Scenario Profile Evaluator...")
        for scenario in config["scenario_profiles"]:
            name = scenario["name"]
            rate = scenario["rate"]
            lat = scenario["latency"]
            jit = scenario["jitter"]
            loss = scenario["loss"]
            cwnd_mode = str(scenario.get("cwnd_mode", default_cwnd_mode)).lower()
            
            if filesize_override:
                fs_list = filesize_override
            else:
                fs_list = scenario.get("filesize") or scenario.get("filesizes") or g_settings.get("filesize", 100)
            if not isinstance(fs_list, list):
                fs_list = [fs_list]

            for fs in fs_list:
                print(f"\n   [DIR] Scenario Profile: {name} | Mode: {cwnd_mode} | File Size: {fs}MB")
                for i in range(1, iterations + 1):
                    print(f"   -> Scenario {name} | Mode: {cwnd_mode} | File Size: {fs}MB | Iteration {i}/{iterations}...")
                    env = os.environ.copy()
                    env["CWND_MODE"] = cwnd_mode
                    subprocess.run(["sudo", core_script, rate, lat, jit, loss, name, results_dir, str(fs), cwnd_mode], check=True, env=env)
                    time.sleep(cooldown)
                
    print("\nStructural Matrix Testing Suite Completed successfully!")

if __name__ == '__main__':
    # First parameter (mandatory): where to find the config file with the experiment description
    # Second parameter (optional): iterations per config
    # Third parameter (optional): cooldown seconds
    # Fourth parameter (optional): results directory override
    # Fifth parameter (optional): filesize override in MB (e.g. 50)
    # Sixth parameter (optional): cwnd_mode override (e.g. cubic or reno)

    if len(sys.argv) < 2:
        print("Usage: python3 batch_runner.py <config_file> [iterations] [cooldown] [results_dir] [filesize] [cwnd_mode]")
        sys.exit(1)
        
    config_file = sys.argv[1]
    if not os.path.exists(config_file):
        print(f"Error: Config file '{config_file}' not found.")
        sys.exit(1)

    iterations_override = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else None
    cooldown_override = float(sys.argv[3]) if len(sys.argv) > 3 else None
    results_override = sys.argv[4] if len(sys.argv) > 4 and sys.argv[4] != "" else None
    filesize_override = [int(sys.argv[5])] if len(sys.argv) > 5 and sys.argv[5].isdigit() else None
    cwnd_mode_override = sys.argv[6] if len(sys.argv) > 6 and sys.argv[6] != "" else None
        
    run_batch(config_file, iterations_override=iterations_override, cooldown_override=cooldown_override, results_override=results_override, filesize_override=filesize_override, cwnd_mode_override=cwnd_mode_override)