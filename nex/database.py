"""Versioned SQLite storage. One process owns a database; all writes are serialized."""

import asyncio
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite

from nex.config import UserError

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version(version INTEGER NOT NULL);
INSERT INTO schema_version SELECT 1 WHERE NOT EXISTS(SELECT 1 FROM schema_version);
CREATE TABLE IF NOT EXISTS registrations(
    guild_id TEXT NOT NULL, owner_id TEXT NOT NULL, bot_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('pending','active')),
    created REAL NOT NULL, expires REAL NOT NULL, role_id TEXT NOT NULL,
    granted INTEGER NOT NULL DEFAULT 0, activated REAL,
    PRIMARY KEY(guild_id,owner_id), UNIQUE(guild_id,bot_id)
);
CREATE INDEX IF NOT EXISTS registration_expiry ON registrations(state,expires);
CREATE TABLE IF NOT EXISTS role_leases(
    guild_id TEXT NOT NULL, user_id TEXT NOT NULL, role_id TEXT NOT NULL,
    owned INTEGER NOT NULL, PRIMARY KEY(guild_id,user_id,role_id)
);
CREATE TABLE IF NOT EXISTS trusted(
    guild_id TEXT NOT NULL, user_id TEXT NOT NULL, privilege TEXT NOT NULL,
    PRIMARY KEY(guild_id,user_id,privilege)
);
CREATE TABLE IF NOT EXISTS whitelist(
    scope TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('bot','user','guild')),
    target_id TEXT NOT NULL, PRIMARY KEY(scope,kind,target_id)
);
CREATE TABLE IF NOT EXISTS ignored_channels(
    guild_id TEXT NOT NULL, channel_id TEXT NOT NULL, PRIMARY KEY(guild_id,channel_id)
);
CREATE TABLE IF NOT EXISTS cooldowns(guild_id TEXT PRIMARY KEY, until REAL NOT NULL);
CREATE TABLE IF NOT EXISTS audit(
    id INTEGER PRIMARY KEY, at REAL NOT NULL, actor_id TEXT NOT NULL,
    guild_id TEXT NOT NULL, action TEXT NOT NULL, target_id TEXT NOT NULL,
    result TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_time ON audit(at);
"""


class Database:
    def __init__(self, path: str):
        self.path = path
        self.lock = asyncio.Lock()
        self.connection: aiosqlite.Connection | None = None

    async def open(self):
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = await aiosqlite.connect(self.path, isolation_level=None)
        self.connection.row_factory = aiosqlite.Row
        await self.connection.execute("PRAGMA journal_mode=WAL")
        await self.connection.execute("PRAGMA busy_timeout=5000")
        await self.connection.executescript(SCHEMA)
        versions = await self.rows("SELECT version FROM schema_version")
        if len(versions) != 1 or versions[0]["version"] != 1:
            raise RuntimeError(
                "Unsupported database schema version; restore a compatible database."
            )

    async def close(self):
        if self.connection:
            await self.connection.close()
            self.connection = None

    @asynccontextmanager
    async def transaction(self):
        async with self.lock:
            assert self.connection is not None
            await self.connection.execute("BEGIN IMMEDIATE")
            try:
                yield self.connection
                await self.connection.commit()
            except BaseException:
                await self.connection.rollback()
                raise

    async def rows(self, query: str, args=()) -> list[dict]:
        async with self.lock:
            assert self.connection is not None
            async with self.connection.execute(query, args) as cursor:
                return [dict(row) for row in await cursor.fetchall()]

    async def execute(self, query: str, args=()):
        async with self.transaction() as connection:
            cursor = await connection.execute(query, args)
            return cursor.rowcount

    async def audit(self, actor: int, guild: int, action: str, target: int, result):
        await self.execute(
            "INSERT INTO audit(at,actor_id,guild_id,action,target_id,result) VALUES(?,?,?,?,?,?)",
            (time.time(), str(actor), str(guild), action, str(target), json.dumps(result)),
        )

    async def reserve(self, guild: int, owner: int, bot: int, role: int, duration: int):
        now = time.time()
        try:
            async with self.transaction() as connection:
                await connection.execute(
                    "DELETE FROM registrations WHERE state='pending' AND expires<=?", (now,)
                )
                await connection.execute(
                    "INSERT INTO registrations"
                    "(guild_id,owner_id,bot_id,state,created,expires,role_id)"
                    " VALUES(?,?,?,'pending',?,?,?)",
                    (str(guild), str(owner), str(bot), now, now + duration, str(role)),
                )
        except aiosqlite.IntegrityError as exc:
            raise UserError(
                "You already have a registration/pending invite, or that bot is reserved."
            ) from exc

    async def registrations(self, guild: int, state: str = "active") -> list[dict]:
        return await self.rows(
            "SELECT * FROM registrations WHERE guild_id=? AND state=? ORDER BY created",
            (str(guild), state),
        )

    async def registration(self, guild: int, bot: int) -> dict | None:
        rows = await self.rows(
            "SELECT * FROM registrations WHERE guild_id=? AND bot_id=? AND state='active'",
            (str(guild), str(bot)),
        )
        return rows[0] if rows else None

    async def forget(self, guild: int, bot: int):
        await self.execute(
            "DELETE FROM registrations WHERE guild_id=? AND bot_id=?", (str(guild), str(bot))
        )
