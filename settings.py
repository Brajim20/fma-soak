"""Harness settings: config.json (committed, nothing instance-specific), then config.local.json (git-ignored, a
local run's instance), then environment variables (CI: repository variables). Later sources win.

  FLEET_URL              the Fleet instance, e.g. https://fleet.example.com
  FMA_SOAK_FLEET_ID      the fleet the harness works in (it never touches anything outside it)
  FMA_SOAK_HOST_DARWIN   macOS soak host IDs, comma-separated; the first is the primary (canaries, update path,
                         scenarios, patch when closed), the others only take new apps from the coverage sample
  FMA_SOAK_HOST_WINDOWS  the same for Windows
  FMA_SOAK_POOL_DARWIN   optional: pool slots, comma-separated numbers (e.g. 90001,90002). Each slot is one GitHub
                         macOS runner that enrolls itself, samples its share of new apps and deletes its host when its
                         job ends (pool.py). Slots are named, not host IDs: the runner's host ID changes every run.
  FMA_CATALOG_REPO       a fleetdm/fleet checkout (CI: a sparse clone of ee/maintained-apps)
"""
import json, os

WS = os.path.dirname(os.path.abspath(__file__))


def load():
    cfg = json.load(open(os.path.join(WS, "config.json")))
    local = os.path.join(WS, "config.local.json")
    if os.path.exists(local):
        cfg.update(json.load(open(local)))
    env = os.environ
    if env.get("FLEET_URL"):
        cfg["instance"] = env["FLEET_URL"]
    if env.get("FMA_SOAK_FLEET_ID"):
        cfg["soak_fleet_id"] = int(env["FMA_SOAK_FLEET_ID"])
    for plat in ("darwin", "windows"):
        if env.get(f"FMA_SOAK_HOST_{plat.upper()}"):
            cfg.setdefault("hosts", {})[plat] = [int(x) for x in env[f"FMA_SOAK_HOST_{plat.upper()}"].split(",") if x.strip()]
        if env.get(f"FMA_SOAK_POOL_{plat.upper()}"):
            cfg.setdefault("pool", {})[plat] = [int(x) for x in env[f"FMA_SOAK_POOL_{plat.upper()}"].split(",") if x.strip()]
    missing = [k for k in ("instance", "soak_fleet_id", "hosts") if not cfg.get(k)]
    if missing:
        raise SystemExit(f"missing settings {missing}: set them in config.local.json or the environment (see settings.py)")
    return cfg


def catalog_repo(cfg):
    return os.environ.get("FMA_CATALOG_REPO") or cfg.get("catalog_path", "").split("/ee/")[0]
