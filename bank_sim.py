import sqlite3
import uuid
from datetime import datetime

DB_PATH = "bank_ledger.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS ledger (
            tx_id TEXT PRIMARY KEY,
            recipient TEXT,
            amount REAL,
            auth_channel TEXT,
            status TEXT,
            timestamp TEXT
        )
    ''')
    conn.commit()
    conn.close()

def create_payment_request(recipient, amount, auth_channel="TERMINAL_AI"):
    init_db()
    tx_id = f"TX-{uuid.uuid4().hex[:8].upper()}"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO ledger VALUES (?, ?, ?, ?, ?, ?)",
        (tx_id, recipient, float(amount), auth_channel, "COMPLETED", timestamp)
    )
    conn.commit()
    conn.close()
    return tx_id

def get_tx_status(tx_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT recipient, amount, status, timestamp FROM ledger WHERE tx_id = ?", (tx_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return {
            "tx_id": tx_id,
            "recipient": row[0],
            "amount": row[1],
            "status": row[2],
            "timestamp": row[3]
        }
    return None

if __name__ == "__main__":
    init_db()
    print("[+] bank_sim database initialized successfully.")
