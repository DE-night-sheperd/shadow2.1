"""
Simulated multi-domain "everyday services" layer for Shadow Core.

*** THIS ENTIRE MODULE IS A DEMO/SIMULATION. ***
Nothing here calls Uber, Bolt, Checkers Sixty60, Woolworths, Pick n Pay,
Debonairs, or any other real company's systems, and nothing here uses real
account credentials. All prices, ETAs, catalogs, and balances are invented
locally for demonstration purposes -- e.g. for a hackathon walkthrough of
"what a unified assistant interaction could feel like."

This is deliberately built the same way bank_sim.py already simulates
PayShap: a local, fake, clearly-labelled stand-in. If this project is ever
turned into something that talks to real services, that requires: (1) actual
commercial/API agreements with each provider, and (2) each user
authenticating directly with that provider (OAuth or equivalent) rather than
the assistant holding or replaying their credentials. Nothing in this file
should be repurposed to bypass that.
"""
import hashlib
import random
import sqlite3
import time
import os
from datetime import datetime

SIMULATION_BANNER = "⚠️ SIMULATED DEMO -- no real ride, order, or purchase is being placed."

DIR = os.path.dirname(os.path.realpath(__file__))
DATA_DIR = os.path.join(DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)
DIR_DB_PATH = os.path.join(DATA_DIR, "wallet_sim.db")

# ---------------------------------------------------------------------------
# Fake wallet, so grocery/food totals have something to check against.
# Entirely separate from bank_sim.py's ledger -- this is play money for the
# demo, not a real balance from any bank.
# ---------------------------------------------------------------------------

def _wallet_conn():
    conn = sqlite3.connect(DIR_DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS wallet (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            balance REAL NOT NULL
        )
    """)
    conn.execute("INSERT OR IGNORE INTO wallet (id, balance) VALUES (1, 3500.00)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,          -- 'ride' | 'grocery' | 'food'
            vendor TEXT NOT NULL,
            summary TEXT NOT NULL,
            total REAL NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    conn.commit()
    return conn


def get_balance():
    conn = _wallet_conn()
    row = conn.execute("SELECT balance FROM wallet WHERE id = 1").fetchone()
    conn.close()
    return round(row[0], 2)


def _deduct(amount):
    conn = _wallet_conn()
    conn.execute("UPDATE wallet SET balance = balance - ? WHERE id = 1", (amount,))
    conn.commit()
    conn.close()


def _record_order(kind, vendor, summary, total):
    conn = _wallet_conn()
    conn.execute(
        "INSERT INTO orders (kind, vendor, summary, total, created_at) VALUES (?, ?, ?, ?, ?)",
        (kind, vendor, summary, total, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    )
    conn.commit()
    conn.close()


def get_order_history(limit=10):
    conn = _wallet_conn()
    rows = conn.execute(
        "SELECT kind, vendor, summary, total, created_at FROM orders ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return rows


# ---------------------------------------------------------------------------
# Rides (simulated Uber / Bolt style quotes)
# ---------------------------------------------------------------------------

_RIDE_PROVIDERS = [
    ("Uber", "UberX"),
    ("Uber", "UberGo"),
    ("Bolt", "Bolt"),
    ("Bolt", "Bolt XL"),
]


def get_ride_quotes(destination):
    """Deterministic-but-varied fake quotes so a demo replays consistently for the same destination."""
    seed = int(hashlib.sha256(destination.lower().encode()).hexdigest(), 16) % (10 ** 6)
    rng = random.Random(seed)
    quotes = []
    for provider, tier in _RIDE_PROVIDERS:
        base = 45 + rng.randint(-8, 25)
        eta = rng.randint(3, 14)
        quotes.append({
            "provider": provider,
            "tier": tier,
            "price": round(base, 2),
            "eta_min": eta,
        })
    quotes.sort(key=lambda q: q["price"])
    return quotes


def simulate_ride_request(quote, destination):
    summary = f"{quote['provider']} {quote['tier']} to {destination}"
    _record_order("ride", quote["provider"], summary, quote["price"])
    return {
        "confirmation_id": f"RIDE-{random.randint(100000, 999999)}",
        "summary": summary,
        "price": quote["price"],
        "eta_min": quote["eta_min"],
        "note": SIMULATION_BANNER,
    }


# ---------------------------------------------------------------------------
# Groceries (simulated Checkers Sixty60 / Woolworths / Pick n Pay catalogs)
# ---------------------------------------------------------------------------

_GROCERY_CATALOGS = {
    "Checkers Sixty60": [
        {"name": "White Bread 700g", "price": 18.99, "special_price": 15.99},
        {"name": "Full Cream Milk 2L", "price": 34.99, "special_price": None},
        {"name": "Eggs 18s", "price": 54.99, "special_price": 47.99},
        {"name": "Chicken Portions 2kg", "price": 89.99, "special_price": 74.99},
        {"name": "Rice 2kg", "price": 42.99, "special_price": None},
    ],
    "Woolworths": [
        {"name": "White Bread 700g", "price": 21.99, "special_price": None},
        {"name": "Full Cream Milk 2L", "price": 37.99, "special_price": 32.99},
        {"name": "Free-range Eggs 18s", "price": 64.99, "special_price": None},
        {"name": "Chicken Breasts 1kg", "price": 99.99, "special_price": 84.99},
        {"name": "Basmati Rice 2kg", "price": 59.99, "special_price": None},
    ],
    "Pick n Pay": [
        {"name": "White Bread 700g", "price": 17.99, "special_price": None},
        {"name": "Full Cream Milk 2L", "price": 33.99, "special_price": None},
        {"name": "Eggs 18s", "price": 52.99, "special_price": 44.99},
        {"name": "Chicken Portions 2kg", "price": 84.99, "special_price": 79.99},
        {"name": "Rice 2kg", "price": 39.99, "special_price": 34.99},
    ],
}


def get_frequent_stores(default_top_n=3):
    """
    In a real system this would come from actual purchase history with the
    user's consent. For the demo, it just returns the fixed catalog list --
    wire this up to memory_store facts (e.g. a 'frequent_store' fact) if you
    want it to vary per simulated user.
    """
    return list(_GROCERY_CATALOGS.keys())[:default_top_n]


def get_any_catalog(store):
    """Look up a store's catalog whether it's a grocery or clothing store."""
    if store in _GROCERY_CATALOGS:
        return _GROCERY_CATALOGS[store]
    if store in _CLOTHING_CATALOGS:
        return _CLOTHING_CATALOGS[store]
    return []


def add_special(store, item_name, special_price):
    """
    Demo/presentation hook: manually inject or update a special on a store's
    catalog so the specials watcher has something new to notice live. Not
    used by any normal user-facing flow -- this exists so a presenter can
    trigger "a new special just dropped" during a demo.
    """
    catalog = _GROCERY_CATALOGS.get(store) or _CLOTHING_CATALOGS.get(store)
    if not catalog:
        return False
    for item in catalog:
        if item["name"].lower() == item_name.lower():
            item["special_price"] = special_price
            return True
    catalog.append({"name": item_name, "price": special_price, "special_price": special_price})
    return True


def get_catalog(store):
    return _GROCERY_CATALOGS.get(store, [])


def get_specials(store):
    return [item for item in get_catalog(store) if item.get("special_price") is not None]


def price_list(store, item_names):
    """Return (line_items, total) for the requested item names, applying specials."""
    catalog = {item["name"].lower(): item for item in get_catalog(store)}
    line_items = []
    total = 0.0
    for name in item_names:
        match = catalog.get(name.lower())
        if not match:
            line_items.append({"name": name, "price": None, "note": "not found in this store's catalog"})
            continue
        price = match["special_price"] if match["special_price"] is not None else match["price"]
        line_items.append({"name": match["name"], "price": price})
        total += price
    return line_items, round(total, 2)


def simulate_online_order(store, item_names):
    line_items, total = price_list(store, item_names)
    balance = get_balance()
    if total > balance:
        return {
            "ok": False,
            "reason": f"Total R{total:.2f} exceeds simulated wallet balance R{balance:.2f}.",
            "line_items": line_items,
            "total": total,
        }
    _deduct(total)
    summary = ", ".join(li["name"] for li in line_items if li.get("price") is not None)
    _record_order("grocery", store, summary, total)
    return {
        "ok": True,
        "confirmation_id": f"GRC-{random.randint(100000, 999999)}",
        "line_items": line_items,
        "total": total,
        "remaining_balance": get_balance(),
        "note": SIMULATION_BANNER,
    }


# ---------------------------------------------------------------------------
# Food delivery (simulated Debonairs-style menu -- generic enough to relabel)
# ---------------------------------------------------------------------------

_FOOD_MENUS = {
    "Debonairs Pizza": [
        {"name": "Triple Decker Pizza (Medium)", "price": 129.90},
        {"name": "Pepper Steak Pizza (Large)", "price": 159.90},
        {"name": "Garlic Rolls", "price": 39.90},
        {"name": "1.25L Coke", "price": 24.90},
    ],
}


def get_food_menu(vendor):
    return _FOOD_MENUS.get(vendor, [])


def simulate_food_order(vendor, item_names):
    menu = {item["name"].lower(): item for item in get_food_menu(vendor)}
    line_items = []
    total = 0.0
    for name in item_names:
        match = menu.get(name.lower())
        if not match:
            line_items.append({"name": name, "price": None, "note": "not on this vendor's simulated menu"})
            continue
        line_items.append({"name": match["name"], "price": match["price"]})
        total += match["price"]
    total = round(total, 2)

    balance = get_balance()
    if total > balance:
        return {
            "ok": False,
            "reason": f"Total R{total:.2f} exceeds simulated wallet balance R{balance:.2f}.",
            "line_items": line_items,
            "total": total,
        }
    _deduct(total)
    summary = ", ".join(li["name"] for li in line_items if li.get("price") is not None)
    _record_order("food", vendor, summary, total)
    return {
        "ok": True,
        "confirmation_id": f"FOOD-{random.randint(100000, 999999)}",
        "line_items": line_items,
        "total": total,
        "remaining_balance": get_balance(),
        "note": SIMULATION_BANNER,
    }


# ---------------------------------------------------------------------------
# Clothing (simulated Mr Price / Woolworths Clothing / Pep style catalogs)
# ---------------------------------------------------------------------------

_CLOTHING_CATALOGS = {
    "Mr Price": [
        {"name": "Men's Crew Neck T-Shirt", "price": 89.99, "special_price": 69.99},
        {"name": "Women's Denim Jeans", "price": 249.99, "special_price": None},
        {"name": "Kids Hoodie", "price": 149.99, "special_price": 119.99},
        {"name": "Sneakers", "price": 349.99, "special_price": None},
    ],
    "Woolworths Clothing": [
        {"name": "Men's Crew Neck T-Shirt", "price": 149.99, "special_price": None},
        {"name": "Women's Denim Jeans", "price": 399.99, "special_price": 329.99},
        {"name": "Kids Hoodie", "price": 229.99, "special_price": None},
        {"name": "Formal Shirt", "price": 349.99, "special_price": 279.99},
    ],
    "Pep": [
        {"name": "Men's Crew Neck T-Shirt", "price": 59.99, "special_price": None},
        {"name": "Women's Denim Jeans", "price": 179.99, "special_price": 149.99},
        {"name": "Kids Hoodie", "price": 99.99, "special_price": None},
        {"name": "School Shoes", "price": 199.99, "special_price": 169.99},
    ],
}


def get_clothing_stores():
    return list(_CLOTHING_CATALOGS.keys())


def get_clothing_catalog(store):
    return _CLOTHING_CATALOGS.get(store, [])


def get_clothing_specials(store):
    return [item for item in get_clothing_catalog(store) if item.get("special_price") is not None]


def simulate_clothing_order(store, item_names):
    catalog = {item["name"].lower(): item for item in get_clothing_catalog(store)}
    line_items = []
    total = 0.0
    for name in item_names:
        match = catalog.get(name.lower())
        if not match:
            line_items.append({"name": name, "price": None, "note": "not found in this store's catalog"})
            continue
        price = match["special_price"] if match["special_price"] is not None else match["price"]
        line_items.append({"name": match["name"], "price": price})
        total += price
    total = round(total, 2)

    balance = get_balance()
    if total > balance:
        return {
            "ok": False,
            "reason": f"Total R{total:.2f} exceeds simulated wallet balance R{balance:.2f}.",
            "line_items": line_items,
            "total": total,
        }
    _deduct(total)
    summary = ", ".join(li["name"] for li in line_items if li.get("price") is not None)
    _record_order("clothing", store, summary, total)
    return {
        "ok": True,
        "confirmation_id": f"CLO-{random.randint(100000, 999999)}",
        "line_items": line_items,
        "total": total,
        "remaining_balance": get_balance(),
        "note": SIMULATION_BANNER,
    }


# ---------------------------------------------------------------------------
# Conversational context builder -- this is what gets fed to the LLM so it
# can discuss/help the user choose in natural language while being unable
# to pretend the data is real.
# ---------------------------------------------------------------------------

MOCK_DATA_DISCLOSURE = (
    "IMPORTANT -- MOCK DATA NOTICE: All prices, specials, quotes, menus, and the wallet "
    "balance below come from a local simulation (services_sim.py), not from any real bank, "
    "store, or ride-hailing API. Every time you present this data or reference it in your "
    "reply, you must clearly tell the user it is demo/mock data (e.g. 'note: this is simulated "
    "demo data, not a live price from the store'). Never imply a real order, ride, or payment "
    "has been or will be placed with a real company. You may discuss it, compare options, and "
    "help the user decide, but do not fabricate additional real-world details (real store "
    "hours, real stock levels, real promotions) beyond what is given here."
)


def build_domain_context(domain, **data):
    """
    Render fetched mock data + the standing disclosure into a text block
    meant to be injected into the system prompt while a domain session
    (ride/groceries/clothing/food) is active.
    """
    lines = [MOCK_DATA_DISCLOSURE, ""]

    if domain == "ride":
        lines.append(f"Simulated ride quotes to '{data['destination']}' (cheapest first):")
        for i, q in enumerate(data["quotes"]):
            lines.append(f"  {i+1}. {q['provider']} {q['tier']} -- R{q['price']:.2f}, ETA {q['eta_min']} min")
        lines.append(
            "\nHelp the user compare these and pick one. When they've decided, tell them to say "
            "'confirm order' and name which option (e.g. 'confirm option 1')."
        )

    elif domain in ("groceries", "clothing"):
        store = data["store"]
        catalog = data["catalog"]
        specials = [i for i in catalog if i.get("special_price") is not None]
        lines.append(f"Simulated catalog for {store}:")
        for item in catalog:
            if item.get("special_price") is not None:
                lines.append(f"  - {item['name']}: was R{item['price']:.2f}, now R{item['special_price']:.2f} (special)")
            else:
                lines.append(f"  - {item['name']}: R{item['price']:.2f}")
        lines.append(f"\nSimulated wallet balance: R{data['balance']:.2f}")
        lines.append(
            "\nHelp the user build a list from this catalog, mention relevant specials, and ask "
            "whether they want it as an online order or an in-store list. When they've decided on "
            "items and mode, tell them to say 'confirm order' (for online) or 'draft my list' (for "
            "in-store) followed by the items."
        )

    elif domain == "food":
        vendor = data["vendor"]
        menu = data["menu"]
        lines.append(f"Simulated menu for {vendor}:")
        for item in menu:
            lines.append(f"  - {item['name']}: R{item['price']:.2f}")
        lines.append(f"\nSimulated wallet balance: R{data['balance']:.2f}")
        lines.append(
            "\nHelp the user pick items from this menu. When they've decided, tell them to say "
            "'confirm order' and list the items."
        )

    return "\n".join(lines)
