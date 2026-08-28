#!/usr/bin/env python3
import sys
import os
import json
import time
import subprocess
import html
import re
import ollama
from PyQt6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout,
                             QTextEdit, QLineEdit, QLabel, QMenu, QListWidget,
                             QPushButton, QSplitter, QInputDialog)
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal, QPoint
from PyQt6.QtGui import QFont, QPainter, QColor, QPen, QBrush, QAction

from bank_sim import create_payment_request, get_tx_status
from config import load_config, save_config
import memory_store
from agent_loop import AutonomousAgent
from watcher import WatcherThread
import sensors
from voice_input import VoiceRecorder
import services_sim
import accounts_sim
from specials_watcher import SpecialsWatcherThread

DIR = os.path.dirname(os.path.realpath(__file__))
SCREENSHOT_PATH = "/tmp/holo_screen.png"
DATA_DIR = os.path.join(DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)
HISTORY_FILE = os.path.join(DATA_DIR, "chat_history.json")
WORKSPACE_DIR = os.path.join(DIR, "workspace")
LOG_DIR = os.path.join(DIR, "logs")

os.makedirs(WORKSPACE_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)


def log_action(line):
    """Append-only audit trail for every autonomous or manual exec/write action."""
    path = os.path.join(DIR, load_config().get("action_log_path", "logs/actions.log"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} | {line}\n")

# Ensure Ollama daemon is reachable from inside Python
def ensure_ollama_alive():
    try:
        subprocess.run(["pgrep", "-x", "ollama"], check=True, stdout=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        subprocess.Popen(["ollama", "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(2)

ensure_ollama_alive()

def build_system_prompt(domain_context=None):
    memory_block = memory_store.build_memory_context()
    memory_section = f"\nLONG-TERM MEMORY:\n{memory_block}\n" if memory_block else ""
    domain_section = f"\nACTIVE DEMO SESSION CONTEXT:\n{domain_context}\n" if domain_context else ""
    return {
        "role": "system",
        "content": (
            "You are Shadow Core, a fully autonomous hands-free software engineer.\n"
            "WORKSPACE DIR: " + WORKSPACE_DIR + "\n"
            + memory_section
            + domain_section +
            "\nSTRICT EXECUTION PROTOCOL:\n"
            "Do NOT output markdown installation steps or setup guides for humans.\n"
            "Execute file operations and terminal builds directly using these exact code block tags:\n\n"
            "To write a file:\n"
            "```write_file:relative/path/to/file.ext\n<content>\n```\n\n"
            "To run shell commands (mkdir, npm, node, python, etc.):\n"
            "```exec_bash\n<command>\n```\n\n"
            "If you learn a durable fact worth remembering across sessions (a preference, "
            "a project path, a recurring constraint), say so plainly in your reply prefixed "
            "with 'REMEMBER: <key> = <value>' on its own line.\n"
        )
    }

def capture_screen():
    tools = [
        ["maim", SCREENSHOT_PATH],
        ["import", "-window", "root", SCREENSHOT_PATH],
        ["scrot", "-z", SCREENSHOT_PATH],
        ["spectacle", "-b", "-n", "-o", SCREENSHOT_PATH],
        ["grim", SCREENSHOT_PATH]
    ]
    for cmd in tools:
        try:
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            continue
    return False

def execute_shell(command):
    log_action(f"SHELL: {command}")
    try:
        res = subprocess.run(command, shell=True, cwd=WORKSPACE_DIR, capture_output=True, text=True, timeout=120)
        output = res.stdout if res.returncode == 0 else f"STDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"
        return output.strip() if output.strip() else "[Executed successfully]"
    except Exception as e:
        return f"[Execution Error: {e}]"

def write_file(rel_path, content):
    full_path = os.path.join(WORKSPACE_DIR, rel_path)
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    with open(full_path, "w", encoding="utf-8") as f:
        f.write(content)
    log_action(f"WRITE_FILE: {full_path}")
    return f"[File written: {full_path}]"

def determine_model(prompt):
    p = prompt.lower()
    screen_keywords = ["scan screen", "look at my screen", "analyze screen", "screenshot", "what is on my screen"]
    if any(k in p for k in screen_keywords):
        return "llava", "VISION_SCREEN"

    camera_keywords = ["look at me", "check the camera", "what do you see", "take a photo",
                        "use the camera", "look around", "what am i holding"]
    if any(k in p for k in camera_keywords):
        return "llava", "VISION_CAMERA"

    location_keywords = ["where am i", "my location", "near me", "nearby", "current location"]
    if any(k in p for k in location_keywords):
        return None, "LOCATION"

    coding_keywords = ["create", "build", "code", "write", "website", "script", "folder", "html", "python", "app", "css", "js"]
    if any(k in p for k in coding_keywords):
        return "qwen2.5-coder", "CODING/DEV"

    return "llama3", "SYSTEM REASONING"


def logged_capture_camera_frame():
    ok, result = sensors.capture_camera_frame()
    log_action(f"CAMERA_CAPTURE: {'ok -> ' + result if ok else 'failed -> ' + result}")
    memory_store.log_action_event("sensor", f"Camera capture: {'ok' if ok else result}")
    return ok, result


def logged_get_location():
    result = sensors.get_location()
    log_action(f"LOCATION_LOOKUP: {result}")
    memory_store.log_action_event("sensor", f"Location lookup: {result}")
    return result

class PaymentPollWorker(QThread):
    tx_status_signal = pyqtSignal(str, str, str)

    def __init__(self, tx_id, amount, recipient):
        super().__init__()
        self.tx_id = tx_id
        self.amount = amount
        self.recipient = recipient

    def run(self):
        while True:
            status = get_tx_status(self.tx_id)
            if status == "CLEARED_SUCCESS":
                self.tx_status_signal.emit(self.tx_id, self.amount, self.recipient)
                break
            time.sleep(1)

class AgentEngineWorker(QThread):
    finished = pyqtSignal(str, str)

    def __init__(self, history_turns, prompt, domain_context=None):
        super().__init__()
        self.history_turns = history_turns
        self.prompt = prompt
        self.domain_context = domain_context

    def run(self):
        try:
            ensure_ollama_alive()
            model_name, mode = determine_model(self.prompt)

            if mode == "LOCATION":
                loc = logged_get_location()
                if "error" in loc:
                    self.finished.emit(f"Location lookup failed: {loc['error']}", "LOCATION | ERROR")
                else:
                    text = (
                        f"Approximate location ({loc.get('source', 'unknown')}): "
                        f"{loc.get('city', '?')}, {loc.get('region', '?')}, {loc.get('country', '?')} "
                        f"(lat {loc.get('lat')}, lon {loc.get('lon')}). {loc.get('accuracy_note', '')}"
                    )
                    self.finished.emit(text, "LOCATION")
                return

            is_screen_vision = (mode == "VISION_SCREEN")
            is_camera_vision = (mode == "VISION_CAMERA")

            image_bytes = None
            if is_screen_vision:
                if not capture_screen():
                    self.finished.emit("System Error: Screen capture utility failed.", "ERROR")
                    return
                if os.path.exists(SCREENSHOT_PATH):
                    with open(SCREENSHOT_PATH, "rb") as f:
                        image_bytes = f.read()
            elif is_camera_vision:
                ok, result = logged_capture_camera_frame()
                if not ok:
                    self.finished.emit(f"System Error: Camera capture failed -- {result}", "ERROR")
                    return
                with open(result, "rb") as f:
                    image_bytes = f.read()

            messages = [build_system_prompt(self.domain_context)]
            for item in self.history_turns[-10:]:
                role = "user" if item.get("sender") == "Sir" else "assistant"
                messages.append({"role": role, "content": item.get("text", "")})

            if image_bytes is not None:
                messages.append({
                    "role": "user",
                    "content": self.prompt + "\nAnalyze this frame precisely.",
                    "images": [image_bytes]
                })
            else:
                messages.append({"role": "user", "content": self.prompt})

            try:
                res = ollama.chat(model=model_name, messages=messages)
            except Exception:
                fallback = "llama3" if image_bytes is None else "llava"
                res = ollama.chat(model=fallback, messages=messages)
                model_name = f"{fallback} (fallback)"

            response_content = res['message']['content']
            self.finished.emit(response_content, f"{model_name} | {mode}")

        except Exception as e:
            self.finished.emit(f"System Connection Error: {e}", "ERROR")

class DomainFinalizeWorker(QThread):
    """
    Runs off the UI thread: asks the LLM to extract a structured choice from
    the conversation (which ride, which items), then executes the matching
    services_sim.* simulated action. Never touches a real API -- see
    services_sim.py's module docstring.
    """
    finished_result = pyqtSignal(str, bool)  # (html-safe message, ok)

    EXTRACTION_PROMPTS = {
        "ride": (
            "Based on the conversation below, which numbered ride option did the user choose? "
            "Reply with ONLY JSON: {{\"chosen_index\": <1-based number>}}"
        ),
        "groceries": (
            "Based on the conversation below, list the exact item names (matching the catalog names "
            "given earlier) the user wants to buy, and whether they want it online or in-store. "
            "Reply with ONLY JSON: {{\"items\": [\"...\"], \"mode\": \"online\" or \"in-store\"}}"
        ),
        "clothing": (
            "Based on the conversation below, list the exact item names (matching the catalog names "
            "given earlier) the user wants to buy. Reply with ONLY JSON: {{\"items\": [\"...\"]}}"
        ),
        "food": (
            "Based on the conversation below, list the exact item names (matching the menu names given "
            "earlier) the user wants to order. Reply with ONLY JSON: {{\"items\": [\"...\"]}}"
        ),
    }

    def __init__(self, session, history_turns, latest_text):
        super().__init__()
        self.session = session
        self.history_turns = history_turns
        self.latest_text = latest_text

    def run(self):
        try:
            session = self.session
            convo = "\n".join(f"{t.get('sender')}: {t.get('text')}" for t in self.history_turns[-12:])
            convo += f"\nSir: {self.latest_text}"

            extraction_prompt = self.EXTRACTION_PROMPTS[session["type"]] + "\n\nCONVERSATION:\n" + convo
            res = ollama.chat(model="llama3", messages=[{"role": "user", "content": extraction_prompt}])
            raw = res["message"]["content"].strip()
            raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
            parsed = json.loads(raw)

            if session["type"] == "ride":
                idx = int(parsed["chosen_index"]) - 1
                quote = session["quotes"][idx]
                result = services_sim.simulate_ride_request(quote, session["destination"])
                msg = (
                    f"<b>[SIMULATED RIDE CONFIRMED]</b> {html.escape(result['summary'])} -- "
                    f"R{result['price']:.2f}, ETA {result['eta_min']} min. Ref: {result['confirmation_id']}.<br>"
                    f"<i>{html.escape(result['note'])}</i>"
                )
                self.finished_result.emit(msg, True)
                return

            items = parsed.get("items", [])
            if session["type"] == "groceries" and parsed.get("mode") == "in-store":
                line_items, total = services_sim.price_list(session["store"], items)
                lines = [f"- {li['name']}: R{li['price']:.2f}" if li.get("price") is not None else f"- {li['name']}: (not found)" for li in line_items]
                msg = (
                    f"<b>[SHOPPING LIST DRAFTED - {html.escape(session['store'])}]</b><br>" + "<br>".join(html.escape(l) for l in lines) +
                    f"<br><b>Estimated total: R{total:.2f}</b> -- take this list with you, no order was placed."
                )
                self.finished_result.emit(msg, True)
                return

            if session["type"] == "groceries":
                result = services_sim.simulate_online_order(session["store"], items)
            elif session["type"] == "clothing":
                result = services_sim.simulate_clothing_order(session["store"], items)
            else:  # food
                result = services_sim.simulate_food_order(session["vendor"], items)

            if not result["ok"]:
                self.finished_result.emit(f"<b>[SIMULATED ORDER FAILED]:</b> {html.escape(result['reason'])}", False)
                return

            lines = [f"- {li['name']}: R{li['price']:.2f}" for li in result["line_items"] if li.get("price") is not None]
            msg = (
                f"<b>[SIMULATED ORDER PLACED]</b><br>" + "<br>".join(html.escape(l) for l in lines) +
                f"<br><b>Total: R{result['total']:.2f}</b> | Remaining balance: R{result['remaining_balance']:.2f} | "
                f"Ref: {result['confirmation_id']}<br><i>{html.escape(result['note'])}</i>"
            )
            self.finished_result.emit(msg, True)

        except Exception as e:
            self.finished_result.emit(f"Could not finalize -- try being more specific about what you chose. ({e})", False)


class AutonomousGoalWorker(QThread):
    """Runs AutonomousAgent.run() off the UI thread and relays its events."""
    event = pyqtSignal(str)
    finished_goal = pyqtSignal()

    def __init__(self, agent, goal, source):
        super().__init__()
        self.agent = agent
        self.goal = goal
        self.source = source

    def run(self):
        self.agent.on_event = self.event.emit
        try:
            self.agent.run(self.goal, source=self.source)
        except Exception as e:
            self.event.emit(f"[AUTONOMOUS] Unhandled error: {e}")
        finally:
            self.finished_goal.emit()

class SpinningHoloOrb(QWidget):
    def __init__(self, hud_window):
        super().__init__()
        self.hud_window = hud_window
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.resize(120, 120)

        screen = QApplication.primaryScreen().geometry()
        self.move(screen.width() - 140, screen.height() - 180)

        self.angle = 0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.rotate)
        self.timer.start(30)
        self.drag_position = QPoint()

    def rotate(self):
        self.angle = (self.angle + 4) % 360
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        center_x, center_y, radius = self.width() // 2, self.height() // 2, 35

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(0, 255, 204, 180)))
        painter.drawEllipse(center_x - 12, center_y - 12, 24, 24)

        pen1 = QPen(QColor(0, 255, 204, 220), 3)
        painter.setPen(pen1)
        painter.drawArc(center_x - radius, center_y - radius, radius * 2, radius * 2, self.angle * 16, 120 * 16)
        painter.drawArc(center_x - radius, center_y - radius, radius * 2, radius * 2, (self.angle + 180) * 16, 120 * 16)

        pen2 = QPen(QColor(255, 170, 0, 200), 2)
        painter.setPen(pen2)
        r2 = radius + 8
        painter.drawArc(center_x - r2, center_y - r2, r2 * 2, r2 * 2, (-self.angle * 2) * 16, 80 * 16)
        painter.drawArc(center_x - r2, center_y - r2, r2 * 2, r2 * 2, (-self.angle * 2 + 180) * 16, 80 * 16)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            if self.hud_window.isVisible():
                self.hud_window.hide()
            else:
                self.hud_window.show()
                self.hud_window.raise_()
                self.hud_window.activateWindow()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self.drag_position)
            event.accept()

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.setStyleSheet("QMenu { background-color: #050c18; color: #00ffcc; border: 1px solid #00ffcc; } QMenu::item:selected { background-color: #00ffcc; color: #050c18; }")
        quit_action = QAction("🛑 Terminate Shadow Core", self)
        quit_action.triggered.connect(QApplication.instance().quit)
        menu.addAction(quit_action)
        menu.exec(event.globalPos())

class HoloHUD(QWidget):
    def __init__(self):
        super().__init__()
        self.history_data = self.load_history()
        self.current_session_id = str(int(time.time()))
        self.cfg = load_config()
        self.watcher_thread = None
        self.autonomous_agent = None
        self.autonomous_worker = None
        self.active_domain_session = None
        self.pending_link = None
        self.specials_watcher_thread = SpecialsWatcherThread()
        self.specials_watcher_thread.specials_found.connect(self.on_specials_found)
        self.specials_watcher_thread.start()
        self.init_ui()
        if self.cfg.get("autonomous_mode", False):
            self.start_watcher()

    def load_history(self):
        if os.path.exists(HISTORY_FILE):
            try:
                with open(HISTORY_FILE, "r") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def save_history(self):
        with open(HISTORY_FILE, "w") as f:
            json.dump(self.history_data, f, indent=2)

    def init_ui(self):
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.resize(840, 520)

        screen = QApplication.primaryScreen().geometry()
        self.move(int((screen.width() - 840) / 2), 120)

        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(15, 15, 15, 15)

        header = QHBoxLayout()
        title = QLabel("⚡ [SHADOW CORE - AUTONOMOUS HUD]")
        title.setFont(QFont("Monospace", 10, QFont.Weight.Bold))
        title.setStyleSheet("color: #00ffcc; letter-spacing: 1px;")

        btn_new = QPushButton("+ NEW SESSION")
        btn_new.setFont(QFont("Monospace", 9, QFont.Weight.Bold))
        btn_new.setStyleSheet("""
            QPushButton { background-color: #050c18; color: #00ffcc; border: 1px solid #00ffcc; border-radius: 4px; padding: 4px 10px; }
            QPushButton:hover { background-color: #00ffcc; color: #050c18; }
        """)
        btn_new.clicked.connect(self.new_chat)

        self.btn_auto = QPushButton()
        self.btn_auto.setFont(QFont("Monospace", 9, QFont.Weight.Bold))
        self.btn_auto.clicked.connect(self.toggle_autonomous_mode)
        self._refresh_auto_button()

        self.btn_mic = QPushButton("🎤 HOLD TO TALK")
        self.btn_mic.setFont(QFont("Monospace", 9, QFont.Weight.Bold))
        self.btn_mic.setStyleSheet("""
            QPushButton { background-color: #050c18; color: #ffaa00; border: 1px solid #ffaa00; border-radius: 4px; padding: 4px 10px; }
            QPushButton:pressed { background-color: #ffaa00; color: #050c18; }
        """)
        self.voice_recorder = VoiceRecorder()
        self.btn_mic.pressed.connect(self.on_mic_pressed)
        self.btn_mic.released.connect(self.on_mic_released)

        header.addWidget(title)
        header.addStretch()
        header.addWidget(self.btn_mic)
        header.addWidget(self.btn_auto)
        header.addWidget(btn_new)
        main_layout.addLayout(header)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        self.history_widget = QListWidget()
        self.history_widget.setFont(QFont("Monospace", 9))
        self.history_widget.setStyleSheet("""
            QListWidget {
                background-color: rgba(5, 12, 24, 230);
                color: #88aaee;
                border: 1px solid #00ffcc;
                border-radius: 6px;
            }
            QListWidget::item:selected {
                background-color: #00ffcc;
                color: #050c18;
                font-weight: bold;
            }
        """)
        self.history_widget.itemClicked.connect(self.load_session)
        splitter.addWidget(self.history_widget)

        right_container = QWidget()
        right_layout = QVBoxLayout(right_container)
        right_layout.setContentsMargins(0, 0, 0, 0)

        self.display = QTextEdit()
        self.display.setReadOnly(True)
        self.display.setFont(QFont("Monospace", 10))
        self.display.setStyleSheet("""
            QTextEdit {
                background-color: rgba(5, 12, 24, 215);
                color: #00ffcc;
                border: 1px solid #00ffcc;
                border-radius: 6px;
                padding: 10px;
            }
        """)
        right_layout.addWidget(self.display)

        self.input_field = QLineEdit()
        self.input_field.setFont(QFont("Monospace", 10))
        self.input_field.setPlaceholderText("Sir > Enter task prompt...")
        self.input_field.setStyleSheet("""
            QLineEdit {
                background-color: rgba(5, 12, 24, 230);
                color: #ffffff;
                border: 1px solid #00ffcc;
                border-radius: 5px;
                padding: 6px;
            }
        """)
        self.input_field.returnPressed.connect(self.process_command)
        right_layout.addWidget(self.input_field)

        splitter.addWidget(right_container)
        splitter.setSizes([220, 620])
        main_layout.addWidget(splitter)

        self.setLayout(main_layout)
        self.refresh_history_list()
        self.new_chat()

    def refresh_history_list(self):
        self.history_widget.clear()
        for session_id, logs in reversed(list(self.history_data.items())):
            label = logs[0]['text'][:22] + "..." if logs else session_id
            self.history_widget.addItem(f"Session #{session_id[-4:]}: {label}")

    def new_chat(self):
        self.current_session_id = str(int(time.time()))
        self.history_data[self.current_session_id] = []
        self.display.clear()
        self.display.append("<b>[SHADOW CORE]:</b> Server Active.<br>Workspace: <i>" + WORKSPACE_DIR + "</i>")

    def load_session(self, item):
        idx = self.history_widget.currentRow()
        session_keys = list(reversed(list(self.history_data.keys())))
        if idx < len(session_keys):
            self.current_session_id = session_keys[idx]
            self.display.clear()
            self.display.append(f"<b>[SHADOW CORE]:</b> Loaded Session #{self.current_session_id[-4:]}")
            for entry in self.history_data[self.current_session_id]:
                sender = entry.get("sender", "User")
                text = entry.get("text", "")
                safe_text = html.escape(text).replace("\n", "<br>")
                self.display.append(f"<b>{sender} ></b> {safe_text}")

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
        elif event.modifiers() == Qt.KeyboardModifier.ControlModifier and event.key() == Qt.Key.Key_Q:
            QApplication.instance().quit()
        else:
            super().keyPressEvent(event)

    def process_command(self):
        text = self.input_field.text().strip()
        if not text:
            return

        if text.lower() in ["quit", "exit", "shutdown"]:
            QApplication.instance().quit()
            return

        if text.startswith("$ "):
            cmd_to_run = text[2:]
            self.display.append(f"\n<b>Sir (Exec) ></b> <i>{html.escape(cmd_to_run)}</i>")
            result = execute_shell(cmd_to_run)
            safe_res = html.escape(result).replace("\n", "<br>")
            self.display.append(f"<span style='color:#00ffaa;'><b>[SYS OUTPUT]:</b><br>{safe_res}</span>")
            self.input_field.clear()
            return

        pay_match = re.search(r'(?:send|pay|transfer)\s+(?:r\s*)?(\d+(?:\.\d{2}?|))\s+to\s+(0\d{9})', text, re.IGNORECASE)
        if pay_match:
            amount = pay_match.group(1)
            recipient = pay_match.group(2)
            self.display.append(f"\n<b>Sir ></b> {html.escape(text)}")
            self.input_field.clear()

            items = ["1. Capitec Pay App Push Notification", "2. USSD Session (*120*3279#)"]
            item, ok = QInputDialog.getItem(self, "Out-of-Band Auth Channel", "Select Authorization Channel:", items, 0, False)
            
            if ok and item:
                auth_method = "USSD" if "USSD" in item else "APP"
                tx_id = create_payment_request(recipient, amount, auth_method)
                self.display.append(
                    f"<span style='color: #ffaa00;'><b>[PAYSHAP DISPATCH]:</b> Initiated outflow of R{amount} to {recipient} ({tx_id}).<br>"
                    f"Pushed to Tablet Security Token via <b>{auth_method}</b>. Awaiting authorization...</span>"
                )
                self.poll_worker = PaymentPollWorker(tx_id, amount, recipient)
                self.poll_worker.tx_status_signal.connect(self.payment_cleared)
                self.poll_worker.start()
            else:
                self.display.append("<span style='color: #ff5555;'><b>[CANCELLED]:</b> Payment request aborted.</span>")
            return

        if text.lower().startswith("goal:"):
            goal = text[len("goal:"):].strip()
            self.display.append(f"\n<b>Sir (Goal) ></b> <i>{html.escape(goal)}</i>")
            self.input_field.clear()
            self.launch_autonomous_goal(goal, source="manual_goal")
            return

        if text.lower() in ["location:", "location", "where am i"]:
            self.display.append(f"\n<b>Sir ></b> {html.escape(text)}")
            self.input_field.clear()
            loc = logged_get_location()
            if "error" in loc:
                self.display.append(f"<span style='color:#ff5555;'><b>[LOCATION]:</b> {html.escape(loc['error'])}</span>")
            else:
                self.display.append(
                    f"<span style='color:#00ffaa;'><b>[LOCATION - {loc.get('source')}]:</b> "
                    f"{html.escape(str(loc.get('city')))}, {html.escape(str(loc.get('region')))}, "
                    f"{html.escape(str(loc.get('country')))} (lat {loc.get('lat')}, lon {loc.get('lon')}). "
                    f"{html.escape(loc.get('accuracy_note', ''))}</span>"
                )
            return

        if text.lower() in ["camera:", "camera", "take a photo"]:
            self.display.append(f"\n<b>Sir ></b> {html.escape(text)}")
            self.input_field.clear()
            ok, result = logged_capture_camera_frame()
            if ok:
                self.display.append(f"<span style='color:#00ffaa;'><b>[CAMERA]:</b> Frame captured -> {html.escape(result)}</span>")
            else:
                self.display.append(f"<span style='color:#ff5555;'><b>[CAMERA]:</b> {html.escape(result)}</span>")
            return

        if text.lower() == "stop autonomous":
            self.input_field.clear()
            if self.autonomous_agent:
                self.autonomous_agent.request_stop()
                self.display.append("<span style='color:#ff5555;'><b>[AUTONOMOUS]:</b> Stop requested.</span>")
            else:
                self.display.append("<span style='color:#ff5555;'><b>[AUTONOMOUS]:</b> No goal is currently running.</span>")
            return

        if text.lower() in ["cancel order", "cancel", "never mind"] and self.active_domain_session:
            self.input_field.clear()
            self.active_domain_session = None
            self.display.append("<span style='color:#ff5555;'>[DEMO SESSION]: Cancelled, no order placed.</span>")
            return

        confirm_match = re.match(r'^(confirm order|draft my list)\b(.*)', text, re.IGNORECASE)
        if confirm_match and self.active_domain_session:
            self.display.append(f"\n<b>Sir ></b> {html.escape(text)}")
            self.input_field.clear()
            self.finalize_domain_session(text)
            return

        known_stores = list(services_sim.get_frequent_stores()) + services_sim.get_clothing_stores() + \
                       ["Checkers Sixty60", "Woolworths", "Pick n Pay", "Woolworths Clothing", "Mr Price", "Pep"]
        known_stores = sorted(set(known_stores))

        link_match = re.match(r'^link\s+(.+)$', text, re.IGNORECASE)
        if link_match:
            self.input_field.clear()
            self.display.append(f"\n<b>Sir ></b> {html.escape(text)}")
            store = self._match_known_store(link_match.group(1).strip(), known_stores)
            if not store:
                self.display.append(f"<span style='color:#ff5555;'>[LINK]: I don't recognise that store in the demo catalog.</span>")
                return
            accounts_sim.link_account(store)
            log_action(f"SIM_ACCOUNT_LINKED: store={store}")
            self.display.append(f"<span style='color:#00ffaa;'>[LINK]: {html.escape(store)} account linked (simulated). "
                                 f"In a real deployment this would be you authenticating directly with {html.escape(store)}.</span>")
            if self.pending_link and self.pending_link["store"] == store:
                domain = self.pending_link["domain"]
                self.pending_link = None
                self._load_store_session(domain, store)
            return

        watch_match = re.match(r'^(only\s+watch|watch)\s+(.+?)(?:\s+for specials)?$', text, re.IGNORECASE)
        if watch_match:
            self.input_field.clear()
            self.display.append(f"\n<b>Sir ></b> {html.escape(text)}")
            exclusive = watch_match.group(1).lower().startswith("only")
            store = self._match_known_store(watch_match.group(2).strip(), known_stores)
            if not store:
                self.display.append("<span style='color:#ff5555;'>[WATCH]: I don't recognise that store in the demo catalog.</span>")
                return
            accounts_sim.watch_store(store, exclusive=exclusive)
            log_action(f"SIM_WATCH_SET: store={store} exclusive={exclusive}")
            watched = accounts_sim.get_watched_stores()
            self.display.append(
                f"<span style='color:#00ffaa;'>[WATCH]: Now watching: {html.escape(', '.join(watched))}. "
                f"You'll only hear from me when one of these has a new/changed special.</span>"
            )
            return

        unwatch_match = re.match(r'^(?:stop watching|unwatch)\s+(.+)$', text, re.IGNORECASE)
        if unwatch_match:
            self.input_field.clear()
            self.display.append(f"\n<b>Sir ></b> {html.escape(text)}")
            store = self._match_known_store(unwatch_match.group(1).strip(), known_stores)
            if store:
                accounts_sim.unwatch_store(store)
                log_action(f"SIM_WATCH_REMOVED: store={store}")
            watched = accounts_sim.get_watched_stores()
            self.display.append(f"<span style='color:#00ffaa;'>[WATCH]: Watch list now: {html.escape(', '.join(watched)) if watched else '(empty)'}</span>")
            return

        if text.lower() in ["what am i watching", "what am i watching?", "watch list", "watchlist"]:
            self.input_field.clear()
            self.display.append(f"\n<b>Sir ></b> {html.escape(text)}")
            watched = accounts_sim.get_watched_stores()
            linked = accounts_sim.get_linked_stores()
            self.display.append(
                f"<span style='color:#00ffaa;'>Linked (simulated): {html.escape(', '.join(linked)) if linked else '(none)'}<br>"
                f"Watching for specials: {html.escape(', '.join(watched)) if watched else '(none)'}</span>"
            )
            return

        demo_special_match = re.match(r'^simulate special:\s*([^:]+):\s*(.+?)\s*=\s*R?([\d.]+)$', text, re.IGNORECASE)
        if demo_special_match:
            self.input_field.clear()
            self.display.append(f"\n<b>Sir ></b> {html.escape(text)}")
            store = self._match_known_store(demo_special_match.group(1).strip(), known_stores)
            item_name = demo_special_match.group(2).strip()
            price = float(demo_special_match.group(3))
            if not store:
                self.display.append("<span style='color:#ff5555;'>[DEMO]: Unrecognised store.</span>")
                return
            services_sim.add_special(store, item_name, price)
            log_action(f"DEMO_INJECT_SPECIAL: store={store} item={item_name} price={price}")
            self.display.append(
                f"<span style='color:#ffaa00;'>[DEMO]: Injected a special into {html.escape(store)}'s catalog -- "
                f"{html.escape(item_name)} now R{price:.2f}. If you're watching this store, the alert will fire "
                f"on the next poll.</span>"
            )
            return

        ride_match = re.search(r'\b(?:ride|lift|uber|bolt)\s+(?:to|home)\b', text, re.IGNORECASE) or \
                     re.search(r'\bget me (?:a|home)\b.*\bride\b', text, re.IGNORECASE) or \
                     text.lower().startswith("ride:")
        if ride_match:
            destination_match = re.search(r'\bto\s+(.+)$', text, re.IGNORECASE)
            destination = destination_match.group(1).strip() if destination_match else "home"
            quotes = services_sim.get_ride_quotes(destination)
            self.active_domain_session = {
                "type": "ride", "destination": destination, "quotes": quotes,
                "context": services_sim.build_domain_context("ride", destination=destination, quotes=quotes),
            }
            self.display.append(f"<span style='color:#ffaa00;'>{services_sim.SIMULATION_BANNER} (ride quotes loaded, ask me anything)</span>")
            log_action(f"SIM_SESSION_START: ride destination={destination}")
            # fall through -- let the normal chat call answer using the injected context

        elif re.search(r'\b(groceries|grocery)\b', text, re.IGNORECASE) or \
                re.search(r'\bshop(?:ping)?\s+for\s+food\b', text, re.IGNORECASE) or \
                re.search(r'\bbuy\s+(?:some\s+)?food\b', text, re.IGNORECASE):
            store = services_sim.get_frequent_stores()[0]
            if not self._start_store_session("groceries", store):
                self.display.append(f"\n<b>Sir ></b> {html.escape(text)}")
                self.input_field.clear()
                return
            # fall through -- either session was loaded, or a link prompt was shown and consumed

        elif re.search(r'\b(clothes|clothing|t-shirt|jeans|hoodie|outfit)\b', text, re.IGNORECASE):
            store = services_sim.get_clothing_stores()[0]
            if not self._start_store_session("clothing", store):
                self.display.append(f"\n<b>Sir ></b> {html.escape(text)}")
                self.input_field.clear()
                return

        elif re.search(r'\b(debonairs|pizza|order food)\b', text, re.IGNORECASE):
            vendor = "Debonairs Pizza"
            menu = services_sim.get_food_menu(vendor)
            balance = services_sim.get_balance()
            self.active_domain_session = {
                "type": "food", "vendor": vendor, "menu": menu,
                "context": services_sim.build_domain_context("food", vendor=vendor, menu=menu, balance=balance),
            }
            self.display.append(f"<span style='color:#ffaa00;'>{services_sim.SIMULATION_BANNER} (menu for {html.escape(vendor)} loaded, ask me anything)</span>")
            log_action(f"SIM_SESSION_START: food vendor={vendor}")

        self.display.append(f"\n<b>Sir ></b> {html.escape(text)}")
        active_history = self.history_data.get(self.current_session_id, [])
        self.input_field.clear()

        model_name, mode = determine_model(text)
        self.display.append(f"<span style='color: #ffaa00;'>[ROUTED TO MODEL: <b>{model_name}</b> ({mode})] Processing...</span>")

        self.worker = AgentEngineWorker(
            active_history, text,
            domain_context=self.active_domain_session["context"] if self.active_domain_session else None
        )
        self.history_data[self.current_session_id].append({"sender": "Sir", "text": text})

        self.worker.finished.connect(self.display_response)
        self.worker.start()

    def on_mic_pressed(self):
        self.display.append("<span style='color:#ffaa00;'>[MIC]: Listening... (release to send)</span>")
        log_action("MIC: recording started")
        self.voice_recorder.start()

    def on_mic_released(self):
        self.display.append("<span style='color:#ffaa00;'>[MIC]: Transcribing...</span>")
        text = self.voice_recorder.stop_and_transcribe()
        log_action(f"MIC: recording stopped, transcript length={len(text)}")
        if text.startswith("[voice error]"):
            self.display.append(f"<span style='color:#ff5555;'>{html.escape(text)}</span>")
            return
        if not text:
            self.display.append("<span style='color:#ff5555;'>[MIC]: No speech detected.</span>")
            return
        self.input_field.setText(text)
        self.process_command()

    def _refresh_auto_button(self):
        on = self.cfg.get("autonomous_mode", False)
        self.btn_auto.setText("AUTO: ON" if on else "AUTO: OFF")
        self.btn_auto.setStyleSheet(
            "QPushButton { background-color: %s; color: #050c18; border: 1px solid #00ffcc; "
            "border-radius: 4px; padding: 4px 10px; font-weight: bold; }"
            % ("#00ffcc" if on else "#333333")
        )

    def toggle_autonomous_mode(self):
        self.cfg["autonomous_mode"] = not self.cfg.get("autonomous_mode", False)
        save_config(self.cfg)
        self._refresh_auto_button()
        if self.cfg["autonomous_mode"]:
            self.display.append(
                "<span style='color:#00ffcc;'><b>[AUTONOMOUS MODE ON]:</b> Watching workspace files and "
                "screen for changes. Type <i>goal: &lt;task&gt;</i> any time to give it a direct objective, "
                "or <i>stop autonomous</i> to interrupt a running goal. Payments still always require your "
                "manual authorization.</span>"
            )
            self.start_watcher()
        else:
            self.display.append("<span style='color:#ff5555;'><b>[AUTONOMOUS MODE OFF]</b></span>")
            self.stop_watcher()

    def start_watcher(self):
        if self.watcher_thread is not None:
            return
        self.watcher_thread = WatcherThread(capture_screen)
        self.watcher_thread.event_detected.connect(self.on_watcher_event)
        self.watcher_thread.start()

    def stop_watcher(self):
        if self.watcher_thread is not None:
            self.watcher_thread.stop()
            self.watcher_thread.wait(2000)
            self.watcher_thread = None

    def on_watcher_event(self, description):
        self.display.append(f"<span style='color:#ffaa00;'>{html.escape(description)}</span>")
        memory_store.log_action_event("watcher", description)
        # Only self-trigger a goal for events that actually came from the watcher
        # (not the "package missing" notices), and only if nothing is already running.
        if description.startswith("[WATCHER:") and self.autonomous_agent is None:
            inferred_goal = (
                f"Something changed that you were watching: {description}. "
                "Decide if any follow-up action is warranted; if not, say status DONE immediately."
            )
            self.launch_autonomous_goal(inferred_goal, source="watcher")

    def launch_autonomous_goal(self, goal, source="goal"):
        if self.autonomous_agent is not None:
            self.display.append(
                "<span style='color:#ff5555;'>[AUTONOMOUS]: A goal is already running -- use "
                "'stop autonomous' first.</span>"
            )
            return

        self.autonomous_agent = AutonomousAgent(execute_shell, write_file)
        self.autonomous_worker = AutonomousGoalWorker(self.autonomous_agent, goal, source)
        self.autonomous_worker.event.connect(
            lambda msg: self.display.append(f"<span style='color:#00ffff;'>{html.escape(msg)}</span>")
        )
        self.autonomous_worker.finished_goal.connect(self.on_autonomous_finished)
        self.autonomous_worker.start()

    def on_autonomous_finished(self):
        self.autonomous_agent = None
        self.autonomous_worker = None

    # ------------------------------------------------------------------
    # Simulated everyday-services flows (rides / groceries / food).
    # Every branch below is demo data via services_sim.py -- no real
    # provider is contacted and no real order/ride/purchase happens.
    # ------------------------------------------------------------------

    def _match_known_store(self, text_fragment, known_stores):
        """Fuzzy-ish match: exact (case-insensitive) first, then substring containment either way."""
        frag = text_fragment.strip().lower().rstrip(".,!?")
        for store in known_stores:
            if store.lower() == frag:
                return store
        for store in known_stores:
            if frag in store.lower() or store.lower() in frag:
                return store
        return None

    def _start_store_session(self, domain_type, store):
        """
        Returns True if a session was loaded (or a link prompt was shown --
        either way the caller should stop and let this method own the reply).
        """
        if not accounts_sim.is_linked(store):
            self.pending_link = {"domain": domain_type, "store": store}
            self.display.append(
                f"\n<span style='color:#ffaa00;'>You don't have a {html.escape(store)} account linked yet. "
                f"Type <b>link {html.escape(store)}</b> to create one (simulated) and I'll pull up their catalog.</span>"
            )
            log_action(f"SIM_LINK_PROMPT: store={store} domain={domain_type}")
            return False
        self._load_store_session(domain_type, store)
        return True

    def _load_store_session(self, domain_type, store):
        if domain_type == "groceries":
            catalog = services_sim.get_catalog(store)
        else:
            catalog = services_sim.get_clothing_catalog(store)
        balance = services_sim.get_balance()
        self.active_domain_session = {
            "type": domain_type, "store": store, "catalog": catalog,
            "context": services_sim.build_domain_context(domain_type, store=store, catalog=catalog, balance=balance),
        }
        self.display.append(f"<span style='color:#ffaa00;'>{services_sim.SIMULATION_BANNER} (catalog for {html.escape(store)} loaded, ask me anything)</span>")
        log_action(f"SIM_SESSION_START: {domain_type} store={store}")

    def finalize_domain_session(self, latest_text):
        session = self.active_domain_session
        if not session:
            return
        self.display.append("<span style='color:#00ffff;'>[DEMO]: Finalizing your choice against the mock data...</span>")
        active_history = self.history_data.get(self.current_session_id, [])
        self.finalize_worker = DomainFinalizeWorker(session, active_history, latest_text)
        self.finalize_worker.finished_result.connect(self.on_domain_finalized)
        self.finalize_worker.start()

    def on_domain_finalized(self, message_html, ok):
        color = "#00ffaa" if ok else "#ff5555"
        self.display.append(f"<span style='color:{color};'>{message_html}</span>")
        self.active_domain_session = None

    def on_specials_found(self, store, changes):
        lines = [f"- {name}: R{price:.2f}" for name, price in changes]
        self.display.append(
            f"<span style='color:#00ffaa;'><b>[SPECIALS ALERT - {html.escape(store)}]</b> "
            f"(you're watching this store)<br>" + "<br>".join(html.escape(l) for l in lines) + "</span>"
        )
        log_action(f"SPECIALS_ALERT: store={store} changes={changes}")
        memory_store.log_action_event("specials", f"{store}: {changes}")

    def closeEvent(self, event):
        self.stop_watcher()
        if self.specials_watcher_thread:
            self.specials_watcher_thread.stop()
            self.specials_watcher_thread.wait(2000)
        super().closeEvent(event)

    def payment_cleared(self, tx_id, amount, recipient):
        self.display.append(
            f"<span style='color: #00ffaa;'><b>[PAYSHAP CLEARED]:</b> ✓ Transaction {tx_id} Authorized!<br>"
            f"Successfully transferred R{amount} to {recipient}.</span>"
        )

    def display_response(self, response_text, meta):
        safe_text = html.escape(response_text).replace("\n", "<br>")
        self.display.append(f"<b>[SHADOW CORE - {meta}]:</b><br>{safe_text}")

        for line in response_text.splitlines():
            if line.strip().upper().startswith("REMEMBER:") and "=" in line:
                _, rest = line.split(":", 1)
                key, _, value = rest.partition("=")
                if key.strip() and value.strip():
                    memory_store.remember_fact(key, value)
                    self.display.append(
                        f"<span style='color:#88aaee;'><b>[MEMORY SAVED]:</b> {html.escape(key.strip())} = {html.escape(value.strip())}</span>"
                    )

        file_blocks = re.findall(r'```write_file:([^\n]+)\n(.*?)\n```', response_text, re.DOTALL)
        for rel_path, content in file_blocks:
            res_msg = write_file(rel_path.strip(), content)
            self.display.append(f"<span style='color:#00ffff;'><b>[AUTO-EXEC WRITE]:</b> {html.escape(res_msg)}</span>")

        bash_blocks = re.findall(r'```(?:exec_bash|bash|sh)\n(.*?)\n```', response_text, re.DOTALL)
        for cmd in bash_blocks:
            self.display.append(f"<span style='color:#ffaa00;'><b>[AUTO-EXEC SHELL]:</b> {html.escape(cmd)}</span>")
            result = execute_shell(cmd)
            safe_res = html.escape(result).replace("\n", "<br>")
            self.display.append(f"<span style='color:#00ffaa;'><b>[SYS OUTPUT]:</b><br>{safe_res}</span>")

        self.history_data[self.current_session_id].append({"sender": "[SHADOW CORE]", "text": response_text})
        self.save_history()
        self.refresh_history_list()

        if hasattr(self, 'worker') and self.worker:
            self.worker.deleteLater()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    hud = HoloHUD()
    orb = SpinningHoloOrb(hud)
    orb.show()
    sys.exit(app.exec())
