import time
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse

import discord
import pytest
from conftest import Member

from nex.config import UserError
from nex.database import Database
from nex.services.invitations import Invitations


async def expire(db):
    await db.execute("UPDATE registrations SET expires=?", (time.time() - 1,))


async def test_invite_real_oauth_url_and_timeout(harness, guild, db):
    url = await harness.invitations.create(guild, 10, 20)
    query = parse_qs(urlparse(url).query)
    assert query["client_id"] == ["20"]
    assert query["scope"] == ["bot"]
    assert query["guild_id"] == ["100"]
    assert await db.registrations(100, "pending")
    guild.members[10].add_roles.assert_awaited_once()
    await expire(db)
    await harness.invitations.recover_guild(guild)
    assert not await db.registrations(100, "pending")
    guild.members[10].remove_roles.assert_awaited_once()
    assert not await db.rows("SELECT * FROM role_leases")


async def test_preexisting_role_is_never_removed(harness, guild, db):
    guild.members[10].roles = [guild.role]
    await harness.invitations.create(guild, 10, 20)
    await expire(db)
    await harness.invitations.recover_guild(guild)
    guild.members[10].remove_roles.assert_not_awaited()
    assert guild.role in guild.members[10].roles


async def test_another_pending_session_keeps_shared_role(harness, guild, db):
    # Legacy overlapping leases/role configuration are safe even though new requests are unique.
    await db.execute("INSERT INTO role_leases VALUES('100','10','30',1)")
    guild.members[10].roles = [guild.role]
    await db.reserve(100, 10, 20, 30, 20)
    await harness.invitations.cleanup_roles(guild)
    guild.members[10].remove_roles.assert_not_awaited()
    await expire(db)
    await harness.invitations.recover_guild(guild)
    guild.members[10].remove_roles.assert_awaited_once()


async def test_expected_bot_only_activates(harness, guild, db):
    await harness.invitations.create(guild, 10, 20)
    guild.members[21] = Member(21, bot=True)
    await harness.invitations.activate(guild, 21)
    assert not await db.registration(100, 20)
    guild.members[20] = Member(20, bot=True)
    await harness.invitations.recover_guild(guild)
    assert (await db.registration(100, 20))["owner_id"] == "10"
    guild.members[10].remove_roles.assert_awaited_once()


async def test_existing_target_and_human_rejected(harness, guild, db):
    guild.members[20] = Member(20, bot=True)
    with pytest.raises(UserError, match="already in"):
        await harness.invitations.create(guild, 10, 20)
    harness.fetch_user = AsyncMock(return_value=SimpleNamespace(bot=False))
    with pytest.raises(UserError, match="not a bot"):
        await harness.invitations.create(guild, 10, 22)
    assert not await db.rows("SELECT * FROM registrations")


async def test_failed_role_grant_releases_reservation(harness, guild, db):
    guild.members[10].add_roles.side_effect = discord.Forbidden(
        SimpleNamespace(status=403, reason="Forbidden"), "no permission"
    )
    with pytest.raises(discord.Forbidden):
        await harness.invitations.create(guild, 10, 20)
    assert not await db.registrations(100, "pending")


async def test_role_cleanup_failure_is_durable(harness, guild, db):
    await harness.invitations.create(guild, 10, 20)
    await expire(db)
    guild.members[10].remove_roles.side_effect = discord.Forbidden(
        SimpleNamespace(status=403, reason="Forbidden"), "no permission"
    )
    with pytest.raises(discord.Forbidden):
        await harness.invitations.recover_guild(guild)
    assert not await db.registrations(100, "pending")
    assert await db.rows("SELECT * FROM role_leases")
    guild.members[10].remove_roles.side_effect = guild.members[10]._remove
    await harness.invitations.recover_guild(guild)
    assert not await db.rows("SELECT * FROM role_leases")


async def test_restart_recovers_expired_role_lease(harness, guild, tmp_path):
    path = str(tmp_path / "restart.db")
    first = Database(path)
    await first.open()
    await first.reserve(100, 10, 20, 30, 20)
    await first.execute("INSERT INTO role_leases VALUES('100','10','30',1)")
    await expire(first)
    await first.close()
    guild.members[10].roles = [guild.role]
    second = Database(path)
    await second.open()
    try:
        harness.db = second
        await Invitations(harness).recover_guild(guild)
        assert not await second.registrations(100, "pending")
        guild.members[10].remove_roles.assert_awaited_once()
    finally:
        await second.close()


async def test_missing_inviter_or_role_releases_lease(harness, guild, db):
    await harness.invitations.create(guild, 10, 20)
    del guild.members[10]
    await expire(db)
    await harness.invitations.recover_guild(guild)
    assert not await db.rows("SELECT * FROM role_leases")


async def test_missing_or_high_role_rejected(harness, guild, db):
    guild.role.position = 99
    with pytest.raises(UserError, match="assignable"):
        await harness.invitations.create(guild, 10, 20)
    assert not await db.rows("SELECT * FROM registrations")


async def test_reconcile_releases_only_confirmed_absent(harness, guild, db):
    await db.reserve(100, 10, 20, 30, 20)
    await db.execute("UPDATE registrations SET state='active'")
    guild.fetch_member.side_effect = discord.Forbidden(
        SimpleNamespace(status=403, reason="Forbidden"), "no permission"
    )
    with pytest.raises(discord.Forbidden):
        await harness.invitations.reconcile_active(guild)
    assert await db.registration(100, 20)
    guild.fetch_member.side_effect = guild._fetch
    await harness.invitations.reconcile_active(guild)
    assert not await db.registration(100, 20)


async def test_failed_lease_does_not_starve_other_cleanup(harness, guild, db):
    await harness.invitations.create(guild, 10, 20)
    await harness.invitations.create(guild, 11, 21)
    await expire(db)
    guild.members[10].remove_roles.side_effect = discord.Forbidden(
        SimpleNamespace(status=403, reason="Forbidden"), "blocked"
    )
    with pytest.raises(discord.Forbidden):
        await harness.invitations.recover_guild(guild)
    guild.members[11].remove_roles.assert_awaited_once()
    leases = await db.rows("SELECT * FROM role_leases")
    assert len(leases) == 1 and leases[0]["user_id"] == "10"


async def test_transport_failure_keeps_lease_and_recovers(harness, guild, db):
    import aiohttp

    await harness.invitations.create(guild, 10, 20)
    await expire(db)
    guild.members[10].remove_roles.side_effect = aiohttp.ServerDisconnectedError()
    with pytest.raises(aiohttp.ServerDisconnectedError):
        await harness.invitations.recover_guild(guild)
    assert await db.rows("SELECT * FROM role_leases")
    guild.members[10].remove_roles.side_effect = guild.members[10]._remove
    await harness.invitations.recover_guild(guild)
    assert not await db.rows("SELECT * FROM role_leases")


async def test_same_owner_can_invite_in_multiple_guilds(harness, guild, db):
    from dataclasses import replace

    from conftest import Guild, Role

    other = Guild(101)
    other.role = Role(31)
    harness.config = replace(harness.config, role_overrides=((101, 31),))
    service = Invitations(harness)
    await service.create(guild, 10, 20)
    await service.create(other, 10, 21)
    assert (await db.registrations(100, "pending"))[0]["role_id"] == "30"
    assert (await db.registrations(101, "pending"))[0]["role_id"] == "31"
