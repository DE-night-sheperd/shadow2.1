"""
Central configuration for Shadow Core autonomy features.
Edit config.json (auto-created on first run) to tune behavior without touching code.
"""
import json
import os

DIR = os.path.dirname(os.path.realpath(__file__))
DATA_DIR = os.path.join(DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")

DEFAULTS = {
    # Master switch. When False, the app behaves exactly like the original
    # one-shot assistant: no watcher thread, no self-triggered goals.
    "autonomous_mode": False,

    # --- Self-directed task loop ---
    "max_plan_steps": 8,           # hard ceiling on steps per autonomous goal
    "max_step_retries": 2,         # retries per failed step before abandoning it

    # --- Proactive monitoring ---
    "watch_dirs": ["workspace"],   # relative to DIR; extra dirs the agent watches for file events
    "watch_poll_interval_sec": 5,  # how often to check for screen changes
    "screen_change_threshold": 12, # perceptual-hash distance that counts as "something changed"
    "watch_cooldown_sec": 30,      # minimum gap between two auto-triggered goals

    # --- Broader tool access ---
    "allow_web_fetch": True,       # agent may fetch (GET only) URLs it is given or infers
    "web_fetch_allowlist": [],     # empty = no domain restriction; add domains to restrict
    "web_fetch_max_bytes": 200_000,

    # --- Safety rails that stay on regardless of autonomous_mode ---
    # Financial transfers always require the existing human-in-the-loop
    # out-of-band auth dialog. This flag cannot be set to False from here
    # on purpose -- see shadow_core.py process_command().
    "payments_require_human_auth": True,
    "action_log_path": "logs/actions.log",
}


def load_config():
    if not os.path.exists(CONFIG_PATH):
        save_config(DEFAULTS)
        return dict(DEFAULTS)
    try:
        with open(CONFIG_PATH, "r") as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}
    merged = dict(DEFAULTS)
    merged.update(cfg)
    # Hard-enforce the one non-configurable safety rail.
    merged["payments_require_human_auth"] = True
    return merged


def save_config(cfg):
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)
