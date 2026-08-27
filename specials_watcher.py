"""
Opt-in specials watcher for Shadow Core.

Polls ONLY the stores the user has explicitly put on the watch list
(accounts_sim.get_watched_stores()). If that list is empty, this thread
does nothing but sleep -- no store is ever checked without the user having
said "watch X" first. This is a separate, much narrower thread than
watcher.py's file/screen monitor, and intentionally so: mixing "things I
watch for autonomy" with "stores I want specials from" would make the
noise-control harder to reason about.
"""
import time

from PyQt6.QtCore import QThread, pyqtSignal

from config import load_config
import accounts_sim
import services_sim


class SpecialsWatcherThread(QThread):
    specials_found = pyqtSignal(str, list)  # (store, [(name, price), ...])

    def __init__(self):
        super().__init__()
        self._running = True

    def stop(self):
        self._running = False

    def run(self):
        while self._running:
            cfg = load_config()
            interval = cfg.get("specials_poll_interval_sec", 60)

            for store in accounts_sim.get_watched_stores():
                catalog = services_sim.get_any_catalog(store)
                if not catalog:
                    continue
                changes = accounts_sim.check_for_new_specials(store, catalog)
                if changes:
                    self.specials_found.emit(store, changes)

            time.sleep(interval)
