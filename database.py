"""Data access for the debts bot.

Dates are stored ISO (YYYY-MM-DD) and only formatted for display. The old
schema stored them as DD.MM.YYYY strings, which meant `ORDER BY due_date`
sorted by day-of-month first - so a debt due 05.12.2026 listed above one due
10.01.2027. migrate_dates() converts any legacy rows on startup.
"""
import logging
from datetime import datetime
from typing import List, Optional

import aiosqlite

from config import DB_PATH

logger = logging.getLogger(__name__)

DB_NAME = DB_PATH


# ---------- schema ----------

async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id           INTEGER PRIMARY KEY,
                phone_number TEXT,
                language     TEXT DEFAULT 'uz',
                username     TEXT,
                joined_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS debts (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id          INTEGER,
                debt_type        TEXT,
                amount           REAL,
                paid_amount      REAL DEFAULT 0,
                currency         TEXT,
                person_name      TEXT,
                due_date         DATE,
                description      TEXT,
                status           TEXT DEFAULT 'active',
                created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_reminded_at TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        """)

        for stmt in (
            "ALTER TABLE debts ADD COLUMN last_reminded_at TIMESTAMP",
            "ALTER TABLE debts ADD COLUMN paid_amount REAL DEFAULT 0",
            # Sharing: a debt can be linked to the other party, who then sees
            # it mirrored (their 'I owe' is the owner's 'owed to me').
            "ALTER TABLE debts ADD COLUMN counterparty_id INTEGER",
            "ALTER TABLE debts ADD COLUMN share_token TEXT",
            "ALTER TABLE debts ADD COLUMN share_status TEXT",
            # Growth tracking: where each user came from.
            "ALTER TABLE users ADD COLUMN referred_by INTEGER",
            "ALTER TABLE users ADD COLUMN source TEXT",
            "ALTER TABLE users ADD COLUMN last_active TIMESTAMP",
        ):
            try:
                await db.execute(stmt)
            except Exception:
                pass  # column already there

        # Without these, every list and every reminder sweep is a full scan.
        await db.execute("CREATE INDEX IF NOT EXISTS idx_debts_user_status ON debts(user_id, status)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_debts_status_due ON debts(status, due_date)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_debts_counterparty ON debts(counterparty_id, status)")
        await db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_debts_share_token ON debts(share_token)")

        await db.commit()

    await migrate_dates()
    logger.info("Database ready at %s", DB_NAME)


async def migrate_dates():
    """Convert any legacy DD.MM.YYYY due_date values to ISO."""
    converted = 0
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT id, due_date FROM debts WHERE due_date LIKE '%.%'") as cur:
            rows = await cur.fetchall()
        for row in rows:
            try:
                iso = datetime.strptime(row["due_date"], "%d.%m.%Y").date().isoformat()
            except (ValueError, TypeError):
                continue
            await db.execute("UPDATE debts SET due_date = ? WHERE id = ?", (iso, row["id"]))
            converted += 1
        if converted:
            await db.commit()
    if converted:
        logger.info("Migrated %d debt dates to ISO format", converted)


# ---------- users ----------

async def upsert_user(user_id: int, username: Optional[str] = None,
                      language: Optional[str] = None, phone: Optional[str] = None):
    """One round-trip instead of the old insert-then-two-updates dance."""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (id, username) VALUES (?, ?)", (user_id, username)
        )
        sets, params = [], []
        if username is not None:
            sets.append("username = ?")
            params.append(username)
        if language is not None:
            sets.append("language = ?")
            params.append(language)
        if phone is not None:
            sets.append("phone_number = ?")
            params.append(phone)
        if sets:
            params.append(user_id)
            await db.execute("UPDATE users SET " + ", ".join(sets) + " WHERE id = ?", params)
        await db.commit()


async def get_user(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE id = ?", (user_id,)) as cur:
            return await cur.fetchone()


async def get_user_lang(user_id: int) -> str:
    """Named field, not row[2] - positional access breaks silently on schema change."""
    user = await get_user(user_id)
    return user["language"] if user and user["language"] else "uz"


async def get_all_user_ids() -> List[int]:
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT id FROM users") as cur:
            return [r["id"] for r in await cur.fetchall()]


# ---------- debts ----------

async def add_debt(user_id: int, debt_type: str, amount: float, currency: str,
                   person_name: str, due_date: str, description: str = "") -> int:
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute("""
            INSERT INTO debts (user_id, debt_type, amount, paid_amount, currency,
                               person_name, due_date, description)
            VALUES (?, ?, ?, 0, ?, ?, ?, ?)
        """, (user_id, debt_type, amount, currency, person_name, due_date, description))
        await db.commit()
        return cur.lastrowid


# A shared debt is one row seen from both sides: the owner's "they owe me" is
# the counterparty's "I owe". `view_type` is that flipped direction, and
# `is_mine` says whether this user owns the row (only owners may edit it).
_VIEW_SELECT = """
    SELECT d.*,
           CASE WHEN d.user_id = :uid THEN d.debt_type
                WHEN d.debt_type = 'lent' THEN 'borrowed'
                ELSE 'lent' END                        AS view_type,
           CASE WHEN d.user_id = :uid THEN 1 ELSE 0 END AS is_mine,
           u.username                                   AS owner_username
    FROM debts d
    LEFT JOIN users u ON u.id = d.user_id
    WHERE d.status = 'active'
      AND (d.user_id = :uid
           OR (d.counterparty_id = :uid AND d.share_status = 'accepted'))
"""


async def get_active_debts(user_id: int, debt_type: Optional[str] = None):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        query = _VIEW_SELECT
        params = {"uid": user_id}
        if debt_type:
            query += " AND (CASE WHEN d.user_id = :uid THEN d.debt_type WHEN d.debt_type = 'lent' THEN 'borrowed' ELSE 'lent' END) = :dtype"
            params["dtype"] = debt_type
        # NULLs (open-ended debts) sort last, not first as SQLite would default to.
        query += " ORDER BY d.due_date IS NULL, d.due_date ASC, d.id ASC"
        async with db.execute(query, params) as cur:
            return await cur.fetchall()


async def get_closed_debts(user_id: int, limit: int = 20):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM debts WHERE user_id = ? AND status = 'paid' ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ) as cur:
            return await cur.fetchall()


async def get_debt(debt_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM debts WHERE id = ?", (debt_id,)) as cur:
            return await cur.fetchone()


async def get_debts_needing_reminder(today_iso: str, horizon_iso: str = None):
    """Debts worth reminding about today.

    The window reaches a few days into the future so an early warning can be
    sent before the deadline - finding out on the day itself is too late to
    act on. Open-ended debts (due_date NULL) are never reminded about.

    The old version pulled every active debt of every user on each sweep and
    filtered in Python; this pushes the filter into SQL where the index helps.
    """
    horizon_iso = horizon_iso or today_iso
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT * FROM debts
            WHERE status = 'active'
              AND due_date IS NOT NULL
              AND due_date <= ?
              AND (last_reminded_at IS NULL OR DATE(last_reminded_at) < ?)
            ORDER BY due_date ASC
        """, (horizon_iso, today_iso)) as cur:
            return await cur.fetchall()


async def mark_debt_paid(debt_id: int, user_id: int) -> bool:
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute(
            "UPDATE debts SET status = 'paid', paid_amount = amount WHERE id = ? AND user_id = ?",
            (debt_id, user_id),
        )
        await db.commit()
        return cur.rowcount > 0


async def update_debt_payment(debt_id: int, user_id: int, payment_amount: float):
    """Returns (ok, remaining_after) - remaining lets the caller phrase the reply."""
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT amount, paid_amount FROM debts WHERE id = ? AND user_id = ? AND status = 'active'",
            (debt_id, user_id),
        ) as cur:
            debt = await cur.fetchone()
        if not debt:
            return False, 0.0

        total = debt["amount"]
        new_paid = (debt["paid_amount"] or 0) + payment_amount
        status = "active"
        if new_paid >= total:
            new_paid, status = total, "paid"

        await db.execute(
            "UPDATE debts SET paid_amount = ?, status = ? WHERE id = ?", (new_paid, status, debt_id)
        )
        await db.commit()
        return True, round(total - new_paid, 2)


async def delete_debt(debt_id: int, user_id: int) -> bool:
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute("DELETE FROM debts WHERE id = ? AND user_id = ?", (debt_id, user_id))
        await db.commit()
        return cur.rowcount > 0


async def get_recent_names(user_id: int, limit: int = 6) -> List[str]:
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT person_name, MAX(created_at) AS last_used
            FROM debts WHERE user_id = ?
            GROUP BY person_name
            ORDER BY last_used DESC
            LIMIT ?
        """, (user_id, limit)) as cur:
            return [r["person_name"] for r in await cur.fetchall()]


async def update_last_reminded(debt_id: int, timestamp: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE debts SET last_reminded_at = ? WHERE id = ?", (timestamp, debt_id))
        await db.commit()


async def get_totals(user_id: int):
    """Outstanding amounts per direction and currency, for the summary card."""
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT CASE WHEN d.user_id = :uid THEN d.debt_type
                        WHEN d.debt_type = 'lent' THEN 'borrowed'
                        ELSE 'lent' END AS debt_type,
                   d.currency,
                   SUM(d.amount - COALESCE(d.paid_amount, 0)) AS remaining,
                   COUNT(*) AS n
            FROM debts d
            WHERE d.status = 'active'
              AND (d.user_id = :uid
                   OR (d.counterparty_id = :uid AND d.share_status = 'accepted'))
            GROUP BY debt_type, d.currency
        """, {"uid": user_id}) as cur:
            return await cur.fetchall()


async def get_stats():
    """Aggregate numbers for the admin panel and the status endpoint."""
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        out = {}
        for label, sql in (
            ("users", "SELECT COUNT(*) AS n FROM users"),
            ("debts_active", "SELECT COUNT(*) AS n FROM debts WHERE status='active'"),
            ("debts_paid", "SELECT COUNT(*) AS n FROM debts WHERE status='paid'"),
        ):
            async with db.execute(sql) as cur:
                out[label] = (await cur.fetchone())["n"]
        return out


async def backup_to(dest_path: str):
    """Consistent snapshot via SQLite's own backup API (safe under WAL)."""
    async with aiosqlite.connect(DB_NAME) as src:
        async with aiosqlite.connect(dest_path) as dst:
            await src.backup(dst)
    return dest_path


# ---------- sharing ----------

async def create_share_token(debt_id: int, owner_id: int):
    """Mint (or reuse) the token behind a debt's invite link."""
    import secrets
    debt = await get_debt(debt_id)
    if not debt or debt["user_id"] != owner_id:
        return None
    if debt["share_token"]:
        return debt["share_token"]
    token = secrets.token_urlsafe(8)
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE debts SET share_token = ?, share_status = 'pending' WHERE id = ?",
            (token, debt_id),
        )
        await db.commit()
    return token


async def get_debt_by_token(token: str):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM debts WHERE share_token = ?", (token,)) as cur:
            return await cur.fetchone()


async def accept_share(token: str, counterparty_id: int):
    """Link the other party to the debt. Returns (ok, reason)."""
    debt = await get_debt_by_token(token)
    if not debt:
        return False, "not_found"
    if debt["user_id"] == counterparty_id:
        return False, "own_debt"  # can't confirm a debt against yourself
    if debt["counterparty_id"] and debt["counterparty_id"] != counterparty_id:
        return False, "taken"     # someone already claimed this link
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE debts SET counterparty_id = ?, share_status = 'accepted' WHERE id = ?",
            (counterparty_id, debt["id"]),
        )
        await db.commit()
    return True, debt


async def decline_share(token: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE debts SET share_status = 'declined' WHERE share_token = ?", (token,)
        )
        await db.commit()


# ---------- growth ----------

async def record_referral(user_id: int, referrer_id: Optional[int], source: str):
    """Only ever set once - the first touch is the one that brought them in."""
    if referrer_id == user_id:
        referrer_id = None
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            """UPDATE users SET referred_by = COALESCE(referred_by, ?),
                                source      = COALESCE(source, ?)
               WHERE id = ?""",
            (referrer_id, source, user_id),
        )
        await db.commit()


async def touch_user(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE users SET last_active = CURRENT_TIMESTAMP WHERE id = ?", (user_id,)
        )
        await db.commit()


async def count_referrals(user_id: int) -> int:
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM users WHERE referred_by = ?", (user_id,)
        ) as cur:
            return (await cur.fetchone())[0]


async def get_growth_stats():
    """Numbers that answer 'is this thing growing, and where from?'"""
    queries = {
        "users_total": "SELECT COUNT(*) FROM users",
        "users_week": "SELECT COUNT(*) FROM users WHERE joined_at >= DATE('now', '-7 days')",
        "active_week": "SELECT COUNT(*) FROM users WHERE last_active >= DATE('now', '-7 days')",
        "from_referral": "SELECT COUNT(*) FROM users WHERE referred_by IS NOT NULL",
        "debts_week": "SELECT COUNT(*) FROM debts WHERE created_at >= DATE('now', '-7 days')",
        "shares_sent": "SELECT COUNT(*) FROM debts WHERE share_token IS NOT NULL",
        "shares_accepted": "SELECT COUNT(*) FROM debts WHERE share_status = 'accepted'",
    }
    out = {}
    async with aiosqlite.connect(DB_NAME) as db:
        for name, sql in queries.items():
            try:
                async with db.execute(sql) as cur:
                    out[name] = (await cur.fetchone())[0]
            except Exception as e:
                logger.warning("growth stat %s failed: %s", name, e)
                out[name] = None
    return out
