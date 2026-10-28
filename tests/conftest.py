import asyncio
from collections import defaultdict
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest
import pytest_asyncio

from nex.config import Config
from nex.database import Database
from nex.services.authorization import Authorization
from nex.services.invitations import Invitations
from nex.services.moderation import Moderation


class Role:
    def __init__(self, role_id=30, position=1):
        self.id = role_id
        self.position = position
        self.managed = False

    def is_default(self):
        return False

    def __ge__(self, other):
        return self.position >= other.position


class Member:
    def __init__(
        self, user_id, *, bot=False, status=discord.Status.offline, roles=None, position=1
    ):
        self.id = user_id
        self.bot = bot
        self.roles = roles or []
        self.top_role = Role(position=position)
        self.status = status
        self.display_name = f"member-{user_id}"
        self.guild_permissions = SimpleNamespace(
            manage_roles=True, kick_members=True, ban_members=True
        )
        self.kick = AsyncMock()
        self.add_roles = AsyncMock(side_effect=self._add)
        self.remove_roles = AsyncMock(side_effect=self._remove)

    async def _add(self, role, **kwargs):
        self.roles.append(role)

    async def _remove(self, role, **kwargs):
        self.roles.remove(role)


class Guild:
    def __init__(self, guild_id=100):
        self.id = guild_id
        self.owner_id = 999
        self.name = f"Guild-{guild_id}"
        self.chunked = True
        self.unavailable = False
        self.role = Role()
        self.members = {2: Member(2, bot=True, position=10), 10: Member(10), 11: Member(11)}
        self.fetch_member = AsyncMock(side_effect=self._fetch)
        self.ban = AsyncMock()
        self.unban = AsyncMock()
        self.leave = AsyncMock()

    async def _fetch(self, user_id):
        if user_id not in self.members:
            raise discord.NotFound(SimpleNamespace(status=404, reason="Not found"), "missing")
        return self.members[user_id]

    def get_member(self, user_id):
        return self.members.get(user_id)

    def get_role(self, role_id):
        return self.role if role_id == self.role.id else None

    def get_channel_or_thread(self, channel_id):
        return None


@pytest_asyncio.fixture
async def db():
    database = Database(":memory:")
    await database.open()
    yield database
    await database.close()


@pytest.fixture
def config():
    return Config(
        token="synthetic-not-a-real-token",
        owner_id=1,
        client_id=2,
        role_id=30,
        developers=frozenset({3}),
        code_users=frozenset({4}),
    )


@pytest.fixture
def guild():
    return Guild()


@pytest.fixture
def harness(db, config, guild):
    locks = defaultdict(asyncio.Lock)
    bot = SimpleNamespace(
        db=db,
        config=config,
        user=SimpleNamespace(id=2),
        intents=SimpleNamespace(members=True, presences=True),
        is_ready=lambda: True,
        guild_lock=lambda guild_id: locks[guild_id],
        fetch_user=AsyncMock(side_effect=lambda uid: SimpleNamespace(id=uid, bot=True)),
        guilds=[guild],
        get_guild=lambda gid: guild if gid == guild.id else None,
    )
    bot.auth = Authorization(config, db)
    bot.invitations = Invitations(bot)
    bot.moderation = Moderation(bot)
    return bot
