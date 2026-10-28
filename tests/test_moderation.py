from dataclasses import replace
from types import SimpleNamespace

import discord
import pytest
from conftest import Member

from nex.config import UserError
from nex.services.moderation import status_of


async def register(db, guild, bot_id, owner=10, **kwargs):
    await db.reserve(guild.id, owner, bot_id, 30, 20)
    await db.execute(
        "UPDATE registrations SET state='active' WHERE guild_id=? AND bot_id=?",
        (str(guild.id), str(bot_id)),
    )
    guild.members[bot_id] = Member(bot_id, bot=True, **kwargs)


async def test_purge_excludes_unknown_online_unregistered_protected(harness, guild, db):
    await register(db, guild, 20)
    await register(db, guild, 21, owner=11, status=discord.Status.online)
    await register(db, guild, 22, owner=12)
    await db.execute("INSERT INTO whitelist VALUES('100','bot','22')")
    guild.members[23] = Member(23, bot=True)
    targets, skips = await harness.moderation.preview(guild)
    assert targets == [20]
    assert len(skips) == 2
    guild.chunked = False
    targets, skips = await harness.moderation.preview(guild)
    assert not targets
    assert len(skips) == 3
    guild.members[23].kick.assert_not_awaited()


async def test_purge_revalidates_presence_after_preview(harness, guild, db):
    await register(db, guild, 20)
    targets, _ = await harness.moderation.preview(guild)
    guild.members[20].status = discord.Status.online
    results = await harness.moderation.purge(guild, 1, targets)
    assert "skipped" in results[0]
    guild.members[20].kick.assert_not_awaited()
    assert await db.registration(100, 20)


async def test_purge_revalidates_whitelist(harness, guild, db):
    await register(db, guild, 20)
    targets, _ = await harness.moderation.preview(guild)
    await db.execute("INSERT INTO whitelist VALUES('0','bot','20')")
    results = await harness.moderation.purge(guild, 1, targets)
    assert "protected" in results[0]
    guild.members[20].kick.assert_not_awaited()


async def test_failed_kick_retains_registration_and_records_failure(harness, guild, db):
    await register(db, guild, 20)
    guild.members[20].kick.side_effect = discord.Forbidden(
        SimpleNamespace(status=403, reason="Forbidden"), "blocked"
    )
    results = await harness.moderation.purge(guild, 1, [20])
    assert "failed" in results[0]
    assert await db.registration(100, 20)
    assert "failed" in (await db.rows("SELECT result FROM audit"))[-1]["result"]


async def test_purge_success_and_persistent_cooldown(harness, guild, db):
    await register(db, guild, 20)
    result = await harness.moderation.purge(guild, 1, [20])
    assert "success" in result[0]
    assert not await db.registration(100, 20)
    with pytest.raises(UserError, match="cooldown"):
        await harness.moderation.purge(guild, 1, [])


async def test_no_authority_no_action(harness, guild, db):
    await register(db, guild, 20)
    with pytest.raises(UserError, match="authorized"):
        await harness.moderation.purge(guild, 10, [20])
    with pytest.raises(UserError, match="authorized"):
        await harness.moderation.moderate_one(guild, 10, 21, "ban")
    with pytest.raises(UserError, match="owner"):
        await harness.moderation.remove(guild, 11, 20)
    guild.members[20].kick.assert_not_awaited()
    guild.ban.assert_not_awaited()


async def test_remove_preserves_on_failure_and_releases_on_success(harness, guild, db):
    await register(db, guild, 20)
    guild.members[20].kick.side_effect = discord.Forbidden(
        SimpleNamespace(status=403, reason="Forbidden"), "blocked"
    )
    with pytest.raises(discord.Forbidden):
        await harness.moderation.remove(guild, 10, 20)
    assert await db.registration(100, 20)
    guild.members[20].kick.side_effect = None
    await harness.moderation.remove(guild, 10, 20)
    assert not await db.registration(100, 20)


async def test_remove_absence_policy_and_non_bot_guard(harness, guild, db):
    await register(db, guild, 20)
    guild.members[20].bot = False
    with pytest.raises(UserError, match="not a bot"):
        await harness.moderation.remove(guild, 10, 20)
    del guild.members[20]
    assert "absent" in await harness.moderation.remove(guild, 10, 20)
    assert not await db.registration(100, 20)


async def test_global_kick_does_not_ban_and_reports_results(harness, guild):
    guild.members[20] = Member(20)
    results = await harness.moderation.global_action(1, 20, "kick", [100, 101])
    guild.members[20].kick.assert_awaited_once()
    guild.ban.assert_not_awaited()
    assert "success" in results[0] and "no longer available" in results[1]


async def test_global_ban_protected_user_and_guild(harness, guild, db):
    await db.execute("INSERT INTO whitelist VALUES('0','user','20')")
    results = await harness.moderation.global_action(1, 20, "ban", [100])
    assert "Protected" in results[0]
    guild.ban.assert_not_awaited()
    await db.execute("DELETE FROM whitelist")
    harness.auth.config = replace(harness.config, protected_guilds=frozenset({100}))
    results = await harness.moderation.global_action(1, 20, "ban", [100])
    assert "Protected" in results[0]


async def test_unban_calls_unban_only(harness, guild):
    result = await harness.moderation.global_action(1, 20, "unban", [100])
    assert "success" in result[0]
    guild.unban.assert_awaited_once()
    guild.ban.assert_not_awaited()


def test_status_unknown_when_disconnected_or_incomplete(harness, guild):
    member = Member(20, bot=True)
    guild.members[20] = member
    assert status_of(harness, guild, member) == "offline"
    harness.is_ready = lambda: False
    assert status_of(harness, guild, member) == "unknown"


async def test_network_failure_does_not_stop_independent_targets(harness, guild, db):
    import aiohttp

    await register(db, guild, 20)
    await register(db, guild, 21, owner=11)
    guild.members[20].kick.side_effect = aiohttp.ServerDisconnectedError()
    results = await harness.moderation.purge(guild, 1, [20, 21])
    assert "failed" in results[0] and "connection unavailable" in results[0]
    assert "success" in results[1]
    assert await db.registration(100, 20)
    assert not await db.registration(100, 21)
