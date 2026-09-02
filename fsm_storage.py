"""SQLite-backed FSM storage for aiogram.

aiogram's default MemoryStorage keeps conversation state in RAM. On
PythonAnywhere the web worker is recycled constantly, so a user halfway
through adding a debt (5 steps) would silently lose their progress and the
bot would stop making sense to them - the single worst thing that can happen
to a public bot's retention. Persisting state to the same SQLite file the
rest of the bot uses makes a restart invisible to the user.
"""
import json
import logging
import time
from typing import Any, Dict, Optional

import aiosqlite
from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StorageKey

logger = logging.getLogger(__name__)

# Abandoned conversations shouldn't linger forever.
STATE_TTL_SECONDS = 24 * 60 * 60


class SQLiteStorage(BaseStorage):
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._ready = False

    async def _ensure_table(self):
        if self._ready:
            return
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS fsm_state (
                    key        TEXT PRIMARY KEY,
                    state      TEXT,
                    data       TEXT,
                    updated_at REAL
                )
            """)
            await db.commit()
        self._ready = True

    @staticmethod
    def _key(key: StorageKey) -> str:
        return f"{key.bot_id}:{key.chat_id}:{key.user_id}:{key.destiny}"

    async def _row(self, key: StorageKey):
        await self._ensure_table()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT state, data, updated_at FROM fsm_state WHERE key = ?",
                (self._key(key),),
            ) as cur:
                row = await cur.fetchone()
        if row and time.time() - (row["updated_at"] or 0) > STATE_TTL_SECONDS:
            return None
        return row

    async def _write(self, key: StorageKey, state: Optional[str], data: Optional[str]):
        await self._ensure_table()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO fsm_state (key, state, data, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET
                       state = COALESCE(excluded.state, fsm_state.state),
                       data  = COALESCE(excluded.data,  fsm_state.data),
                       updated_at = excluded.updated_at""",
                (self._key(key), state, data, time.time()),
            )
            await db.commit()

    async def set_state(self, key: StorageKey, state: Any = None) -> None:
        value = state.state if isinstance(state, State) else state
        if value is None:
            # Clearing state also clears data - that's what aiogram's
            # state.clear() means to callers.
            await self._ensure_table()
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("DELETE FROM fsm_state WHERE key = ?", (self._key(key),))
                await db.commit()
            return
        await self._write(key, value, None)

    async def get_state(self, key: StorageKey) -> Optional[str]:
        row = await self._row(key)
        return row["state"] if row else None

    async def set_data(self, key: StorageKey, data: Dict[str, Any]) -> None:
        await self._write(key, None, json.dumps(data, ensure_ascii=False))

    async def get_data(self, key: StorageKey) -> Dict[str, Any]:
        row = await self._row(key)
        if not row or not row["data"]:
            return {}
        try:
            return json.loads(row["data"])
        except (ValueError, TypeError):
            logger.warning("Corrupt FSM data for %s; starting fresh", self._key(key))
            return {}

    async def close(self) -> None:
        pass


async def purge_expired_states(db_path: str):
    """Housekeeping for abandoned conversations."""
    try:
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "DELETE FROM fsm_state WHERE updated_at < ?",
                (time.time() - STATE_TTL_SECONDS,),
            )
            await db.commit()
    except Exception as e:
        logger.warning("Could not purge FSM states: %s", e)
