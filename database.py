import aiosqlite
import logging

DB_NAME = "qarzdorlik.db"

async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                phone_number TEXT,
                language TEXT DEFAULT 'uz',
                username TEXT,
                joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        await db.execute("""
            CREATE TABLE IF NOT EXISTS debts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                debt_type TEXT, -- 'lent' (men berdim) or 'borrowed' (men oldim)
                amount REAL,
                currency TEXT,
                person_name TEXT,
                due_date DATE,
                description TEXT,
                status TEXT DEFAULT 'active', -- active, paid
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_reminded_at TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        """)
        
        # Check if last_reminded_at column exists in debts, if not add it (migration)
        try:
            await db.execute("ALTER TABLE debts ADD COLUMN last_reminded_at TIMESTAMP")
        except Exception:
            pass # Column likely exists
            
        # Migration for paid_amount
        try:
             await db.execute("ALTER TABLE debts ADD COLUMN paid_amount REAL DEFAULT 0")
        except Exception:
             pass 

        await db.commit()
    logging.info("Database initialized")

async def add_user(user_id: int, username: str = None):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (id, username) VALUES (?, ?)",
            (user_id, username)
        )
        await db.commit()

async def get_user(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT * FROM users WHERE id = ?", (user_id,)) as cursor:
            return await cursor.fetchone()

async def update_user_lang(user_id: int, lang: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET language = ? WHERE id = ?", (lang, user_id))
        await db.commit()

async def update_user_phone(user_id: int, phone: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET phone_number = ? WHERE id = ?", (phone, user_id))
        await db.commit()

async def add_debt(user_id: int, debt_type: str, amount: float, currency: str, person_name: str, due_date: str, description: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            INSERT INTO debts (user_id, debt_type, amount, paid_amount, currency, person_name, due_date, description)
            VALUES (?, ?, ?, 0, ?, ?, ?, ?)
        """, (user_id, debt_type, amount, currency, person_name, due_date, description))
        await db.commit()

async def get_active_debts(user_id: int, debt_type: str = None):
    async with aiosqlite.connect(DB_NAME) as db:
        query = "SELECT * FROM debts WHERE user_id = ? AND status = 'active'"
        params = [user_id]
        if debt_type:
            query += " AND debt_type = ?"
            params.append(debt_type)
        
        query += " ORDER BY due_date ASC"
        
        db.row_factory = aiosqlite.Row
        async with db.execute(query, params) as cursor:
            return await cursor.fetchall()

async def get_debt(debt_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM debts WHERE id = ?", (debt_id,)) as cursor:
            return await cursor.fetchone()

async def get_debts_due_before_or_today(date_str: str):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        # As discussed, we look for ALL active debts for now to check logic in Python
        query = "SELECT * FROM debts WHERE status = 'active'"
        async with db.execute(query) as cursor:
            return await cursor.fetchall()

async def mark_debt_paid(debt_id: int, user_id: int):
     async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE debts SET status = 'paid', paid_amount = amount WHERE id = ? AND user_id = ?", (debt_id, user_id))
        await db.commit()

async def update_debt_payment(debt_id: int, user_id: int, payment_amount: float):
    async with aiosqlite.connect(DB_NAME) as db:
        # Get current debt to check basics
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT amount, paid_amount FROM debts WHERE id = ? AND user_id = ?", (debt_id, user_id)) as cursor:
            debt = await cursor.fetchone()
            if not debt: return False
            
            new_paid = debt['paid_amount'] + payment_amount
            total = debt['amount']
            
            status = 'active'
            if new_paid >= total:
                new_paid = total
                status = 'paid'
            
            await db.execute("UPDATE debts SET paid_amount = ?, status = ? WHERE id = ?", (new_paid, status, debt_id))
            await db.commit()
            return True

async def get_recent_names(user_id: int, limit: int = 5):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        query = "SELECT DISTINCT person_name FROM debts WHERE user_id = ? ORDER BY created_at DESC LIMIT ?"
        async with db.execute(query, (user_id, limit)) as cursor:
            rows = await cursor.fetchall()
            return [row['person_name'] for row in rows]

async def update_last_reminded(debt_id: int, timestamp: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE debts SET last_reminded_at = ? WHERE id = ?", (timestamp, debt_id))
        await db.commit()

async def get_all_users():
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT id FROM users") as cursor:
            rows = await cursor.fetchall()
            return [row['id'] for row in rows]
