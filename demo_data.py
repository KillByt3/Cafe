"""
Demo data for showing the system off.

    python demo_data.py          # back up, wipe, fill with ~4 months of realistic demo data
    python demo_data.py --clear  # back up, wipe all orders/codes/expenses/calls (keeps the menu)
                                 #   -> use this on the day the cafe starts for real

Every run first copies data/cafe.db to data/backups/, so nothing is ever lost.
The demo is built around the current time, so run it again right before a demo:
today's board, "Done today" and the live orders will look fresh.
Table QR codes keep working (data/secret.key and config.json are not touched).
Refresh any open staff/owner pages after running it.
"""
import random
import shutil
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import server  # noqa: E402  (reuses the same database + settings)

DB = server.DB_PATH
WIPE = ["order_items", "orders", "promo_codes", "expenses", "recurring_expenses", "waiter_calls"]

MENU = [  # category, name, price (EGP), popularity weight  -- BUNO menu (Buno menu.pdf, 4 pages)
    # HOT COFFEE
    ("Hot Coffee", "Americano", 100, 6), ("Hot Coffee", "Cappuccino", 100, 10), ("Hot Coffee", "Cortado", 90, 3),
    ("Hot Coffee", "Espresso", 75, 5), ("Hot Coffee", "Flat White", 100, 5), ("Hot Coffee", "Latte", 100, 9),
    ("Hot Coffee", "Macchiato", 90, 3), ("Hot Coffee", "Mocha", 130, 5), ("Hot Coffee", "Pistachio Latte", 160, 6),
    ("Hot Coffee", "Salted Caramel Latte", 130, 5), ("Hot Coffee", "Spanish Latte", 130, 10), ("Hot Coffee", "White Mocha", 130, 4),
    # SPECIALITY
    ("Speciality", "Hot V60", 160, 2), ("Speciality", "Iced V60", 160, 2),
    # ICED COFFEE
    ("Iced Coffee", "Iced Spanish Latte", 130, 10), ("Iced Coffee", "Iced Americano", 110, 5), ("Iced Coffee", "Iced Latte", 110, 7),
    ("Iced Coffee", "Iced Mocha", 130, 5), ("Iced Coffee", "Iced Pistachio Latte", 160, 5),
    ("Iced Coffee", "Iced Salted Caramel", 130, 5), ("Iced Coffee", "Iced White Mocha", 130, 4),
    # MILKSHAKE
    ("Milkshake", "Blueberry Milkshake", 120, 2), ("Milkshake", "Caramel Milkshake", 130, 3), ("Milkshake", "Kinder Milkshake", 130, 4),
    ("Milkshake", "Mango Milkshake", 120, 3), ("Milkshake", "Nutella Milkshake", 130, 5), ("Milkshake", "Oreo Milkshake", 130, 5),
    ("Milkshake", "Pistachio Milkshake", 160, 4), ("Milkshake", "Strawberry Milkshake", 120, 3),
    # BLENDED COFFEE
    ("Blended Coffee", "Frappe Caramel", 130, 5), ("Blended Coffee", "Mocha Frappe", 130, 4), ("Blended Coffee", "Pistachio Frappe", 160, 4),
    ("Blended Coffee", "Salted Caramel Frappe", 130, 4), ("Blended Coffee", "Spanish Frappe", 130, 4), ("Blended Coffee", "White Mocha Frappe", 130, 3),
    # MATCHA
    ("Matcha", "Hot Matcha Latte", 130, 3), ("Matcha", "Hot Spanish Matcha", 150, 2), ("Matcha", "Iced Matcha", 130, 4),
    ("Matcha", "Iced Spanish Matcha", 150, 4), ("Matcha", "Iced Strawberry Matcha", 140, 4), ("Matcha", "Matcha Frappe", 140, 3),
    # MOJITO
    ("Mojito", "Mojito Blueberry", 130, 4), ("Mojito", "Mojito Classic", 110, 4), ("Mojito", "Mojito Lemon Berry", 140, 3),
    ("Mojito", "Mojito Mango", 130, 4), ("Mojito", "Mojito Passion Fruit", 130, 4), ("Mojito", "Mojito Peach", 130, 3),
    ("Mojito", "Mojito Strawberry", 130, 4),
    # ICE TEA
    ("Ice Tea", "Ice Tea Classic", 110, 3), ("Ice Tea", "Ice Tea Passion Fruit", 120, 3), ("Ice Tea", "Ice Tea Peach", 120, 3),
    ("Ice Tea", "Ice Tea Strawberry", 120, 3),
    # MIX RED BULL
    ("Mix Red Bull", "Mix Red Bull (2 Items)", 140, 3),
    # SOFT DRINKS
    ("Soft Drinks", "Red Bull", 100, 4), ("Soft Drinks", "Water", 20, 8),
    # SIGNATURE
    ("Signature", "Love Story", 140, 3), ("Signature", "Second Chance", 140, 3),
    # SMOOTHIE  (the menu lists just "Mango", "Peach"...; "Smoothie" is added so receipts and the
    #            staff screen can't confuse them with the mango milkshake or mojito)
    ("Smoothie", "Mango Smoothie", 120, 4), ("Smoothie", "Mango Coconut Smoothie", 140, 3), ("Smoothie", "Mango Peach Smoothie", 140, 3),
    ("Smoothie", "Passion Fruit Smoothie", 120, 2), ("Smoothie", "Peach Smoothie", 120, 2), ("Smoothie", "Pina Colada Smoothie", 140, 3),
    # DESSERT
    ("Dessert", "Blueberry Cheesecake", 130, 4), ("Dessert", "Cookies Cake", 130, 3), ("Dessert", "Nutella Cake", 130, 4),
    ("Dessert", "Red Velvet", 130, 3), ("Dessert", "San Sebastian", 140, 5), ("Dessert", "Tiramisu", 130, 4),
    # BAKERY
    ("Bakery", "Bacon Turkey Sandwich", 180, 3), ("Bakery", "Club Sandwich", 150, 3), ("Bakery", "Greek Salad", 120, 2),
    ("Bakery", "Roast Beef Bagel", 230, 2), ("Bakery", "Smoked Chicken Sandwich", 170, 3),
]
NOTES = ["Less sugar please", "No sugar", "Extra shot", "Oat milk if possible", "بدون سكر", "Less ice", "No ice",
         "Extra hot", "Birthday — can you add a candle?", "سكر خفيف", "Almond milk please"]
STAFF_CODES = [  # code, kind, value, max_off EGP, min_total EGP, note
    ("WELCOME10", "percent", 10, 50, 0, "First visit"),
    ("STUDENT15", "percent", 15, 40, 100, "Students with ID"),
    ("FRIDAY20", "percent", 20, 60, 250, "Friday brunch"),
]


def backup():
    if not DB.exists():
        return None
    d = DB.parent / "backups"
    d.mkdir(exist_ok=True)
    dst = d / f"cafe-{datetime.now():%Y%m%d-%H%M%S}.db"
    shutil.copy2(DB, dst)
    return dst


def wipe(c, menu_too):
    tables = WIPE + (["menu_items"] if menu_too else [])
    for t in tables:
        c.execute(f"DELETE FROM {t}")
    # restart numbering so order #1 is the first order
    c.execute(f"DELETE FROM sqlite_sequence WHERE name IN ({','.join('?' * len(tables))})", tables)


def code(rng, prefix):
    return prefix + "-" + "".join(rng.choice(server.CODE_ALPHABET) for _ in range(6))


def main():
    clear_only = "--clear" in sys.argv
    server.init_db()
    b = backup()
    print(f"Backup saved: {b}" if b else "No database yet - nothing to back up.")
    rng = random.Random(2026)
    now = time.time()
    today0 = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    with server.DB_LOCK, server.db() as c:
        wipe(c, menu_too=not clear_only)
        if clear_only:
            print("All orders, promo codes, expenses and waiter calls removed. The menu was kept.")
            return

        # ---- menu
        for i, (cat, name, price, _) in enumerate(MENU):
            c.execute("INSERT INTO menu_items(category,name,price,sort) VALUES(?,?,?,?)", (cat, name, price * 100, i))
        menu = [dict(r) for r in c.execute("SELECT id,name,price FROM menu_items ORDER BY sort")]
        weights = [w for *_, w in MENU]

        # ---- staff promo codes (created when the cafe opened)
        opened = today0 - timedelta(days=120)
        for cd, kind, val, mx, mn, note in STAFF_CODES:
            c.execute("INSERT INTO promo_codes(code,kind,value,max_off,min_total,uses_left,expires_at,source,note,created_at)"
                      " VALUES(?,?,?,?,?,NULL,NULL,'staff',?,?)", (cd, kind, val, mx * 100, mn * 100, note, opened.timestamp()))
        c.execute("INSERT INTO promo_codes(code,kind,value,max_off,min_total,uses_left,expires_at,active,source,note,created_at)"
                  " VALUES('SUMMER25','percent',25,7500,20000,NULL,?,0,'staff','Summer offer (ended)',?)",
                  ((opened + timedelta(days=75)).timestamp(), opened.timestamp()))
        staff_codes = {r["code"]: r for r in c.execute("SELECT * FROM promo_codes")}

        open_rewards = []  # [code, created_ts, expires_ts]
        msgs = server.CFG.get("messages") or ["Thank you!"]
        min_reward = int(float(server.CFG["reward_min_total"]) * 100)

        def make_order(t, table, status="done", paid=True, on_way=None, delivered=None):
            lines = {}
            for _ in range(rng.choices([1, 2, 3, 4], [35, 38, 19, 8])[0]):
                it = rng.choices(menu, weights)[0]
                lines[it["id"]] = lines.get(it["id"], 0) + rng.choices([1, 2, 3], [70, 23, 7])[0]
            subtotal = sum(m["price"] * q for m in menu for i, q in lines.items() if m["id"] == i)
            discount, promo = 0, None
            usable = [r for r in open_rewards if r[1] < t - 3600 and r[2] > t]
            if usable and rng.random() < 0.08:
                r = rng.choice(usable); open_rewards.remove(r)
                promo, discount = r[0], min(subtotal * 15 // 100, 5000)
                c.execute("UPDATE promo_codes SET uses_left=0 WHERE code=?", (promo,))
            elif rng.random() < 0.05:
                sc = staff_codes[rng.choice(["WELCOME10", "WELCOME10", "STUDENT15", "FRIDAY20"])]
                if subtotal >= sc["min_total"]:
                    promo = sc["code"]
                    discount = min(subtotal * sc["value"] // 100, sc["max_off"] or subtotal)
            total = subtotal - discount
            note = rng.choice(NOTES) if rng.random() < 0.09 else ""
            if status == "done":
                ow = t + rng.randint(90, 360); dl = ow + rng.randint(120, 600); pd = dl + rng.randint(30, 900)
                st, on_way, delivered = "delivered", ow, dl
                paid_at = pd if paid else None
                if not paid:
                    st = "cancelled"
            else:
                st, paid_at = status, (t + 200 if paid else None)
            oid = c.execute(
                "INSERT INTO orders(table_no,status,note,subtotal,discount,promo_code,total,message,created_at,"
                "on_way_at,delivered_at,paid_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (table, st, note, subtotal, discount, promo, total, rng.choice(msgs), t, on_way, delivered, paid_at)).lastrowid
            c.executemany("INSERT INTO order_items(order_id,item_id,name,price,qty) VALUES(?,?,?,?,?)",
                          [(oid, i, next(m["name"] for m in menu if m["id"] == i), next(m["price"] for m in menu if m["id"] == i), q)
                           for i, q in lines.items()])
            if paid_at and total >= min_reward:  # same rule as the live system
                rc = code(rng, "THX")
                exp = paid_at + float(server.CFG["reward_days_valid"]) * 86400
                c.execute("INSERT INTO promo_codes(code,kind,value,max_off,uses_left,expires_at,source,from_order,note,created_at)"
                          " VALUES(?,'percent',?,?,1,?,'reward',?,?,?)",
                          (rc, int(server.CFG["reward_percent"]), int(float(server.CFG["reward_max_off"]) * 100), exp, oid,
                           f"Reward for order #{oid}", paid_at))
                c.execute("UPDATE orders SET reward_code=? WHERE id=?", (rc, oid))
                open_rewards.append([rc, paid_at, exp])
            return oid

        # ---- 4 months of history (busier as the cafe grows, Fri/Sat are the busy days)
        hours = list(range(8, 24))
        hour_w = [4, 7, 8, 7, 6, 7, 8, 7, 6, 6, 8, 10, 10, 9, 7, 4]
        for d in range(120, 0, -1):
            day = today0 - timedelta(days=d)
            growth = 1 + (120 - d) / 120 * 0.7
            n = int(26 * growth * (1.35 if day.weekday() in (4, 5) else 1) * rng.uniform(.8, 1.2))
            times = sorted(day.timestamp() + rng.choices(hours, hour_w)[0] * 3600 + rng.randint(0, 3599) for _ in range(n))
            for t in times:
                make_order(t, rng.randint(1, 12), paid=rng.random() > 0.03)

        # ---- today: finished orders so far, then a live board
        start = today0.timestamp() + 8 * 3600  # the cafe opens at 8 AM
        span = now - 35 * 60 - start
        if span > 0:  # run before opening time -> no finished orders yet today (no fake night-time sales)
            n_today = max(3, min(30, int(span / 3600 * 2.6)))
            for t in sorted(start + rng.random() * span for _ in range(n_today)):
                make_order(t, rng.randint(1, 12), paid=rng.random() > 0.04)
        busy = rng.sample(range(1, 13), 8)
        m = 60
        make_order(now - 24 * m, busy[0], "delivered", paid=False, on_way=now - 21 * m, delivered=now - 16 * m)
        make_order(now - 19 * m, busy[1], "delivered", paid=False, on_way=now - 17 * m, delivered=now - 12 * m)
        make_order(now - 13 * m, busy[2], "on_the_way", paid=False, on_way=now - 10 * m)
        make_order(now - 9 * m, busy[3], "on_the_way", paid=True, on_way=now - 6 * m)
        make_order(now - 5 * m, busy[4], "new", paid=False)
        oid = make_order(now - 3 * m, busy[5], "new", paid=False)
        c.execute("UPDATE orders SET note='Oat milk please, and no sugar' WHERE id=?", (oid,))
        make_order(now - 1 * m, busy[6], "new", paid=False)
        c.execute("INSERT INTO waiter_calls(table_no,reason,created_at) VALUES(?,?,?)", (busy[7], "help", now - 50))

        # ---- expenses since opening
        first = opened.strftime("%Y-%m")
        c.executemany("INSERT INTO recurring_expenses(category,amount,note,start_month,created_at) VALUES(?,?,?,?,?)", [
            ("Rent", 2500000, "Shop rent", first, opened.timestamp()),
            ("Salaries", 3600000, "3 baristas + 2 waiters", first, opened.timestamp()),
            ("Internet", 85000, "Fiber 200 Mb", first, opened.timestamp()),
            ("Salaries", 600000, "New waiter", server.add_months(first, 2), server.month_bounds(server.add_months(first, 2))[0])])
        month = first
        this_month = datetime.now().strftime("%Y-%m")
        while month <= this_month:
            ms, me, _ = server.month_bounds(month)
            rev = c.execute("SELECT COALESCE(SUM(total),0) FROM orders WHERE paid_at>=? AND paid_at<?", (ms, me)).fetchone()[0]
            prev_name = datetime.strptime(server.add_months(month, -1), "%Y-%m").strftime("%B")
            rows = [("Supplies & ingredients", int(rev * rng.uniform(.2, .24)) // 10000 * 10000, "Coffee beans, milk, syrups, bakery")]
            if month != first:
                rows += [("Electricity", rng.randint(3200, 5200) * 100, f"{prev_name} bill"),
                         ("Water", rng.randint(450, 700) * 100, f"{prev_name} bill"),
                         ("Gas", rng.randint(300, 500) * 100, f"{prev_name} bill")]
            if month == first:
                rows.append(("Marketing", 600000, "Opening flyers & Instagram ads"))
            if month == server.add_months(first, 2):
                rows.append(("Maintenance", 250000, "Espresso machine service"))
            if month == server.add_months(first, 3):
                rows.append(("Marketing", 300000, "Instagram ads"))
            c.executemany("INSERT INTO expenses(month,category,amount,note,created_at) VALUES(?,?,?,?,?)",
                          [(month, cat, amt, note, min(now - 86400 * 4, ms + rng.randint(1, 6) * 86400))
                           for cat, amt, note in rows])
            month = server.add_months(month, 1)

        n_orders = c.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
        n_codes = c.execute("SELECT COUNT(*) FROM promo_codes").fetchone()[0]
    print(f"Demo data ready: {len(MENU)} menu items, {n_orders} orders since {opened:%d %b %Y}, "
          f"{n_codes} promo codes, expenses for every month, a live board and one waiter call.")
    print("Refresh any open staff / owner pages.")


if __name__ == "__main__":
    main()
