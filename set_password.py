"""
Manage staff / owner logins.

    python set_password.py                                  # list accounts
    python set_password.py owner@cafe.com "new password"    # change a password
    python set_password.py ali@cafe.com "pass" staff        # add a new staff account
    python set_password.py ali@cafe.com --remove            # remove an account

Passwords are stored hashed (PBKDF2-SHA256), never in plain text.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import server  # noqa: E402

server.init_db()
args = sys.argv[1:]
with server.DB_LOCK, server.db() as c:
    if not args:
        for u in c.execute("SELECT email, role FROM users ORDER BY role DESC, email"):
            print(f"  {u['role']:6}  {u['email']}")
        sys.exit(0)
    email = args[0].strip().lower()
    if len(args) == 2 and args[1] == "--remove":
        owners = c.execute("SELECT COUNT(*) FROM users WHERE role='owner' AND email!=?", (email,)).fetchone()[0]
        row = c.execute("SELECT role FROM users WHERE email=?", (email,)).fetchone()
        if not row:
            sys.exit(f"No account {email}")
        if row["role"] == "owner" and not owners:
            sys.exit("Can't remove the last owner account.")
        c.execute("DELETE FROM users WHERE email=?", (email,))
        print(f"Removed {email}")
        sys.exit(0)
    if len(args) < 2 or len(args[1]) < 6:
        sys.exit('Usage: python set_password.py <email> "<password, at least 6 characters>" [staff|owner]')
    role = args[2] if len(args) > 2 else None
    if role not in (None, "staff", "owner"):
        sys.exit("Role must be staff or owner")
    row = c.execute("SELECT role FROM users WHERE email=?", (email,)).fetchone()
    if row:
        c.execute("UPDATE users SET pw_hash=?" + (", role=?" if role else "") + " WHERE email=?",
                  (server.hash_password(args[1]), *( [role] if role else [] ), email))
        print(f"Password changed for {email}")
    else:
        c.execute("INSERT INTO users(email,role,name,pw_hash,created_at) VALUES(?,?,?,?,?)",
                  (email, role or "staff", email.split("@")[0].title(), server.hash_password(args[1]), time.time()))
        print(f"Created {role or 'staff'} account {email}")
