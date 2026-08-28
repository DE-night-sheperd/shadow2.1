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

# Import local banking simulation engine
from bank_sim import create_payment_request, get_tx_status
# Lock path to USB directory ensuring portability
DIR = os.path.dirname(os.path.realpath(__file__))
SCREENSHOT_PATH = "/tmp/holo_screen.png"

# Dedicated subdirectories
DATA_DIR = os.path.join(DIR, "data")
WORKSPACE_DIR = os.path.join(DIR, "workspace")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(WORKSPACE_DIR, exist_ok=True)

HISTORY_FILE = os.path.join(DATA_DIR, "chat_history.json")

SYSTEM_PROMPT = {
    "role": "system",
    "content": (
        "You are Shadow Core, an elite, hyper-intelligent autonomous system engineer and agent. "
        "You possess expert-level mastery over computer science, software architecture, full-stack development, "
        "reverse engineering, and Linux system administration. You have access to system tools to execute terminal "
        "commands, manage files, build web apps, and debug errors dynamically. Always aim for optimal, production-grade solutions."
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
    """Executes bash commands directly from the agent."""
    try:
        res = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=120)
        output = res.stdout if res.returncode == 0 else f"STDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"
        return output.strip() if output.strip() else "[Command executed successfully with no output]"
    except Exception as e:
        return f"[Execution Error: {e}]"

def write_file(rel_path, content):
    """Writes files relative to the USB workspace directory while stripping duplicate workspace/ prefixes."""
    clean_path = rel_path.lstrip("/").lstrip("\\")
    if clean_path.startswith("workspace/"):
        clean_path = clean_path[10:]

    full_path = os.path.join(WORKSPACE_DIR, clean_path)
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    with open(full_path, "w", encoding="utf-8") as f:
        f.write(content)
    return f"[File saved successfully to: {full_path}]"

class PaymentPollWorker(QThread):
    tx_status_signal = pyqtSignal(str, str, str)  # tx_id, amount, recipient

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
    finished = pyqtSignal(str)

    def __init__(self, history_turns, prompt, is_vision=False):
        super().__init__()
        self.history_turns = history_turns
        self.prompt = prompt
        self.is_vision = is_vision

    def run(self):
        try:
            model_name = "llava" if self.is_vision else "qwen2.5-coder"

            if self.is_vision and not capture_screen():
                self.finished.emit("System Error: Screen capture utility failed.")
                return

            messages = [SYSTEM_PROMPT]
            for item in self.history_turns[-10:]:
                role = "user" if item.get("sender") == "Sir" else "assistant"
                messages.append({"role": role, "content": item.get("text", "")})

            if self.is_vision and os.path.exists(SCREENSHOT_PATH):
                with open(SCREENSHOT_PATH, "rb") as f:
                    img_bytes = f.read()
                messages.append({
                    "role": "user",
                    "content": self.prompt + "\nAnalyze this screen capture frame precisely.",
                    "images": [img_bytes]
                })
            else:
                formatted_prompt = (
                    f"{self.prompt}\n\n"
                    f"[System Execution Context]\n"
                    f"USB Workspace Root: {WORKSPACE_DIR}\n"
                    f"If the task requires running commands or writing files, detail the code clearly."
                )
                messages.append({"role": "user", "content": formatted_prompt})

            try:
                res = ollama.chat(model=model_name, messages=messages)
            except Exception:
                fallback_model = "llava" if self.is_vision else "llama3"
                res = ollama.chat(model=fallback_model, messages=messages)

            response_content = res['message']['content']
            self.finished.emit(response_content)

        except Exception as e:
            self.finished.emit(f"System Error: {e}")

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
        self.init_ui()

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
        title = QLabel("⚡ [SHADOW CORE - AUTONOMOUS SYSTEM HUD]")
        title.setFont(QFont("Monospace", 10, QFont.Weight.Bold))
        title.setStyleSheet("color: #00ffcc; letter-spacing: 1px;")

        btn_new = QPushButton("+ NEW SESSION")
        btn_new.setFont(QFont("Monospace", 9, QFont.Weight.Bold))
        btn_new.setStyleSheet("""
            QPushButton { background-color: #050c18; color: #00ffcc; border: 1px solid #00ffcc; border-radius: 4px; padding: 4px 10px; }
            QPushButton:hover { background-color: #00ffcc; color: #050c18; }
        """)
        btn_new.clicked.connect(self.new_chat)

        header.addWidget(title)
        header.addStretch()
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
        self.input_field.setPlaceholderText("Sir > Build a web app, execute shell command, analyze screen, or ask anything...")
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
        self.display.append("<b>[SHADOW CORE]:</b> Agent Engine Active.<br>Workspace: <i>" + WORKSPACE_DIR + "</i>")

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

        # Direct execution shortcuts inside the HUD
        if text.startswith("$ "):
            cmd_to_run = text[2:]
            self.display.append(f"\n<b>Sir (Exec) ></b> <i>{html.escape(cmd_to_run)}</i>")
            result = execute_shell(cmd_to_run)
            safe_res = html.escape(result).replace("\n", "<br>")
            self.display.append(f"<span style='color:#00ffaa;'><b>[SYS OUTPUT]:</b><br>{safe_res}</span>")
            self.input_field.clear()
            return

        # Payment Transfer Detection (e.g., "send R250 to 0699636266")
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

        self.display.append(f"\n<b>Sir ></b> {html.escape(text)}")
        active_history = self.history_data.get(self.current_session_id, [])
        self.input_field.clear()

        is_vision = any(w in text.lower() for w in ["screen", "look", "see", "whats on my screen"])
        if is_vision:
            self.display.append("<span style='color: #ffaa00;'>[OPTICAL SCREEN SCAN ACTIVATED...]</span>")

        self.worker = AgentEngineWorker(active_history, text, is_vision=is_vision)
        self.history_data[self.current_session_id].append({"sender": "Sir", "text": text})

        self.worker.finished.connect(self.display_response)
        self.worker.start()

    def payment_cleared(self, tx_id, amount, recipient):
        self.display.append(
            f"<span style='color: #00ffaa;'><b>[PAYSHAP CLEARED]:</b> ✓ Transaction {tx_id} Authorized!<br>"
            f"Successfully transferred R{amount} to {recipient}.</span>"
        )

    def display_response(self, response_text):
        safe_text = html.escape(response_text).replace("\n", "<br>")
        self.display.append(f"<b>[SHADOW CORE]:</b> {safe_text}")
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
