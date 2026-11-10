"""
Cafe QR Ordering System - single-file server (Python 3.9+, standard library only).

Flow:
  customer scans table QR -> /t/<table>?k=<token> -> picks items -> POST /api/orders
  -> staff dashboard gets the order live (Server-Sent Events) -> receipt auto-prints
  -> waiter takes receipt + order to the table -> customer pays -> staff marks "Done".

Run:  python server.py        (then open http://localhost:8090/staff)
"""
import hashlib
import hmac
import html
import json
import os
import queue
import re
import secrets
import socket
import sqlite3
import threading
import time
from datetime import datetime
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

BASE = Path(__file__).resolve().parent
STATIC = BASE / "static"
DATA = BASE / "data"
DATA.mkdir(exist_ok=True)
DB_PATH = DATA / "cafe.db"
CONFIG_PATH = BASE / "config.json"

DEFAULT_CONFIG = {
    "cafe_name": "My Cafe",
    "currency": "EGP",
    "tables": 12,
    "port": 8090,
    # First-run accounts (email + password). Used only to create the accounts the first time;
    # after that change passwords with:  python set_password.py <email> <new password>
    "owner_email": "owner@cafe.com",
    "owner_password": "owner123",
    "staff_email": "staff@cafe.com",
    "staff_password": "staff123",
    "receipt_address": "",  # optional, printed under the cafe name, e.g. "12 Tahrir St, Cairo"
    "receipt_phone": "",    # optional, e.g. "0100 000 0000"
    "public_url": "",  # e.g. "http://192.168.1.50:8090" - empty = auto-detect LAN IP
    # Loyalty reward: a paid order of at least reward_min_total earns a one-time code
    # worth reward_percent % off the next order, capped at reward_max_off (0 = no cap).
    "reward_min_total": 300,
    "reward_percent": 15,
    "reward_max_off": 50,
    "reward_days_valid": 30,
    # One of these is picked at random for every order (customer screen + receipt).
    "messages": [
        "We're glad you're here. Enjoy!",
        "Thank you for choosing us. See you next time!",
        "We're happy to serve you today.",
        "So nice to see you. Enjoy every sip!",
        "Thanks for visiting. Come back soon!",
        "Good food, good mood. Enjoy your time with us!",
    ],
}

# ---------------------------------------------------------------- config / secret


def load_config():
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, indent=2), encoding="utf-8")
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    return cfg


CFG = load_config()

SECRET_PATH = DATA / "secret.key"
if not SECRET_PATH.exists():
    SECRET_PATH.write_text(secrets.token_hex(32), encoding="utf-8")
SECRET = SECRET_PATH.read_text(encoding="utf-8").strip().encode()


def table_token(table_no: int) -> str:
    """Signed token printed inside each table's QR code, so nobody can
    order 'to table 7' by just editing the number in the URL."""
    return hmac.new(SECRET, f"table:{table_no}".encode(), hashlib.sha256).hexdigest()[:16]


def valid_table(table, token) -> bool:
    try:
        t = int(table)
    except (TypeError, ValueError):
        return False
    if not 1 <= t <= int(CFG["tables"]):
        return False
    return hmac.compare_digest(table_token(t), str(token or ""))


def lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def public_url() -> str:
    return (CFG.get("public_url") or f"http://{lan_ip()}:{CFG['port']}").rstrip("/")


# ---------------------------------------------------------------- passwords

def hash_password(pw: str) -> str:
    """PBKDF2-SHA256 with a random salt (standard library only)."""
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 200_000)
    return f"pbkdf2$200000${salt.hex()}${dk.hex()}"


def check_password(pw: str, stored: str) -> bool:
    try:
        _, n, salt, dk = stored.split("$")
        got = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), int(n))
        return hmac.compare_digest(got.hex(), dk)
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------- database

DB_LOCK = threading.Lock()


def db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


SEED_MENU = [
    ("Cold drinks", "Pepsi", 2500), ("Cold drinks", "7Up", 2500),
    ("Cold drinks", "Mineral water", 1500), ("Cold drinks", "Fresh orange juice", 4500),
    ("Hot drinks", "Turkish coffee", 3000), ("Hot drinks", "Espresso", 3500),
    ("Hot drinks", "Cappuccino", 5000), ("Hot drinks", "Tea", 2000),
    ("Food", "Cheese croissant", 4000), ("Food", "Club sandwich", 8500),
    ("Desserts", "Chocolate cake", 6000), ("Desserts", "Cheesecake", 7000),
]


def init_db():
    with DB_LOCK, db() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS menu_items(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              category TEXT NOT NULL, name TEXT NOT NULL,
              price INTEGER NOT NULL,             -- minor units (piastres/cents)
              available INTEGER NOT NULL DEFAULT 1,
              sort INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS orders(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              table_no INTEGER NOT NULL,
              status TEXT NOT NULL DEFAULT 'new', -- new | printed | done | cancelled
              note TEXT NOT NULL DEFAULT '',
              total INTEGER NOT NULL,
              created_at REAL NOT NULL, printed_at REAL, done_at REAL);
            CREATE TABLE IF NOT EXISTS order_items(
              order_id INTEGER NOT NULL REFERENCES orders(id),
              item_id INTEGER, name TEXT NOT NULL,
              price INTEGER NOT NULL, qty INTEGER NOT NULL);
            CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status, created_at);
            CREATE TABLE IF NOT EXISTS promo_codes(
              code TEXT PRIMARY KEY,
              kind TEXT NOT NULL,                 -- percent | fixed
              value INTEGER NOT NULL,             -- percent (e.g. 15) or minor units (fixed)
              max_off INTEGER NOT NULL DEFAULT 0, -- minor units, 0 = no cap
              min_total INTEGER NOT NULL DEFAULT 0,
              uses_left INTEGER,                  -- NULL = unlimited
              expires_at REAL,
              active INTEGER NOT NULL DEFAULT 1,
              source TEXT NOT NULL DEFAULT 'staff', -- staff | reward
              from_order INTEGER,
              note TEXT NOT NULL DEFAULT '',
              created_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS users(             -- staff / owner logins
              email TEXT PRIMARY KEY, role TEXT NOT NULL,  -- owner | staff
              name TEXT NOT NULL DEFAULT '', pw_hash TEXT NOT NULL, created_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS waiter_calls(      -- "Call waiter" from a table
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              table_no INTEGER NOT NULL, reason TEXT NOT NULL,
              created_at REAL NOT NULL, handled_at REAL,  -- handled = a waiter is going
              cancelled INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS expenses(          -- one-off expense in a month
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              month TEXT NOT NULL,                        -- YYYY-MM
              category TEXT NOT NULL, amount INTEGER NOT NULL,
              note TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS recurring_expenses( -- repeats every month (rent, salaries)
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              category TEXT NOT NULL, amount INTEGER NOT NULL, note TEXT NOT NULL DEFAULT '',
              start_month TEXT NOT NULL, end_month TEXT,  -- NULL = still running
              created_at REAL NOT NULL);
            """
        )
        if c.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
            for role in ("owner", "staff"):
                c.execute("INSERT INTO users(email,role,name,pw_hash,created_at) VALUES(?,?,?,?,?)",
                          (CFG[f"{role}_email"].strip().lower(), role, role.title(),
                           hash_password(CFG[f"{role}_password"]), time.time()))
        # migrate databases created by the first version
        cols = {r[1] for r in c.execute("PRAGMA table_info(orders)")}
        for name, ddl in [("subtotal", "INTEGER"), ("discount", "INTEGER NOT NULL DEFAULT 0"),
                          ("promo_code", "TEXT"), ("reward_code", "TEXT"),
                          ("message", "TEXT NOT NULL DEFAULT ''"), ("on_way_at", "REAL"),
                          ("delivered_at", "REAL"), ("paid_at", "REAL"), ("client_key", "TEXT"), ("cancelled_at", "REAL")]:
            if name not in cols:
                c.execute(f"ALTER TABLE orders ADD COLUMN {name} {ddl}")
        c.execute("UPDATE orders SET subtotal=total WHERE subtotal IS NULL")
        # old statuses: printed -> on_the_way, done -> delivered + paid
        c.execute("UPDATE orders SET status='on_the_way', on_way_at=printed_at WHERE status='printed'")
        c.execute("UPDATE orders SET status='delivered', delivered_at=done_at, paid_at=done_at "
                  "WHERE status='done'")
        if c.execute("SELECT COUNT(*) FROM menu_items").fetchone()[0] == 0:
            c.executemany(
                "INSERT INTO menu_items(category,name,price,sort) VALUES(?,?,?,?)",
                [(cat, n, p, i) for i, (cat, n, p) in enumerate(SEED_MENU)],
            )


def order_dict(c, row):
    items = [dict(r) for r in c.execute(
        "SELECT item_id,name,price,qty FROM order_items WHERE order_id=?", (row["id"],))]
    d = dict(row)
    d["items"] = items
    return d


def get_order(order_id):
    with db() as c:
        row = c.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        return order_dict(c, row) if row else None


# ---------------------------------------------------------------- live events (SSE)

SUBSCRIBERS = set()
PRINT_STATIONS = set()  # live staff screens with "Print on Paid" switched on
SUB_LOCK = threading.Lock()


def broadcast(event: str, data):
    msg = f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()
    with SUB_LOCK:
        for q in list(SUBSCRIBERS):
            q.put(msg)


# ---------------------------------------------------------------- staff auth + rate limits

class Sessions:
    """Logins kept in SQLite (only a hash of each token) so restarting the
    server doesn't log everybody out. One table per role (staff / owner)."""

    def __init__(self, table):
        self.table = table

    @staticmethod
    def _h(tok):
        return hashlib.sha256(str(tok).encode()).hexdigest()

    def _ensure(self, c):
        c.execute(f"CREATE TABLE IF NOT EXISTS {self.table}(token_hash TEXT PRIMARY KEY, expires REAL)")

    def __setitem__(self, tok, expires):
        with DB_LOCK, db() as c:
            self._ensure(c)
            c.execute(f"DELETE FROM {self.table} WHERE expires < ?", (time.time(),))
            c.execute(f"INSERT OR REPLACE INTO {self.table} VALUES(?,?)", (self._h(tok), expires))

    def get(self, tok):
        with db() as c:
            self._ensure(c)
            r = c.execute(f"SELECT expires FROM {self.table} WHERE token_hash=?", (self._h(tok),)).fetchone()
        return r[0] if r else None

    def pop(self, tok, default=None):
        if tok:
            with DB_LOCK, db() as c:
                self._ensure(c)
                c.execute(f"DELETE FROM {self.table} WHERE token_hash=?", (self._h(tok),))
        return default


STAFF_SESSIONS = Sessions("staff_sessions")
OWNER_SESSIONS = Sessions("owner_sessions")
LOGIN_FAILS = {}  # ip -> (count, first_ts)
ORDER_HITS = {}  # key -> [timestamps]
RL_LOCK = threading.Lock()


def rate_ok(key, limit, window):
    now = time.time()
    with RL_LOCK:
        hits = [t for t in ORDER_HITS.get(key, []) if now - t < window]
        if len(hits) >= limit:
            ORDER_HITS[key] = hits
            return False
        hits.append(now)
        ORDER_HITS[key] = hits
        return True


def money(v):
    return f"{v / 100:,.2f} {CFG['currency']}"


# ---------------------------------------------------------------- promo codes / rewards

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I confusion


def norm_code(s):
    return re.sub(r"[^A-Z0-9-]", "", str(s or "").upper())[:24]


def promo_label(row):
    if row["kind"] == "percent":
        cap = f" (up to {money(row['max_off'])})" if row["max_off"] else ""
        return f"{row['value']}% off{cap}"
    return f"{money(row['value'])} off"


def evaluate_promo(c, code, subtotal):
    """-> (discount, error, row). Never trusts anything but the DB."""
    row = c.execute("SELECT * FROM promo_codes WHERE code=?", (code,)).fetchone()
    if not row or not row["active"]:
        return 0, "This code is not valid.", None
    if row["expires_at"] and row["expires_at"] < time.time():
        return 0, "This code has expired.", None
    if row["uses_left"] is not None and row["uses_left"] <= 0:
        return 0, "This code has already been used.", None
    if subtotal < row["min_total"]:
        return 0, f"This code needs an order of at least {money(row['min_total'])}.", None
    if row["kind"] == "percent":
        d = subtotal * row["value"] // 100
        if row["max_off"]:
            d = min(d, row["max_off"])
    else:
        d = row["value"]
    return min(d, subtotal), None, row


def issue_reward(c, order_id):
    for _ in range(10):
        code = "THX-" + "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))
        if not c.execute("SELECT 1 FROM promo_codes WHERE code=?", (code,)).fetchone():
            break
    c.execute(
        "INSERT INTO promo_codes(code,kind,value,max_off,uses_left,expires_at,source,from_order,note,created_at)"
        " VALUES(?,?,?,?,1,?,'reward',?,?,?)",
        (code, "percent", int(CFG["reward_percent"]), int(float(CFG["reward_max_off"]) * 100),
         time.time() + float(CFG["reward_days_valid"]) * 86400, order_id,
         f"Reward for order #{order_id}", time.time()))
    return code


def reward_info(c, code):
    row = c.execute("SELECT * FROM promo_codes WHERE code=?", (code,)).fetchone() if code else None
    if not row:
        return None
    return {"code": row["code"], "label": promo_label(row), "expires_at": row["expires_at"],
            "used": row["uses_left"] is not None and row["uses_left"] <= 0, "active": bool(row["active"])}


def parse_items(body):
    raw = body.get("items") or []
    if not isinstance(raw, list) or not raw:
        return None, "Your order is empty."
    wanted = {}
    for it in raw[:50]:
        try:
            iid, qty = int(it["id"]), int(it["qty"])
        except (KeyError, TypeError, ValueError):
            return None, "Bad item."
        if qty < 1 or qty > 20:
            return None, "Quantity must be 1-20."
        wanted[iid] = wanted.get(iid, 0) + qty
    return wanted, None


def price_items(c, wanted):
    """-> (menu rows, subtotal, unavailable item ids). menu is None if anything is unavailable."""
    q = ",".join("?" * len(wanted))
    menu = {r["id"]: r for r in c.execute(
        f"SELECT id,name,price FROM menu_items WHERE available=1 AND id IN ({q})", list(wanted))}
    missing = [i for i in wanted if i not in menu]
    if missing:
        return None, None, missing
    return menu, sum(menu[i]["price"] * n for i, n in wanted.items()), []


def unavailable_error(c, missing):
    names = [r["name"] for r in c.execute(
        f"SELECT name FROM menu_items WHERE id IN ({','.join('?' * len(missing))})", missing)]
    what = ", ".join(names) or "Something in your order"
    return {"error": f"Sorry, {what} just ran out and was removed from your order.",
            "unavailable": missing, "refresh": True}


# ---------------------------------------------------------------- call waiter

# No "bring the bill": every order is paid to the waiter at the table and the receipt is
# printed when staff press Paid, so the guest already has it. ("bill" stays only as a
# label for calls made before this change.)
CALL_REASONS = {"help": "Needs a waiter", "water": "Asks for water", "clean": "Table needs cleaning"}
CALL_LABELS = dict(CALL_REASONS, bill="Wants the bill")


def call_dict(r):
    if not r:
        return None
    d = dict(r)
    d["label"] = CALL_LABELS.get(r["reason"], r["reason"])
    d["status"] = "cancelled" if r["cancelled"] else ("handled" if r["handled_at"] else "waiting")
    return d


def get_call(cid):
    with db() as c:
        return call_dict(c.execute("SELECT * FROM waiter_calls WHERE id=?", (cid,)).fetchone())


def table_call(table):
    """The table's open call, or one a waiter answered in the last 3 minutes (so the
    guest sees "a waiter is on the way")."""
    with db() as c:
        r = c.execute("SELECT * FROM waiter_calls WHERE table_no=? AND cancelled=0 AND created_at>? "
                      "ORDER BY id DESC LIMIT 1", (table, time.time() - 3600)).fetchone()
    if r and r["handled_at"] and time.time() - r["handled_at"] > 180:
        return None
    return call_dict(r)


# ---------------------------------------------------------------- owner finance

def add_months(month, n):
    y, m = map(int, month.split("-"))
    k = y * 12 + (m - 1) + n
    return f"{k // 12:04d}-{k % 12 + 1:02d}"


def month_bounds(month):
    start = datetime.strptime(month, "%Y-%m")
    end = datetime.strptime(add_months(month, 1), "%Y-%m")
    return start.timestamp(), end.timestamp(), (end - start).days


def month_expenses(c, month):
    once = c.execute("SELECT id,category,amount,note FROM expenses WHERE month=? ORDER BY id",
                     (month,)).fetchall()
    monthly = c.execute(
        "SELECT id,category,amount,note,start_month FROM recurring_expenses "
        "WHERE start_month<=? AND (end_month IS NULL OR end_month>=?) ORDER BY id",
        (month, month)).fetchall()
    return ([dict(r, kind="monthly") for r in monthly] + [dict(r, kind="once") for r in once])


def finance_summary(month):
    """Earnings come from paid (not cancelled) orders, grouped by the day they were paid."""
    months = [add_months(month, i - 11) for i in range(12)]
    start, _, _ = month_bounds(months[0])
    m_start, m_end, days = month_bounds(month)
    with db() as c:
        paid = c.execute(
            "SELECT id,total,discount,paid_at,created_at FROM orders WHERE paid_at>=? AND paid_at<? "
            "AND status!='cancelled'", (start, m_end)).fetchall()
        by_month = {m: {"month": m, "revenue": 0, "orders": 0} for m in months}
        daily = [{"day": d + 1, "revenue": 0, "orders": 0} for d in range(days)]
        discounts = 0
        prev_days = month_bounds(months[-2])[2]
        prev_daily = [0] * prev_days  # last month, day by day
        heat = [[0] * 24 for _ in range(7)]  # [weekday Mon=0][hour] -> orders placed (peak hours)
        # for the current month, compare with the SAME point of last month (not the whole month)
        now_dt = datetime.now()
        is_current = month == now_dt.strftime("%Y-%m")
        prev_td = {"revenue": 0, "orders": 0}
        for o in paid:
            dt = datetime.fromtimestamp(o["paid_at"])
            mk = dt.strftime("%Y-%m")
            if mk in by_month:
                by_month[mk]["revenue"] += o["total"]
                by_month[mk]["orders"] += 1
            if mk == months[-2]:
                prev_daily[dt.day - 1] += o["total"]
            if mk == month:
                daily[dt.day - 1]["revenue"] += o["total"]
                daily[dt.day - 1]["orders"] += 1
                discounts += o["discount"] or 0
                placed = datetime.fromtimestamp(o["created_at"])
                heat[placed.weekday()][placed.hour] += 1
            elif is_current and mk == months[-2] and (dt.day, dt.time()) <= (now_dt.day, now_dt.time()):
                prev_td["revenue"] += o["total"]
                prev_td["orders"] += 1
        for m in months:
            by_month[m]["expenses"] = sum(e["amount"] for e in month_expenses(c, m))
            by_month[m]["profit"] = by_month[m]["revenue"] - by_month[m]["expenses"]
        exp = month_expenses(c, month)
        cats = {}
        for e in exp:
            cats[e["category"]] = cats.get(e["category"], 0) + e["amount"]
        top = c.execute(
            "SELECT oi.name, SUM(oi.qty) qty, SUM(oi.qty*oi.price) amount FROM order_items oi "
            "JOIN orders o ON o.id=oi.order_id WHERE o.paid_at>=? AND o.paid_at<? "
            "AND o.status!='cancelled' GROUP BY oi.name ORDER BY amount DESC LIMIT 8",  # by money earned, not count
            (m_start, m_end)).fetchall()
        p_start, p_end, _ = month_bounds(months[-2])
        if is_current:  # compare like with like: last month up to the same day and time
            prev_first = datetime.fromtimestamp(p_start)
            same = prev_first.replace(day=min(now_dt.day, prev_days), hour=now_dt.hour,
                                      minute=now_dt.minute, second=now_dt.second)
            p_end = min(p_end, same.timestamp())
        prev_qty = {r["name"]: r["qty"] for r in c.execute(
            "SELECT oi.name, SUM(oi.qty) qty FROM order_items oi JOIN orders o ON o.id=oi.order_id "
            "WHERE o.paid_at>=? AND o.paid_at<? AND o.status!='cancelled' GROUP BY oi.name", (p_start, p_end))}
        today0 = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        today = c.execute("SELECT COALESCE(SUM(total),0), COUNT(*) FROM orders WHERE paid_at>=? "
                          "AND status!='cancelled'", (today0,)).fetchone()
    cur, prev = by_month[month], by_month[months[-2]]
    return {
        "month": month, "currency": CFG["currency"], "cafe_name": CFG["cafe_name"],
        "kpis": {"revenue": cur["revenue"], "expenses": cur["expenses"], "profit": cur["profit"],
                 "orders": cur["orders"], "avg_order": cur["revenue"] // cur["orders"] if cur["orders"] else 0,
                 "discounts": discounts, "prev_revenue": prev["revenue"], "prev_profit": prev["profit"],
                 "prev_expenses": prev["expenses"], "prev_orders": prev["orders"],
                 "prev_avg_order": prev["revenue"] // prev["orders"] if prev["orders"] else 0,
                 "is_current": is_current, "prev_revenue_to_date": prev_td["revenue"],
                 "prev_orders_to_date": prev_td["orders"],
                 "today_revenue": today[0], "today_orders": today[1]},
        "daily": daily,
        "heatmap": heat,
        "monthly": [by_month[m] for m in months],
        "expenses": exp,
        "expenses_by_category": sorted(({"category": k, "amount": v} for k, v in cats.items()),
                                       key=lambda x: -x["amount"]),
        "top_items": [dict(r, prev_qty=prev_qty.get(r["name"], 0)) for r in top],
        "prev_daily": prev_daily,
    }


def owner_activity(limit=14):
    """Latest money events for the owner's live feed, plus a few alerts worth a look."""
    now = time.time()
    since = now - 3 * 86400
    ev = []
    with db() as c:
        for o in c.execute("SELECT * FROM orders WHERE paid_at>? AND status!='cancelled' ORDER BY paid_at DESC LIMIT 40", (since,)):
            ev.append({"t": o["paid_at"], "kind": "paid", "who": f"Table {o['table_no']}",
                       "what": f"paid {money(o['total'])} for order #{o['id']}"})
            if o["reward_code"]:
                ev.append({"t": o["paid_at"] + 0.1, "kind": "reward", "who": f"Table {o['table_no']}",
                           "what": f"earned reward code {o['reward_code']}"})
            if o["promo_code"]:
                ev.append({"t": o["created_at"], "kind": "promo", "who": o["promo_code"],
                           "what": f"used on order #{o['id']} (-{money(o['discount'])})"})
        for o in c.execute("SELECT * FROM orders WHERE status='cancelled' AND COALESCE(cancelled_at, created_at)>? "
                           "ORDER BY id DESC LIMIT 10", (since,)):
            ev.append({"t": o["cancelled_at"] or o["created_at"], "kind": "cancelled", "who": f"Order #{o['id']}",
                       "what": f"cancelled (table {o['table_no']}, {money(o['total'])})"})
        for e in c.execute("SELECT category, amount, created_at, 'once' k FROM expenses WHERE created_at>? UNION ALL "
                           "SELECT category, amount, created_at, 'monthly' k FROM recurring_expenses WHERE created_at>?",
                           (since, since)):
            ev.append({"t": e["created_at"], "kind": "expense", "who": e["category"],
                       "what": f"added as an expense ({money(e['amount'])}{', every month' if e['k'] == 'monthly' else ''})"})
        alerts = []
        month = datetime.now().strftime("%Y-%m")
        k = finance_summary(month)["kpis"]
        if k["profit"] < 0 and k["revenue"]:
            alerts.append({"level": "warn", "text": f"Loss of {money(-k['profit'])} so far this month",
                           "hint": "Full-month rent and salaries are already counted; earnings catch up as the month goes on."})
        today0 = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        avg = k["avg_order"] or 0
        if avg:
            for o in c.execute("SELECT * FROM orders WHERE paid_at>=? AND status!='cancelled' AND total>=? "
                               "ORDER BY total DESC LIMIT 2", (today0, avg * 3)):
                alerts.append({"level": "good", "text": f"Big order: table {o['table_no']} paid {money(o['total'])}",
                               "hint": f"About {round(o['total'] / avg)}x the average order."})
        cx = c.execute("SELECT COUNT(*), COALESCE(SUM(total),0) FROM orders WHERE status='cancelled' "
                       "AND COALESCE(cancelled_at, created_at)>=?", (today0,)).fetchone()
        if cx[0]:
            alerts.append({"level": "bad", "text": f"{cx[0]} order{'s' if cx[0] > 1 else ''} cancelled today",
                           "hint": f"{money(cx[1])} not collected."})
        if not month_expenses(c, month):
            alerts.append({"level": "info", "text": "No expenses added for this month yet",
                           "hint": "Add rent, bills and supplies so the profit is right."})
    ev.sort(key=lambda e: -e["t"])
    return {"events": ev[:limit], "alerts": alerts}


def owner_search(q):
    """Owner search box: an order number / table number, or a menu item name."""
    q = str(q).strip()[:40]
    if not q:
        return {"orders": [], "items": []}
    m_start, m_end, _ = month_bounds(datetime.now().strftime("%Y-%m"))
    with db() as c:
        orders = []
        digits = q.lstrip("#")
        if digits.isdigit():
            rows = c.execute("SELECT * FROM orders WHERE id=? OR (table_no=? AND created_at>?) "
                             "ORDER BY id DESC LIMIT 8", (int(digits), int(digits), time.time() - 86400)).fetchall()
            orders = [order_dict(c, r) for r in rows]
        items = [dict(r) for r in c.execute(
            "SELECT m.name, m.category, m.price, m.available, COALESCE(SUM(CASE WHEN o.paid_at>=? AND o.paid_at<? "
            "AND o.status!='cancelled' THEN oi.qty END),0) qty FROM menu_items m "
            "LEFT JOIN order_items oi ON oi.item_id=m.id LEFT JOIN orders o ON o.id=oi.order_id "
            "WHERE m.name LIKE ? OR m.category LIKE ? GROUP BY m.id ORDER BY qty DESC LIMIT 8",
            (m_start, m_end, f"%{q}%", f"%{q}%"))]
    return {"orders": orders, "items": items}


# ---------------------------------------------------------------- HTTP handler

# Order journey: new (Received) -> on_the_way -> delivered. Payment is separate (paid_at)
# because customers may pay before or after delivery. An order is finished once it is
# delivered AND paid.
STATUS_FLOW = {"on_the_way", "delivered", "cancelled"}
# Orders only move forward. "delivered" can be reached straight from "new" (waiter took it
# right away). An unpaid order can be cancelled at any stage.
NEXT_STATUS = {"new": {"on_the_way", "delivered", "cancelled"},
               "on_the_way": {"delivered", "cancelled"},
               "delivered": {"cancelled"},
               "cancelled": set()}


class Handler(BaseHTTPRequestHandler):
    server_version = "CafeQR/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # keep the console readable
        pass

    # --- helpers
    def _headers(self, code, ctype, length=None, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        if length is not None:
            self.send_header("Content-Length", str(length))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            for one in (v if isinstance(v, list) else [v]):
                self.send_header(k, one)
        self.end_headers()

    def send_json(self, obj, code=200, extra=None):
        body = json.dumps(obj).encode()
        self._headers(code, "application/json; charset=utf-8", len(body), extra)
        self.wfile.write(body)

    def send_html(self, text, code=200):
        body = text.encode()
        self._headers(code, "text/html; charset=utf-8", len(body))
        self.wfile.write(body)

    def send_static(self, name):
        p = STATIC / name
        self.send_html(p.read_text(encoding="utf-8"))

    def send_asset(self, name):
        """Shared CSS/JS from static/ (plain file names only - no paths)."""
        p = STATIC / name
        if not p.is_file():
            return self.send_json({"error": "not found"}, 404)
        body = p.read_bytes()
        ctype = "text/css; charset=utf-8" if name.endswith(".css") else "text/javascript; charset=utf-8"
        self._headers(200, ctype, len(body))
        self.wfile.write(body)

    def send_product_image(self, name):
        """Serve a bundled menu photo by its restricted file name."""
        p = STATIC / "products" / name
        if not p.is_file():
            return self.send_json({"error": "not found"}, 404)
        body = p.read_bytes()
        self._headers(200, "image/jpeg", len(body))
        self.wfile.write(body)

    def redirect(self, to):
        self._headers(302, "text/plain", 0, {"Location": to})

    def read_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 64 * 1024:
            raise ValueError("body too large")
        if "application/json" not in (self.headers.get("Content-Type") or ""):
            raise ValueError("expected JSON")
        return json.loads(self.rfile.read(n) or b"{}")

    def client_ip(self):
        return self.client_address[0]

    def staff_token(self):
        jar = cookies.SimpleCookie(self.headers.get("Cookie") or "")
        return jar["staff"].value if "staff" in jar else None

    def is_staff(self):
        tok = self.staff_token()
        exp = STAFF_SESSIONS.get(tok) if tok else None
        return bool(exp and exp > time.time())

    def owner_token(self):
        jar = cookies.SimpleCookie(self.headers.get("Cookie") or "")
        return jar["owner"].value if "owner" in jar else None

    def is_owner(self):
        tok = self.owner_token()
        exp = OWNER_SESSIONS.get(tok) if tok else None
        return bool(exp and exp > time.time())

    # --- routing
    def do_GET(self):
        u = urlparse(self.path)
        path, qs = u.path, {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if path == "/":
                return self.redirect("/staff")
            if re.fullmatch(r"/t/\d+", path):
                return self.send_static("menu.html")
            if path == "/api/menu":
                return self.api_menu(qs)
            if path == "/api/order-status":
                return self.api_order_status(qs)
            if path == "/api/call-status":
                if not valid_table(qs.get("table"), qs.get("k")):
                    return self.send_json({"error": "invalid table"}, 403)
                return self.send_json({"call": table_call(int(qs["table"]))})
            if path == "/login":
                return self.send_static("login.html")
            if path == "/api/login-info":
                return self.send_json({"cafe_name": CFG["cafe_name"]})
            if path == "/staff":
                return self.send_static("staff.html") if self.is_staff() else self.redirect("/login?next=/staff")
            if path == "/owner":
                return self.send_static("owner.html") if self.is_owner() else self.redirect("/login?next=/owner")
            m = re.fullmatch(r"/static/([a-z0-9_-]+\.(?:css|js))", path)
            if m:
                return self.send_asset(m.group(1))
            m = re.fullmatch(r"/static/products/([a-z0-9_-]+\.jpg)", path)
            if m:
                return self.send_product_image(m.group(1))
            if path.startswith("/api/owner/"):
                if not self.is_owner():
                    return self.send_json({"error": "login required"}, 401)
                if path == "/api/owner/me":
                    return self.send_json({"ok": True, "cafe_name": CFG["cafe_name"],
                                           "currency": CFG["currency"]})
                if path == "/api/owner/summary":
                    month = qs.get("month") or datetime.now().strftime("%Y-%m")
                    if not re.fullmatch(r"\d{4}-\d{2}", month):
                        return self.send_json({"error": "bad month"}, 400)
                    return self.send_json(finance_summary(month))
                if path == "/api/owner/activity":
                    return self.send_json(owner_activity())
                if path == "/api/owner/search":
                    return self.send_json(owner_search(qs.get("q", "")))
                return self.send_json({"error": "not found"}, 404)

            # ---- everything below needs a staff session
            if path.startswith("/staff/") or path.startswith("/api/staff/"):
                if not self.is_staff():
                    if path.startswith("/api/"):
                        return self.send_json({"error": "login required"}, 401)
                    return self.redirect("/login?next=/staff")
            if path == "/staff/qr":
                return self.send_static("qr.html")
            m = re.fullmatch(r"/staff/receipt/(\d+)", path)
            if m:
                return self.receipt(int(m.group(1)))
            if path == "/api/staff/me":
                return self.send_json({"ok": True, "cafe_name": CFG["cafe_name"],
                                       "currency": CFG["currency"], "tables": CFG["tables"]})
            if path == "/api/staff/orders":
                return self.api_staff_orders()
            if path == "/api/staff/calls":
                with db() as c:
                    rows = c.execute("SELECT * FROM waiter_calls WHERE handled_at IS NULL AND cancelled=0 "
                                     "AND created_at>? ORDER BY id", (time.time() - 3 * 3600,)).fetchall()
                return self.send_json({"calls": [call_dict(r) for r in rows]})
            if path == "/api/staff/menu":
                with db() as c:
                    rows = c.execute("SELECT * FROM menu_items ORDER BY sort, id").fetchall()
                return self.send_json([dict(r) for r in rows])
            if path == "/api/staff/promos":
                with db() as c:
                    rows = c.execute("SELECT * FROM promo_codes ORDER BY created_at DESC LIMIT 300").fetchall()
                    return self.send_json([dict(r, label=promo_label(r)) for r in rows])
            if path == "/api/staff/qr-links":
                base = public_url()
                return self.send_json({"base": base, "cafe_name": CFG["cafe_name"], "tables": [
                    {"table": t, "url": f"{base}/t/{t}?k={table_token(t)}"}
                    for t in range(1, int(CFG["tables"]) + 1)]})
            if path == "/api/staff/events":
                return self.sse(printer=qs.get("printer") == "1")
            self.send_json({"error": "not found"}, 404)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            body = self.read_json()
        except (ValueError, json.JSONDecodeError) as e:
            return self.send_json({"error": str(e)}, 400)
        if path == "/api/orders":
            return self.api_place_order(body)
        if path == "/api/promo/check":
            return self.api_check_promo(body)
        if path in ("/api/call", "/api/call/cancel"):
            return self.api_call(body, cancel=path.endswith("cancel"))
        if path == "/api/login":
            return self.api_login(body)
        if path in ("/api/logout", "/api/staff/logout", "/api/owner/logout"):
            STAFF_SESSIONS.pop(self.staff_token(), None)
            OWNER_SESSIONS.pop(self.owner_token(), None)
            return self.send_json({"ok": True}, extra={"Set-Cookie": [
                "staff=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict",
                "owner=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict"]})
        if path.startswith("/api/owner/"):
            if not self.is_owner():
                return self.send_json({"error": "login required"}, 401)
            return self.owner_post(path, body)
        if not self.is_staff():
            return self.send_json({"error": "login required"}, 401)
        m = re.fullmatch(r"/api/staff/orders/(\d+)/status", path)
        if m:
            return self.api_set_status(int(m.group(1)), body)
        m = re.fullmatch(r"/api/staff/orders/(\d+)/paid", path)
        if m:
            return self.api_mark_paid(int(m.group(1)))
        m = re.fullmatch(r"/api/staff/calls/(\d+)/handled", path)
        if m:
            with DB_LOCK, db() as c:
                n = c.execute("UPDATE waiter_calls SET handled_at=? WHERE id=? AND handled_at IS NULL "
                              "AND cancelled=0", (time.time(), int(m.group(1)))).rowcount
            call = get_call(int(m.group(1)))
            if not call:
                return self.send_json({"error": "no such call"}, 404)
            broadcast("call_update", call)
            return self.send_json({"ok": True, "call": call, "changed": bool(n)})
        if path == "/api/staff/menu":
            return self.api_save_menu_item(body)
        if path == "/api/staff/promos":
            return self.api_create_promo(body)
        m = re.fullmatch(r"/api/staff/promos/([A-Z0-9-]+)/toggle", path)
        if m:
            with DB_LOCK, db() as c:
                c.execute("UPDATE promo_codes SET active=1-active WHERE code=?", (m.group(1),))
            return self.send_json({"ok": True})
        self.send_json({"error": "not found"}, 404)

    def api_create_promo(self, body):
        try:
            kind = "fixed" if body.get("kind") == "fixed" else "percent"
            value = float(body["value"])
            value = int(value) if kind == "percent" else int(round(value * 100))
            max_off = int(round(float(body.get("max_off") or 0) * 100))
            min_total = int(round(float(body.get("min_total") or 0) * 100))
            uses = int(body["uses"]) if str(body.get("uses") or "").strip() else None
            days = float(body["days"]) if str(body.get("days") or "").strip() else None
        except (KeyError, ValueError, TypeError):
            return self.send_json({"error": "Check the numbers."}, 400)
        if value <= 0 or (kind == "percent" and value > 100) or (uses is not None and uses < 1):
            return self.send_json({"error": "Invalid discount or uses."}, 400)
        code = norm_code(body.get("code")) or "CAFE-" + "".join(
            secrets.choice(CODE_ALPHABET) for _ in range(6))
        if len(code) < 4:
            return self.send_json({"error": "Code must be at least 4 characters."}, 400)
        with DB_LOCK, db() as c:
            if c.execute("SELECT 1 FROM promo_codes WHERE code=?", (code,)).fetchone():
                return self.send_json({"error": "That code already exists."}, 409)
            c.execute("INSERT INTO promo_codes(code,kind,value,max_off,min_total,uses_left,expires_at,"
                      "source,note,created_at) VALUES(?,?,?,?,?,?,?,'staff',?,?)",
                      (code, kind, value, max_off, min_total, uses,
                       time.time() + days * 86400 if days else None,
                       str(body.get("note") or "")[:80], time.time()))
        self.send_json({"ok": True, "code": code})

    # --- customer API
    def api_menu(self, qs):
        with db() as c:
            rows = c.execute(
                "SELECT id,category,name,price FROM menu_items WHERE available=1 ORDER BY sort,id"
            ).fetchall()
        self.send_json({
            "cafe_name": CFG["cafe_name"], "currency": CFG["currency"],
            "table_ok": valid_table(qs.get("table"), qs.get("k")),
            "items": [dict(r) for r in rows],
            "reward": {"min_total": int(float(CFG["reward_min_total"]) * 100),
                       "percent": int(CFG["reward_percent"]),
                       "max_off": int(float(CFG["reward_max_off"]) * 100)}
            if int(CFG["reward_percent"]) > 0 else None,
        })

    def api_check_promo(self, body):
        if not rate_ok(f"promo{self.client_ip()}", 30, 600):
            return self.send_json({"error": "Too many tries. Please wait a few minutes."}, 429)
        if not valid_table(body.get("table"), body.get("k")):
            return self.send_json({"error": "invalid table"}, 403)
        code = norm_code(body.get("code"))
        if not code:
            return self.send_json({"error": "Enter a code."}, 400)
        wanted, err = parse_items(body)
        if err:
            return self.send_json({"error": err}, 400)
        with db() as c:
            menu, subtotal, missing = price_items(c, wanted)
            if missing:
                return self.send_json(unavailable_error(c, missing), 409)
            discount, perr, row = evaluate_promo(c, code, subtotal)
        if perr:
            return self.send_json({"error": perr}, 400)
        self.send_json({"ok": True, "code": code, "label": promo_label(row),
                        "rule": {k: row[k] for k in ("kind", "value", "max_off", "min_total")},
                        "subtotal": subtotal, "discount": discount, "total": subtotal - discount})

    def api_place_order(self, body):
        table, token = body.get("table"), body.get("k")
        if not valid_table(table, token):
            return self.send_json({"error": "This QR code is not valid. Please ask the staff."}, 403)
        table = int(table)
        # A double tap / network retry sends the same client_key: hand back the order we
        # already made instead of creating a second one.
        client_key = re.sub(r"[^A-Za-z0-9-]", "", str(body.get("client_key") or ""))[:40] or None
        if client_key:
            with db() as c:
                dup = c.execute("SELECT id FROM orders WHERE client_key=? AND table_no=? AND created_at>?",
                                (client_key, table, time.time() - 3600)).fetchone()
            if dup:
                return self.send_json({"ok": True, "order": get_order(dup["id"]), "duplicate": True})
        # Several guests at one table may order at the same time, so allow a few orders per
        # minute per table; the per-device limit still stops spam.
        if not rate_ok(f"t{table}", 4, 60) or not rate_ok(f"ip{self.client_ip()}", 10, 600):
            return self.send_json({"error": "Please wait a few seconds before ordering again."}, 429)

        wanted, err = parse_items(body)
        if err:
            return self.send_json({"error": err}, 400)
        note = str(body.get("note") or "")[:200]
        code = norm_code(body.get("promo_code"))

        with DB_LOCK, db() as c:
            # prices always come from the DB, never from the client
            menu, subtotal, missing = price_items(c, wanted)
            if missing:
                return self.send_json(unavailable_error(c, missing), 409)
            # The guest saw a total on their phone. If staff changed a price since the menu
            # loaded, don't silently charge a different amount - make them confirm again.
            try:
                expected = int(body["expected_subtotal"]) if "expected_subtotal" in body else None
            except (TypeError, ValueError):
                expected = None
            if expected is not None and expected != subtotal:
                return self.send_json({"error": "Prices on the menu were just updated. Please check your order total again.",
                                       "refresh": True}, 409)
            discount, promo = 0, None
            if code:
                discount, perr, promo = evaluate_promo(c, code, subtotal)
                if perr:
                    return self.send_json({"error": perr, "promo_error": True}, 400)
                if promo["uses_left"] is not None:
                    c.execute("UPDATE promo_codes SET uses_left=uses_left-1 WHERE code=?", (code,))
            total = subtotal - discount
            message = secrets.choice(CFG["messages"]) if CFG.get("messages") else ""
            cur = c.execute(
                "INSERT INTO orders(table_no,note,subtotal,discount,promo_code,total,message,created_at,client_key)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (table, note, subtotal, discount, code if promo else None, total, message, time.time(), client_key))
            oid = cur.lastrowid
            c.executemany(
                "INSERT INTO order_items(order_id,item_id,name,price,qty) VALUES(?,?,?,?,?)",
                [(oid, i, menu[i]["name"], menu[i]["price"], n) for i, n in wanted.items()])
        order = get_order(oid)
        broadcast("order_new", order)
        print(f"[{datetime.now():%H:%M:%S}] NEW ORDER #{oid} table {table}: "
              + ", ".join(f"{x['qty']}x {x['name']}" for x in order["items"]))
        self.send_json({"ok": True, "order": order})

    def api_call(self, body, cancel=False):
        if not valid_table(body.get("table"), body.get("k")):
            return self.send_json({"error": "This QR code is not valid. Please ask the staff."}, 403)
        table = int(body["table"])
        if cancel:
            with DB_LOCK, db() as c:
                rows = c.execute("SELECT id FROM waiter_calls WHERE table_no=? AND handled_at IS NULL "
                                 "AND cancelled=0", (table,)).fetchall()
                c.execute("UPDATE waiter_calls SET cancelled=1 WHERE table_no=? AND handled_at IS NULL "
                          "AND cancelled=0", (table,))
            for r in rows:
                broadcast("call_update", get_call(r["id"]))
            return self.send_json({"ok": True, "call": None})
        reason = body.get("reason") if body.get("reason") in CALL_REASONS else "help"
        open_call = table_call(table)
        if open_call and open_call["status"] == "waiting":
            return self.send_json({"ok": True, "call": open_call, "already": True})  # no duplicates
        if not rate_ok(f"call-t{table}", 1, 20) or not rate_ok(f"call-ip{self.client_ip()}", 8, 600):
            return self.send_json({"error": "Please wait a moment before calling again."}, 429)
        with DB_LOCK, db() as c:
            cid = c.execute("INSERT INTO waiter_calls(table_no,reason,created_at) VALUES(?,?,?)",
                            (table, reason, time.time())).lastrowid
        call = get_call(cid)
        broadcast("call_new", call)
        print(f"[{datetime.now():%H:%M:%S}] CALL table {table}: {CALL_REASONS[reason]}")
        self.send_json({"ok": True, "call": call})

    def api_order_status(self, qs):
        if not valid_table(qs.get("table"), qs.get("k")):
            return self.send_json({"error": "invalid table"}, 403)
        ids = [int(x) for x in (qs.get("ids") or "").split(",") if x.isdigit()][:20]
        if not ids:
            return self.send_json({"orders": []})
        with db() as c:
            rows = c.execute(
                f"SELECT * FROM orders WHERE table_no=? AND id IN ({','.join('?' * len(ids))})",
                [int(qs["table"]), *ids]).fetchall()
            out = []
            for r in rows:
                d = order_dict(c, r)
                d["reward"] = reward_info(c, r["reward_code"])
                out.append(d)
            self.send_json({"orders": out})

    # --- staff API
    def api_login(self, body):
        """Email + password. Staff get the staff screen; the owner gets the owner dashboard
        and can also open the staff screen."""
        key = f"login:{self.client_ip()}"
        cnt, first = LOGIN_FAILS.get(key, (0, time.time()))
        if cnt >= 5 and time.time() - first < 300:
            return self.send_json({"error": "Too many attempts. Try again in 5 minutes."}, 429)
        email = str(body.get("email") or "").strip().lower()[:120]
        pw = str(body.get("password") or "")[:200]
        with db() as c:
            user = c.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        # same message whether the email or the password was wrong
        if not user or not check_password(pw, user["pw_hash"]):
            LOGIN_FAILS[key] = (cnt + 1 if time.time() - first < 300 else 1,
                                first if time.time() - first < 300 else time.time())
            return self.send_json({"error": "Wrong email or password."}, 403)
        LOGIN_FAILS.pop(key, None)
        cookies_out = []
        stok = secrets.token_urlsafe(24)
        STAFF_SESSIONS[stok] = time.time() + 16 * 3600
        cookies_out.append(f"staff={stok}; Path=/; Max-Age={16 * 3600}; HttpOnly; SameSite=Strict")
        home = "/staff"
        if user["role"] == "owner":
            otok = secrets.token_urlsafe(24)
            OWNER_SESSIONS[otok] = time.time() + 12 * 3600
            cookies_out.append(f"owner={otok}; Path=/; Max-Age={12 * 3600}; HttpOnly; SameSite=Strict")
            home = "/owner"
        # only ever send people to our own dashboards (no open redirects)
        nxt = body.get("next")
        if nxt == "/staff" or (nxt == "/owner" and user["role"] == "owner"):
            home = nxt
        self.send_json({"ok": True, "role": user["role"], "redirect": home}, extra={"Set-Cookie": cookies_out})

    # --- owner API (expenses)
    def owner_post(self, path, body):
        if path == "/api/owner/expenses":
            try:
                month = str(body["month"])
                if not re.fullmatch(r"\d{4}-\d{2}", month):
                    raise ValueError
                category = str(body.get("category") or "Other").strip()[:40] or "Other"
                amount = int(round(float(body["amount"]) * 100))
                note = str(body.get("note") or "").strip()[:120]
            except (KeyError, ValueError, TypeError):
                return self.send_json({"error": "Enter a month and an amount."}, 400)
            if amount <= 0:
                return self.send_json({"error": "Amount must be more than 0."}, 400)
            with DB_LOCK, db() as c:
                if body.get("monthly"):
                    c.execute("INSERT INTO recurring_expenses(category,amount,note,start_month,created_at)"
                              " VALUES(?,?,?,?,?)", (category, amount, note, month, time.time()))
                else:
                    c.execute("INSERT INTO expenses(month,category,amount,note,created_at)"
                              " VALUES(?,?,?,?,?)", (month, category, amount, note, time.time()))
            return self.send_json({"ok": True})
        if path == "/api/owner/expenses/delete":
            try:
                eid, kind, month = int(body["id"]), body.get("kind"), str(body.get("month") or "")
            except (KeyError, ValueError, TypeError):
                return self.send_json({"error": "bad request"}, 400)
            with DB_LOCK, db() as c:
                if kind == "monthly":
                    # stop it from this month on; earlier months keep their history
                    row = c.execute("SELECT start_month FROM recurring_expenses WHERE id=?", (eid,)).fetchone()
                    if row and row["start_month"] >= month:
                        c.execute("DELETE FROM recurring_expenses WHERE id=?", (eid,))
                    elif row:
                        c.execute("UPDATE recurring_expenses SET end_month=? WHERE id=?",
                                  (add_months(month, -1), eid))
                else:
                    c.execute("DELETE FROM expenses WHERE id=?", (eid,))
            return self.send_json({"ok": True})
        self.send_json({"error": "not found"}, 404)

    def api_staff_orders(self):
        start_of_day = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        with db() as c:
            rows = c.execute(
                "SELECT * FROM orders WHERE status IN ('new','on_the_way') "
                "OR (status='delivered' AND paid_at IS NULL) OR created_at >= ? "
                "ORDER BY id DESC LIMIT 300", (start_of_day,)).fetchall()
            self.send_json({"orders": [order_dict(c, r) for r in rows]})

    def api_mark_paid(self, oid):
        with DB_LOCK, db() as c:
            row = c.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
            if not row:
                return self.send_json({"error": "no such order"}, 404)
            if row["status"] == "cancelled":
                return self.send_json({"error": "This order was cancelled."}, 409)
            if row["paid_at"]:
                return self.send_json({"error": "Already paid."}, 409)
            c.execute("UPDATE orders SET paid_at=? WHERE id=?", (time.time(), oid))
            # paid order big enough -> one-time reward code for the next visit
            if (not row["reward_code"] and int(CFG["reward_percent"]) > 0
                    and row["total"] >= int(float(CFG["reward_min_total"]) * 100)):
                c.execute("UPDATE orders SET reward_code=? WHERE id=?", (issue_reward(c, oid), oid))
        order = get_order(oid)
        broadcast("order_update", order)
        broadcast("order_paid", order)  # the print station prints the receipt on this
        with SUB_LOCK:
            stations = len(PRINT_STATIONS)
        # stations == 0 -> nobody will print, so the screen that pressed Paid prints it itself
        self.send_json({"ok": True, "order": order, "print_stations": stations})

    def api_set_status(self, oid, body):
        status = body.get("status")
        if status not in STATUS_FLOW:
            return self.send_json({"error": "bad status"}, 400)
        col = {"on_the_way": ", on_way_at=COALESCE(on_way_at, ?)",
               "delivered": ", delivered_at=COALESCE(delivered_at, ?)",
               "cancelled": ", cancelled_at=?"}.get(status, "")
        args = [status] + ([time.time()] if col else []) + [oid]
        with DB_LOCK, db() as c:
            row = c.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
            if not row:
                return self.send_json({"error": "no such order"}, 404)
            if row["status"] == "cancelled":
                return self.send_json({"error": "This order was cancelled."}, 409)
            if status == "cancelled" and row["paid_at"]:
                return self.send_json({"error": "This order is already paid, so it can't be cancelled."}, 409)
            if status == row["status"]:  # pressed twice / on two screens - nothing to do
                return self.send_json({"ok": True, "order": get_order(oid)})
            if status not in NEXT_STATUS[row["status"]]:
                return self.send_json({"error": "This order has already moved past that step."}, 409)
            c.execute(f"UPDATE orders SET status=?{col} WHERE id=?", args)
            if status == "cancelled":
                if row["promo_code"]:  # give the customer their code back
                    c.execute("UPDATE promo_codes SET uses_left=uses_left+1 "
                              "WHERE code=? AND uses_left IS NOT NULL", (row["promo_code"],))
                if row["reward_code"]:
                    c.execute("UPDATE promo_codes SET active=0 WHERE code=?", (row["reward_code"],))
        order = get_order(oid)
        broadcast("order_update", order)
        self.send_json({"ok": True, "order": order})

    def api_save_menu_item(self, body):
        try:
            name = str(body["name"]).strip()[:60]
            category = str(body.get("category") or "Other").strip()[:40]
            price = int(round(float(body["price"]) * 100))
            available = 1 if body.get("available", True) else 0
        except (KeyError, ValueError, TypeError):
            return self.send_json({"error": "name and price are required"}, 400)
        if not name or price < 0:
            return self.send_json({"error": "invalid item"}, 400)
        with DB_LOCK, db() as c:
            if body.get("id"):
                c.execute("UPDATE menu_items SET name=?,category=?,price=?,available=? WHERE id=?",
                          (name, category, price, available, int(body["id"])))
            else:
                c.execute("INSERT INTO menu_items(category,name,price,available,sort) "
                          "VALUES(?,?,?,?,(SELECT COALESCE(MAX(sort),0)+1 FROM menu_items))",
                          (category, name, price, available))
        broadcast("menu_update", {})
        self.send_json({"ok": True})

    def receipt(self, oid):
        o = get_order(oid)
        if not o:
            return self.send_html("<p>No such order</p>", 404)
        e = html.escape
        n = lambda v: f"{v / 100:,.2f}"                      # amounts without the currency on every line
        cur = e(CFG["currency"])
        rows = "".join(
            f"<div class=item><div class=iname><b>{x['qty']}&times;</b> {e(x['name'])}"
            + (f"<small>{n(x['price'])} each</small>" if x["qty"] > 1 else "")
            + f"</div><div class=amt>{n(x['price'] * x['qty'])}</div></div>" for x in o["items"])
        count = sum(x["qty"] for x in o["items"])
        made = datetime.fromtimestamp(o["created_at"])
        note = f"<div class=note><span>Note</span>{e(o['note'])}</div>" if o["note"] else ""
        sums = ""
        if o["discount"]:
            sums = (f"<hr class=rule><div class=line><span>Subtotal</span><span>{n(o['subtotal'])}</span></div>"
                    f"<div class=line><span>Promo <b>{e(o['promo_code'] or '')}</b></span><span>&minus;{n(o['discount'])}</span></div>")
        if o["status"] == "cancelled":
            status = "<span class=tag>CANCELLED</span>"
            stamp = "<div class=stamp>CANCELLED</div>"
        elif o["paid_at"]:
            status = f"<b>Paid</b> {datetime.fromtimestamp(o['paid_at']):%H:%M}"
            stamp = f"<div class=stamp>&#10003; PAID &middot; {datetime.fromtimestamp(o['paid_at']):%H:%M}</div>"
        else:
            status = "<b>Not paid</b>"
            stamp = "<div class=due>Please pay the waiter at your table</div>"
        reward = ""
        if o["reward_code"]:
            with db() as c:
                info = reward_info(c, o["reward_code"])
            if info and info["active"]:
                until = datetime.fromtimestamp(info["expires_at"]).strftime("%d %b %Y") if info["expires_at"] else ""
                reward = (f"<div class=coupon><div class=ct>Your next-visit reward</div><div class=code>{e(info['code'])}</div>"
                          f"<div class=cd>{e(info['label'])}{' &middot; until ' + until if until else ''}</div></div>")
        contact = " &middot; ".join(e(x) for x in (CFG.get("receipt_address"), CFG.get("receipt_phone")) if x)
        self.send_html(RECEIPT_HTML.format(
            cafe=e(CFG["cafe_name"]), contact=f"<div class=contact>{contact}</div>" if contact else "",
            table=o["table_no"], id=o["id"], date=f"{made:%d %b %Y}", time=f"{made:%H:%M}", status=status,
            rows=rows, count=count, note=note, sums=sums, total=n(o["total"]), cur=cur, stamp=stamp,
            message=e(o["message"] or "Thank you for visiting!"), reward=reward))

    def sse(self, printer=False):
        self._headers(200, "text/event-stream", extra={"Connection": "keep-alive"})
        q = queue.Queue()
        with SUB_LOCK:
            SUBSCRIBERS.add(q)
            if printer:
                PRINT_STATIONS.add(q)
        try:
            self.wfile.write(b"retry: 3000\n\n")
            self.wfile.flush()
            while True:
                try:
                    msg = q.get(timeout=15)
                except queue.Empty:
                    msg = b": ping\n\n"
                self.wfile.write(msg)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass
        finally:
            with SUB_LOCK:
                SUBSCRIBERS.discard(q)
                PRINT_STATIONS.discard(q)
            self.close_connection = True


RECEIPT_HTML = """<!doctype html><html><head><meta charset=utf-8><title>Receipt #{id}</title>
<meta name=viewport content="width=device-width, initial-scale=1">
<style>
/* 80 mm thermal receipt: black only, clean sans-serif, clear hierarchy */
@page {{ size: 80mm auto; margin: 0; }}
* {{ box-sizing: border-box; -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
body {{ margin: 0; color: #000; font: 12.5px/1.4 "Segoe UI", "Helvetica Neue", Arial, sans-serif; font-variant-numeric: tabular-nums; }}
.paper {{ width: 72mm; margin: 0 auto; padding: 5mm 0 6mm; }}
@media screen {{
  body {{ background: #eef1ef; padding: 24px 12px; }}
  .paper {{ background: #fff; padding: 7mm 5mm 8mm; width: 80mm; border-radius: 4px;
            box-shadow: 0 1px 2px rgba(0,0,0,.06), 0 10px 30px rgba(0,0,0,.10); }}
}}
.head {{ text-align: center; }}
.logo {{ width: 34px; height: 34px; margin: 0 auto 6px; border: 2px solid #000; border-radius: 10px; display: grid; place-items: center; }}
.logo svg {{ width: 19px; height: 19px; }}
.cafe {{ font-size: 19px; font-weight: 800; letter-spacing: .02em; text-transform: uppercase; }}
.contact {{ font-size: 10.5px; margin-top: 2px; }}
.table {{ display: flex; align-items: stretch; justify-content: center; margin: 4mm auto 3.5mm; width: max-content;
          border: 2px solid #000; border-radius: 10px; overflow: hidden; }}
.table span {{ padding: 3px 9px; font-size: 10.5px; font-weight: 700; letter-spacing: .14em; display: flex; align-items: center; }}
.table b {{ background: #000; color: #fff; font-size: 30px; line-height: 1; padding: 5px 14px; font-weight: 800; }}
.meta {{ display: grid; grid-template-columns: auto 1fr; gap: 1px 10px; font-size: 11.5px; }}
.meta span {{ color: #000; opacity: .7; }}
.meta div {{ text-align: right; }}
.rule {{ border: 0; border-top: 1.5px dashed #000; margin: 3mm 0; }}
.cols {{ display: flex; justify-content: space-between; font-size: 9.5px; font-weight: 700; letter-spacing: .12em; margin-bottom: 1.5mm; }}
.item {{ display: flex; justify-content: space-between; gap: 8px; padding: 1.2mm 0; }}
.iname {{ min-width: 0; }}
.iname b {{ font-weight: 800; margin-right: 2px; }}
.iname small {{ display: block; font-size: 10.5px; opacity: .7; }}
.amt {{ white-space: nowrap; font-weight: 600; }}
.note {{ border: 1.5px solid #000; border-radius: 8px; padding: 2mm 2.5mm; margin: 2mm 0 0; font-size: 11.5px; }}
.note span {{ display: block; font-size: 9px; font-weight: 800; letter-spacing: .14em; text-transform: uppercase; }}
.line {{ display: flex; justify-content: space-between; padding: .6mm 0; font-size: 12px; }}
.total {{ display: flex; justify-content: space-between; align-items: baseline; border-top: 2.5px solid #000; margin-top: 3mm; padding-top: 2.5mm; }}
.total span {{ font-size: 12px; font-weight: 800; letter-spacing: .12em; }}
.total b {{ font-size: 24px; font-weight: 800; letter-spacing: -.01em; }}
.total small {{ font-size: 12px; font-weight: 700; margin-left: 3px; }}
.count {{ text-align: right; font-size: 10.5px; opacity: .7; }}
.stamp {{ margin: 4mm auto 0; width: max-content; background: #000; color: #fff; font-weight: 800; font-size: 14px;
          letter-spacing: .1em; padding: 2mm 5mm; border-radius: 999px; }}
.due {{ margin: 4mm 0 0; text-align: center; font-weight: 700; font-size: 12px; border: 1.5px solid #000; border-radius: 999px; padding: 1.6mm; }}
.tag {{ font-weight: 800; }}
.msg {{ text-align: center; font-size: 12.5px; font-style: italic; margin: 4mm 2mm 0; }}
.coupon {{ border: 2px dashed #000; border-radius: 10px; text-align: center; padding: 3mm 2mm; margin-top: 4mm; }}
.coupon .ct {{ font-size: 9.5px; font-weight: 800; letter-spacing: .14em; text-transform: uppercase; }}
.coupon .code {{ font: 800 21px/1.3 "Consolas", "Courier New", monospace; letter-spacing: .08em; margin: 1mm 0; }}
.coupon .cd {{ font-size: 11px; }}
.foot {{ text-align: center; margin-top: 5mm; }}
.foot b {{ display: block; font-size: 13px; }}
.foot small {{ display: block; font-size: 10.5px; opacity: .75; margin-top: 1px; }}
.cut {{ text-align: center; font-size: 9px; letter-spacing: .3em; margin-top: 4mm; opacity: .5; }}
@media print {{ .cut {{ display: none; }} }}
</style></head><body><div class=paper>
<div class=head>
  <div class=logo><svg viewBox="0 0 24 24" fill="none" stroke="#000" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 9h13v5a5 5 0 0 1-5 5H9a5 5 0 0 1-5-5z"/><path d="M17 10h1.5a2.5 2.5 0 0 1 0 5H17"/><path d="M8 3v3M12 3v3"/></svg></div>
  <div class=cafe>{cafe}</div>{contact}
</div>
<div class=table><span>TABLE</span><b>{table}</b></div>
<div class=meta><span>Order</span><div><b>#{id}</b></div><span>Date</span><div>{date} &middot; {time}</div><span>Status</span><div>{status}</div></div>
<hr class=rule>
<div class=cols><span>ITEM</span><span>AMOUNT</span></div>
{rows}
{note}
{sums}
<div class=total><span>TOTAL</span><b>{total}<small>{cur}</small></b></div>
<div class=count>{count} item(s)</div>
{stamp}
<div class=msg>&ldquo;{message}&rdquo;</div>
{reward}
<div class=foot><b>Thank you!</b><small>Scan the QR code on your table to order again</small></div>
<div class=cut>- - - - - - - - - - - - - - -</div>
</div>
<script>if (new URLSearchParams(location.search).has('print')) window.onload = () => window.print();</script>
</body></html>"""


def main():
    init_db()
    port = int(CFG["port"])
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    srv.daemon_threads = True
    url = public_url()
    print("=" * 60)
    print(f"  {CFG['cafe_name']} - QR ordering system is running")
    print(f"  Staff dashboard : http://localhost:{port}/staff   (PIN in config.json)")
    print(f"  Owner dashboard : http://localhost:{port}/owner   (owner PIN)")
    print(f"  Table QR codes  : http://localhost:{port}/staff/qr")
    print(f"  Customers reach : {url}/t/<table>?k=...  (same Wi-Fi)")
    print(f"  Example table 1 : {url}/t/1?k={table_token(1)}")
    print("=" * 60)
    print(f"  Sign in         : http://localhost:{port}/login")
    with db() as c:
        for u in c.execute("SELECT email, pw_hash FROM users"):
            if any(check_password(d, u["pw_hash"]) for d in ("owner123", "staff123")):
                print(f"  WARNING: {u['email']} still uses its default password - run:")
                print(f"           python set_password.py {u['email']} <new password>")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
