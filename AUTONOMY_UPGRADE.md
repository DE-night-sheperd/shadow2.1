# Shadow Core — Autonomy Upgrade

This adds four new modules on top of the original one-shot HUD: `config.py`,
`memory_store.py`, `agent_loop.py`, and `watcher.py`. `shadow_core.py` wires
them in. Nothing about the original chat/exec/write flow was removed.

## What's new

**Self-directed task loop** (`agent_loop.py`)
Type `goal: <what you want>` instead of a normal prompt. The agent asks the
model for one JSON step at a time, executes it (shell / write_file /
web_fetch / remember), feeds the result back, and repeats until the model
reports `DONE` or it hits `max_plan_steps` (default 8). Say `stop autonomous`
to interrupt a running goal at any time.

**Persistent memory** (`memory_store.py`)
A local `memory.db` (SQLite) stores durable facts and per-session summaries.
Facts get pulled into every system prompt so the assistant remembers things
across restarts. The model can save a fact itself by emitting a line like
`REMEMBER: preferred_shell = zsh`; the agent loop can do the same via a
`remember` action.

**Proactive monitoring** (`watcher.py`)
When `AUTO` is toggled on (button in the header, or `autonomous_mode: true`
in `config.json`), a background thread watches your `workspace/` directory
for file changes (via `watchdog`) and periodically diffs screenshots (via
`Pillow`/`imagehash`) to detect meaningful screen changes. A detected event
gets turned into a small autonomous goal automatically, with a cooldown
(`watch_cooldown_sec`, default 30s) so it doesn't fire constantly.

**Broader tool access** (`web_tool.py`)
The planning loop can fetch a URL (GET only, size-capped, optional domain
allowlist via `web_fetch_allowlist` in `config.json`) when it decides it
needs to read something off the web.

## What I deliberately did *not* make autonomous

The payment flow (`send/pay/transfer ... to <number>`) still shows you the
out-of-band auth-channel picker (App push vs USSD) exactly as before, and
the agent's planner is hard-blocked from calling into `bank_sim.py` /
`create_payment_request` — see `FORBIDDEN_PATTERNS` in `agent_loop.py`. It's
also blocked from `rm -rf /`, `mkfs`, and raw-disk `dd` writes.

Everything the agent executes — manual `$ ...` commands, one-shot
write_file/exec_bash, and every autonomous-loop step — is appended to
`logs/actions.log` with a timestamp, so you have a full audit trail of what
it did on its own.

## Config

First run creates `config.json` next to `shadow_core.py` with sane defaults
(autonomy off). Key fields:

```json
{
  "autonomous_mode": false,
  "max_plan_steps": 8,
  "watch_dirs": ["workspace"],
  "watch_poll_interval_sec": 5,
  "screen_change_threshold": 12,
  "watch_cooldown_sec": 30,
  "allow_web_fetch": true,
  "web_fetch_allowlist": []
}
```

`payments_require_human_auth` is present but hard-forced to `true` by
`config.py` — it's not a real toggle, just documentation of the rail.

## New dependencies

`run_shadowcore.sh` now auto-installs `watchdog`, `Pillow`, `imagehash`,
`opencv-python`, `sounddevice`, `numpy`, and `openai-whisper` if missing. If
you run `shadow_core.py` directly instead of the launch script, install them
yourself:

```
pip install watchdog Pillow imagehash opencv-python sounddevice numpy openai-whisper
```

If any of these aren't available, the relevant feature degrades gracefully
(tells you what's missing in the HUD instead of crashing) rather than
silently failing.

## Sensors: camera, location, voice (`sensors.py`, `voice_input.py`)

These are **on-demand only** -- there is no background thread anywhere in
this project that keeps a mic or camera open, and none was added to
`watcher.py` on purpose. Each one is a single, visibly-logged action:

- **Camera** — say something like `look at me` / `take a photo`, or type
  `camera:` directly. Grabs exactly one frame (OpenCV, falling back to
  `fswebcam`/`imagesnap`), analyzes it with the vision model if you asked a
  question, and releases the device immediately.
- **Location** — say `where am I` / `near me`, or type `location:` directly.
  One IP-based geolocation lookup (city-level accuracy), or a single gpsd
  fix if you have real GPS hardware and `gpsd` running. Nothing is polled or
  tracked over time.
- **Voice** — hold the 🎤 button, speak, release. Transcribed locally with
  Whisper (audio never leaves the machine) and then handled exactly like
  typed text. No wake-word listening, no background recording.

Inside an autonomous `goal:`, the planner can also choose `camera_capture`
or `get_location` as a step -- but each is hard-capped at **one use per
goal run**, enforced in code (`agent_loop.py`), not just by prompting. A
plan that tries to call either twice gets a `[BLOCKED]` observation instead.
This is intentional: "trigger when required" should never be able to drift
into "poll it every step."

Every sensor use is written to `logs/actions.log` alongside the shell/write
actions, so there's one place to check what it looked at, where it thought
it was, or when it listened.

## Simulated everyday-services demo (`services_sim.py`)

⚠️ **This entire section is a hackathon-style simulation, not a real
integration.** No credentials for any bank, ride-hailing app, or grocery app
are stored or used anywhere in this project. `services_sim.py` generates
fake quotes/catalogs/orders locally, the same way `bank_sim.py` already
fakes a PayShap ledger.

**The LLM stays in the loop the whole time, and it's instructed to keep
saying so.** Instead of popup dialogs, mentioning rides/groceries/clothing/
food opens a "domain session": the relevant mock catalog/quotes/balance get
loaded and injected into the system prompt (`services_sim.build_domain_context`)
along with a standing instruction (`MOCK_DATA_DISCLOSURE`) that the model
must call out every time it references the data as demo/mock, not real. You
can then just talk to it normally — ask about prices, compare specials,
change your mind — while it stays aware of the loaded catalog. Domains:

- **Rides** — "get me a ride to \<destination\>" loads fake Uber/Bolt-style
  quotes (cheapest first) into context; discuss them normally.
- **Groceries** — "buy groceries" loads a store's catalog + specials +
  simulated wallet balance; ask about anything on it.
- **Clothing** — mentioning clothes/jeans/t-shirt/hoodie/outfit loads a
  simulated Mr Price / Woolworths Clothing / Pep catalog the same way.
- **Food delivery** — "pizza"/"Debonairs"/"order food" loads a fake menu.

Say **"confirm order"** (add which items, or which numbered ride) or
**"draft my list"** (groceries, in-store mode) to finalize. That triggers a
small extraction step — one more LLM call that reads the conversation and
pulls out a structured choice — then calls the matching `services_sim.*`
function and shows the result. Say **"cancel"** any time to drop the session
without ordering anything.

All simulated spending comes out of one shared fake wallet (`wallet_sim.db`,
starts at R3500) so specials/insufficient-funds behavior is visible in a
demo. `services_sim.get_order_history()` shows everything "ordered" so far.

## Account linking + opt-in specials alerts (`accounts_sim.py`, `specials_watcher.py`)

Referencing a store you haven't linked yet doesn't silently assume access —
it stops and asks:

> You don't have a Woolworths account linked yet. Type **link Woolworths**
> to create one (simulated) and I'll pull up their catalog.

This is deliberately modeled the way a real deployment would have to work:
each store requires its own authorization step (a real version would be
that store's actual login/OAuth, not a shared credential), not a single
blanket "the assistant can order from anywhere" assumption.

**Specials alerts are opt-in and quiet by default.** Nothing is watched
until you say so, and only watched stores are ever polled:

- `watch <store>` — add a store to the watch list.
- `only watch <store>` — replace the whole watch list with just this store
  (this is how you keep it to exactly one store, with zero leftover noise
  from anything you watched before).
- `stop watching <store>` / `unwatch <store>` — remove one.
- `what am I watching` — shows linked stores and the current watch list.

`specials_watcher.py` runs a background thread that polls only the stores
on that list (interval: `specials_poll_interval_sec` in `config.json`,
default 60s), diffs each store's specials against what it last saw, and
only speaks up when something is actually new or changed — checking the
same data twice produces no alert, verified in testing.

Because the demo catalogs are static, nothing will naturally change on its
own — for a live demo, use the presenter-only command:

```
simulate special: Woolworths: White Bread 700g = R12.99
```

which injects/updates that item's special price so the next poll of a
watched store fires a real `[SPECIALS ALERT]` in the HUD.

**To make any of this real** (not part of this delivery, and a materially
different project): each provider requires its own commercial/API agreement,
and each user would need to authenticate directly with that provider (their
own login/OAuth), not hand credentials to the assistant. `services_sim.py`'s
functions are structured so a real backend could eventually be swapped in
behind the same function signatures, but doing that swap is out of scope
here on purpose.

## Suggested next steps for you

- Try `AUTO: OFF` first with `goal: ...` manually a few times before
  flipping the toggle on, so you get a feel for how many steps a typical
  task takes and whether `max_plan_steps` needs adjusting.
- Watch `logs/actions.log` for the first day with autonomy on — it's your
  cheapest sanity check that it's doing what you'd expect.
- If you ever want it to touch directories outside `workspace/`, add them
  to `watch_dirs`, but know that `execute_shell` still runs with
  `cwd=WORKSPACE_DIR`, so file watching elsewhere won't automatically grant
  write access there — that'd be a separate, bigger change.
