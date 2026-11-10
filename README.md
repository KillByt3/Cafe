# Cafe QR Ordering System

Customers scan a QR code on their table, order from their phone, and the order shows up
on the staff screen. Staff move it step by step: On the way → Delivered → Paid. Pressing
Paid prints the receipt, and the waiter puts it on the table.

```
Customer phone              Server (this PC)               Staff screen + receipt printer
--------------              ----------------               ------------------------------
scan QR on table 9   --->   /t/9?k=<table key>
tap + Pepsi, Order   --->   saves order #12        --->    "1 · Received" card pops up, beeps
"On the way"         <---   status = on_the_way    <---    staff press "On the way"
"Delivered"          <---   status = delivered     <---    staff press "Delivered"
                                                           customer pays the waiter
"Paid ✓" + reward    <---   paid_at set            <---    staff press "Paid" -> receipt prints,
                                                           waiter puts it on the table
```

## Requirements
- One Windows PC (or any computer) with **Python 3.9+**. No other installs are needed.
- The PC and the customers' phones on the **same Wi-Fi**.
- A receipt printer (80 mm thermal) installed as the PC's **default printer**.

## Start
Double-click `start.bat`, or run `python server.py`. The console prints the addresses.

1. On the PC, open **http://localhost:8090/login** and sign in with an email and password.
   Staff go to the live orders screen, and the owner goes to the owner dashboard (the owner
   can open the orders screen too). First-run accounts: `owner@cafe.com` / `owner123` and
   `staff@cafe.com` / `staff123`. **Change both passwords** (see Accounts below).
2. Open **Table QR codes** and check that the address is the PC's Wi-Fi IP, for example
   `http://192.168.1.50:8090` and not `localhost`. Then print the codes and stick one on
   each table.
3. If Windows Firewall asks, allow Python on **private networks**. Phones can't connect
   without it.

## Demo data
The database currently holds **demo data** for **Buno** so you can show the system off: Buno's full
74-item menu (14 sections, from `Buno menu.pdf`),
about 4 months of orders since 12 July, promo and reward codes, monthly expenses, a live
board and one waiter call.

```
python demo_data.py           # refresh the demo (run it right before showing it)
python demo_data.py --clear   # the day you go live: removes all orders, codes,
                              # expenses and calls, and keeps the menu
```

Both commands first save a copy of the database in `data/backups/`. The table QR codes keep
working. Refresh any open staff and owner pages afterwards.

## Accounts
Everyone signs in at **/login** with an email and password.

```
python set_password.py                                 # list accounts
python set_password.py owner@cafe.com "new password"   # change a password
python set_password.py ali@cafe.com "password" staff   # add a staff account
python set_password.py ali@cafe.com --remove           # remove an account
```

The server prints a warning at start-up while any account still uses its default password.

## Settings (`config.json`, created on the first run)
| key | meaning |
|---|---|
| `cafe_name` | shown on the menu and the receipts |
| `receipt_address`, `receipt_phone` | optional lines printed under the café name on every receipt |
| `currency` | e.g. `EGP` |
| `tables` | number of tables (QR codes 1..N) |
| `port` | default 8090 |
| `owner_email`, `owner_password`, `staff_email`, `staff_password` | used only once, to create the first two accounts. After that, manage accounts with `set_password.py` |
| `public_url` | fixed address for the QR codes, e.g. `http://192.168.1.50:8090` (give the PC a fixed IP in the router) |

Restart the server after you edit the file. Edit the menu from the dashboard in the
**Menu** tab. Switch an item off to hide an item that has run out.

## Silent receipt printing (no print dialog)
By default the browser opens a print dialog for every receipt. To print with no dialog,
start Chrome on the counter PC in kiosk-printing mode:

```
"C:\Program Files\Google\Chrome\Application\chrome.exe" --kiosk-printing http://localhost:8090/staff
```

Turn on **Receipt printer here** on the computer that has the printer. It is off by
default on every other screen, such as a waiter's phone. When anyone presses Paid, that
computer prints the receipt. If no screen has it switched on, the screen that pressed
Paid prints instead, so a receipt is never missed and never printed twice. You can reprint any receipt with the 🖨 button.

## Order tracking
The staff board has four columns, and each card has one big button for the next step:

| Column | Button | Customer sees |
|---|---|---|
| 1 · Received | 🚶 **On the way** | Received |
| 2 · On the way | ✓ **Delivered** | On the way |
| 3 · Delivered, not paid | 💵 **Paid · print** | Delivered, "please pay the waiter" |
| Done today | 🖨 reprint | Delivered · Paid ✓ (plus the reward code, if earned) |

**Paid** works at any stage, for customers who pay early. Orders only move forward: once a
card is "On the way" it can't go back to "Received", and pressing a button twice (or on
two screens) does nothing extra. A card leaves the board once it
is both delivered and paid. You can cancel an order until it is paid.

## Safety checks when guests order
- **Double tap or weak Wi-Fi:** each basket carries a one-time key, so sending it twice
  creates only one order.
- **Several guests at one table** can each order from their own phone at the same time.
- **Menu changes while a guest is browsing:** if an item is switched off, it is removed
  from the guest's basket and they are told which item ran out. If a price changes, the
  order is stopped and the guest sees the new total before confirming, so nobody is charged
  a different amount than their phone showed. The menu on guests' phones also refreshes
  every 45 seconds.

## Promo codes and rewards
- **Reward codes:** when staff mark an order **paid** and its total is at least
  `reward_min_total` (default 300), the system creates a one-time code like `THX-7KQ2MP`.
  It is worth `reward_percent`% off the next order (default 15%), capped at
  `reward_max_off` (default 50), and stays valid for `reward_days_valid` days (default
  30). The customer sees it on their phone, the phone remembers it for one-tap use next
  time, and it is printed on any receipt reprinted after payment.
- **Staff codes:** in **Promo codes** on the dashboard you can create your own codes, such
  as `WELCOME10`, as a % or a fixed amount off. Each one can have a cap, a minimum order,
  a number of uses and an expiry. You can turn any code off.
- Customers type a code in the **Promo code** box in their order, and the discount shows
  before they send the order. The server checks the code again when the order arrives.
- If an order is cancelled, its promo code is given back and any reward it earned is
  turned off.

## Call waiter
Guests tap **Call waiter** at the top of the menu. One tap sends the call straight to the
staff screen, with no menu to choose from. On every staff screen a ringing card appears at
the top ("Table 5 · Needs a waiter"), with its own louder sound. A waiter presses
**I'm going**, and the guest's phone shows "A waiter is on the way".
- A table can have only one open call at a time, so extra taps don't create duplicates,
  and the guest can cancel the call.
- Calls are rate-limited and need the table's signed QR key, so nobody can ring tables
  from outside the café.

## Owner dashboard (`/owner`)
Only an owner account opens it. A staff account can't open it, even with a direct link.
- **This month:** earnings, expenses, net profit (shown in red when the month made a
  loss), paid orders, average order, discounts given, and today's earnings so far.
  The ‹ › arrows switch between months.
- **Earnings per day:** a bar for each day of the month. Hover a bar to see the amount
  and the number of orders.
- **Last 12 months:** earnings and expenses side by side, with the profit in the hover
  details. **Show table** lists the same numbers as a table.
- **Expenses:** add rent, electricity, water, gas, salaries, supplies and so on, or your
  own category. Tick **Repeats every month** for fixed costs like rent, and they are
  counted every month from then on. The × on a monthly expense stops it from the month
  you are viewing, and earlier months keep their numbers.
- **Where the money goes** (expenses by category) and **Best sellers**.

Earnings count only paid orders, on the day they were paid. Cancelled orders are not
counted.

## Friendly messages
Every order gets a random message from `messages` in `config.json` ("We're glad you're
here", "See you next time", and others). It appears on the customer's "Order sent" screen,
on their order card and on the receipt. Edit the list however you like.

## Security built in
- Each QR code contains a signed per-table key (an HMAC that uses `data/secret.key`), so
  nobody can send orders to "table 7" by editing the URL. If you delete `secret.key`, all
  the QR codes stop working and you must reprint them.
- Prices always come from the database. A customer can't change the price in the request.
- Rate limits: 1 order per table per 15 s and 10 orders per device per 10 min. Quantities
  are 1–20.
- Staff and owner pages need an email and password (stored hashed with PBKDF2). Sign-in
  locks for 5 minutes after 5 wrong tries, and wrong email or wrong password show the same
  message. The session
  cookie is HttpOnly and SameSite=Strict.
- It runs over plain HTTP on the LAN. If you expose it to the internet, put it behind
  HTTPS (Caddy or nginx, for example).

## Files
- `server.py`: the whole backend (stdlib HTTP server + SQLite + live updates over SSE)
- `static/menu.html`: the customer menu page
- `static/products/`: locally stored representative food and drink photos for the customer menu (sources and license in `PHOTO_CREDITS.md`)
- `static/staff.html`: the staff dashboard, receipt printing and menu editor
- `static/qr.html`: the printable QR code sheet
- `data/cafe.db`: orders and menu (back this file up)
