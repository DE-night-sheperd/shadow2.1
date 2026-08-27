"""
Proactive monitoring for Shadow Core.

Two watchers run on a background QThread when autonomous_mode is on:
  1. Filesystem watcher (watchdog) over the configured watch_dirs.
  2. Screen-change watcher: periodically screenshots and compares a
     perceptual hash to the previous frame; a large enough change fires
     an event.

Both watchers emit plain-text descriptions through a callback rather than
acting directly -- shadow_core.py decides whether/how to turn an event into
an autonomous goal, so this module has no execution privileges of its own.
"""
import os
import time

from PyQt6.QtCore import QThread, pyqtSignal

from config import load_config

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    _HAS_WATCHDOG = True
except ImportError:
    _HAS_WATCHDOG = False

try:
    from PIL import Image
    import imagehash
    _HAS_IMAGEHASH = True
except ImportError:
    _HAS_IMAGEHASH = False


DIR = os.path.dirname(os.path.realpath(__file__))
SCREENSHOT_PATH = "/tmp/holo_screen.png"


class _FSHandler(FileSystemEventHandler if _HAS_WATCHDOG else object):
    def __init__(self, on_event):
        if _HAS_WATCHDOG:
            super().__init__()
        self.on_event = on_event

    def on_any_event(self, event):
        if event.is_directory:
            return
        self.on_event(f"File {event.event_type}: {event.src_path}")


class WatcherThread(QThread):
    """Emits a human-readable description of whatever it noticed."""
    event_detected = pyqtSignal(str)

    def __init__(self, capture_screen_fn):
        super().__init__()
        self.capture_screen = capture_screen_fn
        self._running = True
        self._last_hash = None
        self._last_trigger_time = 0

    def stop(self):
        self._running = False

    def run(self):
        cfg = load_config()
        observer = None

        if _HAS_WATCHDOG:
            observer = Observer()
            handler = _FSHandler(self._emit_fs_event)
            for rel_dir in cfg.get("watch_dirs", []):
                abs_dir = os.path.join(DIR, rel_dir)
                os.makedirs(abs_dir, exist_ok=True)
                observer.schedule(handler, abs_dir, recursive=True)
            observer.start()
        else:
            self.event_detected.emit(
                "[WATCHER] 'watchdog' package not installed -- filesystem watching disabled. "
                "Run: pip install watchdog"
            )

        poll_interval = cfg.get("watch_poll_interval_sec", 5)

        if not _HAS_IMAGEHASH:
            self.event_detected.emit(
                "[WATCHER] 'Pillow'/'imagehash' not installed -- screen watching disabled. "
                "Run: pip install Pillow imagehash"
            )

        while self._running:
            if _HAS_IMAGEHASH:
                self._check_screen(cfg)
            time.sleep(poll_interval)

        if observer:
            observer.stop()
            observer.join(timeout=2)

    def _emit_fs_event(self, description):
        if self._cooldown_ok():
            self.event_detected.emit(f"[WATCHER:FS] {description}")
            self._last_trigger_time = time.time()

    def _check_screen(self, cfg):
        if not self.capture_screen():
            return
        try:
            current_hash = imagehash.phash(Image.open(SCREENSHOT_PATH))
        except Exception:
            return

        if self._last_hash is not None:
            distance = current_hash - self._last_hash
            threshold = cfg.get("screen_change_threshold", 12)
            if distance >= threshold and self._cooldown_ok():
                self.event_detected.emit(
                    f"[WATCHER:SCREEN] Significant screen change detected (distance={distance})."
                )
                self._last_trigger_time = time.time()

        self._last_hash = current_hash

    def _cooldown_ok(self):
        cfg = load_config()
        return (time.time() - self._last_trigger_time) >= cfg.get("watch_cooldown_sec", 30)
