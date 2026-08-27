"""
Self-directed task loop for Shadow Core.

Given a high-level goal, this asks the model to produce a short JSON plan,
then executes each step (shell command / file write / web fetch) using the
same primitives as the original one-shot chat mode, feeding results back to
the model so it can adapt the remaining steps. It stops when the model
reports DONE, when it hits max_plan_steps, or when it's told to stop.

Deliberately excluded from autonomous execution: anything that moves money.
The payment flow in shadow_core.py is never invoked from here.
"""
import json
import re
import time

import ollama

from config import load_config
import memory_store
from web_tool import fetch_url
import sensors

PLANNER_SYSTEM_PROMPT = """You are Shadow Core's autonomous planning module.
Given a GOAL, respond with ONLY a JSON object, no prose, no markdown fences:

{
  "thought": "one sentence about your reasoning",
  "status": "CONTINUE" or "DONE",
  "action": {
    "type": "shell" | "write_file" | "web_fetch" | "camera_capture" | "get_location" | "remember" | "none",
    "command": "<shell command, if type=shell>",
    "path": "<relative file path, if type=write_file>",
    "content": "<file content, if type=write_file>",
    "url": "<url, if type=web_fetch>",
    "key": "<fact key, if type=remember>",
    "value": "<fact value, if type=remember>"
  }
}

Notes on the physical-sensor actions:
- "camera_capture" grabs exactly one still frame from the camera -- use it only when the goal
  genuinely needs to see the physical environment, not as a default action.
- "get_location" performs one approximate (usually city-level) location lookup -- use it only
  when the goal needs to know where the machine is.
- Neither of these starts a recording or a monitoring loop. Never propose calling either of
  them repeatedly in a tight loop "to keep watch" -- that capability does not exist here on
  purpose and any such plan will be rejected.

Rules:
- Never propose an action that sends money, makes a payment, or touches bank_sim.py / PaySh
  functions. That capability is intentionally off-limits to autonomous planning.
- Prefer the smallest safe next step over trying to do everything in one action.
- Set status "DONE" as soon as the goal is satisfied or you determine it cannot be completed.
- If you are unsure or the goal is ambiguous, set status "DONE" and explain why in "thought"
  rather than guessing destructively.
"""

FORBIDDEN_PATTERNS = [
    r"\bbank_sim\b",
    r"\bcreate_payment_request\b",
    r"\bpayshap\b",
    r"\brm\s+-rf\s+/(?:\s|$)",   # rm -rf / (root wipe) -- plain text, not JSON-escaped
    r"\bmkfs\b",
    r"\bdd\s+if=.*of=/dev/",
]


def _is_forbidden(action):
    # Check the raw field values as plain text (not JSON-encoded) so patterns
    # like a trailing "/" aren't hidden behind an escaped quote.
    blob = " ".join(str(v) for v in action.values() if v is not None).lower()
    return any(re.search(p, blob, re.IGNORECASE) for p in FORBIDDEN_PATTERNS)


class AutonomousAgent:
    """
    Runs a bounded plan-act-observe loop for a single goal.
    `execute_shell` and `write_file` are injected from shadow_core.py so this
    module reuses the exact same execution primitives (and log paths) as the
    manual `$ ...` command mode.
    """

    def __init__(self, execute_shell_fn, write_file_fn, on_event=None):
        self.execute_shell = execute_shell_fn
        self.write_file = write_file_fn
        self.on_event = on_event or (lambda msg: None)
        self.stop_requested = False
        # Hard per-goal cap: a single autonomous goal may take at most one
        # camera frame and one location fix, full stop. This is what keeps
        # "trigger when required" from ever becoming "poll it every step".
        self._sensor_uses = {"camera_capture": 0, "get_location": 0}
        self._sensor_limit = 1

    def request_stop(self):
        self.stop_requested = True

    def run(self, goal, source="goal"):
        cfg = load_config()
        max_steps = cfg.get("max_plan_steps", 8)
        memory_store.log_action_event(source, f"Goal started: {goal}")

        transcript = [
            {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
            {"role": "user", "content": f"GOAL: {goal}\n\nRelevant memory:\n{memory_store.build_memory_context()}"},
        ]

        for step_num in range(1, max_steps + 1):
            if self.stop_requested:
                self.on_event(f"[AUTONOMOUS] Stop requested, halting after {step_num - 1} step(s).")
                break

            plan = self._get_plan(transcript)
            if plan is None:
                self.on_event("[AUTONOMOUS] Planner returned unparseable output, stopping.")
                break

            self.on_event(f"[AUTONOMOUS step {step_num}] {plan.get('thought', '')}")

            action = plan.get("action", {}) or {}
            if _is_forbidden(action):
                self.on_event("[AUTONOMOUS] Blocked: proposed action touches payments or a destructive "
                               "system operation. Autonomous mode cannot do this -- use the manual "
                               "'$ ...' command or the pay flow yourself.")
                observation = "[BLOCKED] Action rejected by safety filter. Choose a different next step."
            else:
                observation = self._act(action)

            transcript.append({"role": "assistant", "content": json.dumps(plan)})
            transcript.append({"role": "user", "content": f"OBSERVATION: {observation}"})

            if plan.get("status") == "DONE":
                self.on_event(f"[AUTONOMOUS] Goal complete after {step_num} step(s).")
                break
        else:
            self.on_event(f"[AUTONOMOUS] Reached max_plan_steps ({max_steps}) without completion.")

        memory_store.log_action_event(source, f"Goal ended: {goal}")

    def _get_plan(self, transcript):
        try:
            res = ollama.chat(model="qwen2.5-coder", messages=transcript)
            raw = res["message"]["content"].strip()
            raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
            return json.loads(raw)
        except Exception as e:
            self.on_event(f"[AUTONOMOUS] Planner error: {e}")
            return None

    def _act(self, action):
        atype = action.get("type", "none")
        try:
            if atype == "shell":
                cmd = action.get("command", "")
                self.on_event(f"[AUTONOMOUS EXEC] {cmd}")
                result = self.execute_shell(cmd)
                memory_store.log_action_event("goal", f"shell: {cmd}")
                return result[:2000]

            if atype == "write_file":
                path = action.get("path", "")
                content = action.get("content", "")
                self.on_event(f"[AUTONOMOUS WRITE] {path}")
                result = self.write_file(path, content)
                memory_store.log_action_event("goal", f"write_file: {path}")
                return result

            if atype == "web_fetch":
                url = action.get("url", "")
                self.on_event(f"[AUTONOMOUS FETCH] {url}")
                result = fetch_url(url)
                memory_store.log_action_event("goal", f"web_fetch: {url}")
                return result[:2000]

            if atype == "remember":
                key = action.get("key", "")
                value = action.get("value", "")
                memory_store.remember_fact(key, value)
                return f"[remembered] {key} = {value}"

            if atype == "camera_capture":
                if self._sensor_uses["camera_capture"] >= self._sensor_limit:
                    return "[BLOCKED] camera_capture already used once this goal -- one still frame per goal, no more."
                self._sensor_uses["camera_capture"] += 1
                self.on_event("[AUTONOMOUS CAMERA] Capturing one frame...")
                ok, result = sensors.capture_camera_frame()
                memory_store.log_action_event("goal", f"camera_capture: {'ok' if ok else result}")
                return f"[camera] {'captured -> ' + result if ok else 'failed -> ' + result}"

            if atype == "get_location":
                if self._sensor_uses["get_location"] >= self._sensor_limit:
                    return "[BLOCKED] get_location already used once this goal -- one lookup per goal, no more."
                self._sensor_uses["get_location"] += 1
                self.on_event("[AUTONOMOUS LOCATION] Looking up approximate location...")
                loc = sensors.get_location()
                memory_store.log_action_event("goal", f"get_location: {loc}")
                return f"[location] {loc}"

            return "[no-op]"
        except Exception as e:
            return f"[action error] {e}"
