"""Validated, secret-safe environment configuration."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv


class UserError(Exception):
    """An actionable message safe to show to users."""


def snowflake(value: str | int) -> int:
    text = str(value)
    if not text.isascii() or not text.isdecimal() or not 0 < int(text) < 2**64:
        raise UserError("Provide a valid positive Discord ID (up to 64 bits).")
    return int(text)


def ids(value: str) -> frozenset[int]:
    return frozenset(snowflake(part.strip()) for part in value.split(",") if part.strip())


def public_url(value: str) -> str:
    if not value:
        return ""
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Configured links must be HTTPS URLs without credentials.")
    if parsed.query or parsed.fragment:
        raise ValueError("Configured public links must not contain query strings or fragments.")
    return value


@dataclass(frozen=True)
class Config:
    token: str = field(repr=False)
    owner_id: int
    client_id: int
    role_id: int
    role_overrides: tuple[tuple[int, int], ...] = ()
    developers: frozenset[int] = frozenset()
    code_users: frozenset[int] = frozenset()
    protected_users: frozenset[int] = frozenset()
    protected_bots: frozenset[int] = frozenset()
    protected_guilds: frozenset[int] = frozenset()
    global_purge_guilds: frozenset[int] = frozenset()
    support_url: str = ""
    repository_url: str = ""
    invite_permissions: int = 0
    invite_timeout: int = 20
    purge_cooldown: int = 10800
    concurrency: int = 3
    database: str = "data/nex_codex.db"
    emoji_provisioning: bool = False
    emoji_directory: Path = Path("assets/emojis")

    def role_for(self, guild_id: int) -> int:
        return dict(self.role_overrides).get(guild_id, self.role_id)

    @classmethod
    def from_env(cls) -> "Config":
        load_dotenv()
        required = ["DISCORD_TOKEN", "BOT_OWNER_ID", "BOT_CLIENT_ID", "BOT_ADDER_ROLE_ID"]
        missing = [key for key in required if not os.getenv(key, "").strip()]
        if missing:
            raise ValueError("Missing required environment variables: " + ", ".join(missing))
        database = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///data/nex_codex.db")
        if not database.startswith("sqlite+aiosqlite:///"):
            raise ValueError("DATABASE_URL must start with sqlite+aiosqlite:///.")
        database = database.removeprefix("sqlite+aiosqlite:///")
        if not database:
            raise ValueError("DATABASE_URL must identify a database file.")
        permissions = int(os.getenv("INVITE_PERMISSIONS", "0"))
        timeout = int(os.getenv("INVITE_TIMEOUT_SECONDS", "20"))
        cooldown = int(os.getenv("PURGE_COOLDOWN_SECONDS", "10800"))
        concurrency = int(os.getenv("GLOBAL_CONCURRENCY", "3"))
        if permissions < 0 or permissions >= 2**53 or permissions & 8:
            raise ValueError("INVITE_PERMISSIONS must be valid and must not request Administrator.")
        if not 1 <= timeout <= 300 or cooldown < 0 or not 1 <= concurrency <= 10:
            raise ValueError("Timeout must be 1..300, cooldown >= 0, concurrency 1..10.")
        provisioning = os.getenv("APPLICATION_EMOJI_PROVISIONING", "false").lower()
        if provisioning not in {"true", "false"}:
            raise ValueError("APPLICATION_EMOJI_PROVISIONING must be true or false.")
        overrides = []
        for entry in os.getenv("BOT_ADDER_ROLE_IDS", "").split(","):
            if not entry.strip():
                continue
            pair = entry.strip().split(":")
            if len(pair) != 2:
                raise ValueError("BOT_ADDER_ROLE_IDS must use guild_id:role_id pairs.")
            guild_id, role_id = (snowflake(value.strip()) for value in pair)
            if any(existing_guild == guild_id for existing_guild, _ in overrides):
                raise ValueError("BOT_ADDER_ROLE_IDS contains a duplicate guild.")
            overrides.append((guild_id, role_id))
        return cls(
            token=os.environ["DISCORD_TOKEN"].strip(),
            owner_id=snowflake(os.environ["BOT_OWNER_ID"]),
            client_id=snowflake(os.environ["BOT_CLIENT_ID"]),
            role_id=snowflake(os.environ["BOT_ADDER_ROLE_ID"]),
            role_overrides=tuple(overrides),
            developers=ids(os.getenv("DEVELOPER_IDS", "")),
            code_users=ids(os.getenv("CODE_USER_IDS", "")),
            protected_users=ids(os.getenv("PROTECTED_USER_IDS", "")),
            protected_bots=ids(os.getenv("PROTECTED_BOT_IDS", "")),
            protected_guilds=ids(os.getenv("PROTECTED_GUILD_IDS", "")),
            global_purge_guilds=ids(os.getenv("GLOBAL_PURGE_GUILD_IDS", "")),
            support_url=public_url(os.getenv("SUPPORT_SERVER_URL", "")),
            repository_url=public_url(os.getenv("PUBLIC_REPOSITORY_URL", "")),
            invite_permissions=permissions,
            invite_timeout=timeout,
            purge_cooldown=cooldown,
            concurrency=concurrency,
            database=database,
            emoji_provisioning=provisioning == "true",
            emoji_directory=Path(os.getenv("EMOJI_ASSET_DIRECTORY", "assets/emojis")),
        )
