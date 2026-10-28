"""Restart-safe invitations and durable ownership of temporary roles."""

import logging
import time

import discord

from nex.config import UserError
from nex.errors import DISCORD_ERRORS, failure_label

log = logging.getLogger(__name__)


async def member_or_none(guild, user_id: int):
    try:
        return await guild.fetch_member(user_id)
    except discord.NotFound:
        return None


class Invitations:
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
        self.config = bot.config

    async def create(self, guild, user_id: int, bot_id: int) -> str:
        async with self.bot.guild_lock(guild.id):
            user = await self.bot.fetch_user(bot_id)
            if not user.bot:
                raise UserError("The supplied ID belongs to a user, not a bot.")
            if await member_or_none(guild, bot_id) is not None:
                raise UserError("That bot is already in this guild; no reservation was made.")
            member = await guild.fetch_member(user_id)
            role = guild.get_role(self.config.role_for(guild.id))
            me = await guild.fetch_member(self.bot.user.id)
            if (
                role is None
                or role.managed
                or role.is_default()
                or role >= me.top_role
                or not me.guild_permissions.manage_roles
            ):
                raise UserError("Configure an assignable Bot Adder role below my highest role.")
            await self.db.reserve(guild.id, user_id, bot_id, role.id, self.config.invite_timeout)
            try:
                # Record ownership intent before the HTTP request, so a crash can recover it.
                leases = await self.db.rows(
                    "SELECT * FROM role_leases WHERE guild_id=? AND user_id=? AND role_id=?",
                    (str(guild.id), str(user_id), str(role.id)),
                )
                owned = leases[0]["owned"] if leases else int(role not in member.roles)
                await self.db.execute(
                    "INSERT OR IGNORE INTO role_leases VALUES(?,?,?,?)",
                    (str(guild.id), str(user_id), str(role.id), owned),
                )
                if role not in member.roles:
                    await member.add_roles(role, reason="Nex CodeX invitation (temporary)")
                await self.db.execute(
                    "UPDATE registrations SET granted=? WHERE guild_id=? AND bot_id=?",
                    (owned, str(guild.id), str(bot_id)),
                )
                await self.db.audit(user_id, guild.id, "invite", bot_id, "pending")
            except BaseException:
                await self.db.forget(guild.id, bot_id)
                # Cleanup is retried from the durable lease if the gateway is unavailable.
                try:
                    await self.cleanup_roles(guild)
                except (*DISCORD_ERRORS, UserError):
                    log.warning("Invitation role cleanup deferred in guild %s", guild.id)
                raise
            # The target bot ID is its OAuth application ID; BOT_CLIENT_ID identifies Nex itself.
            return discord.utils.oauth_url(
                bot_id,
                permissions=discord.Permissions(self.config.invite_permissions),
                guild=discord.Object(guild.id),
                scopes=("bot",),
                disable_guild_select=True,
            )

    async def cleanup_roles(self, guild):
        leases = await self.db.rows("SELECT * FROM role_leases WHERE guild_id=?", (str(guild.id),))
        failures = []
        for lease in leases:
            pending = await self.db.rows(
                "SELECT 1 FROM registrations WHERE guild_id=? AND owner_id=? AND role_id=? "
                "AND state='pending' AND expires>?",
                (str(guild.id), lease["user_id"], lease["role_id"], time.time()),
            )
            if pending:
                continue
            try:
                if lease["owned"]:
                    member = await member_or_none(guild, int(lease["user_id"]))
                    role = guild.get_role(int(lease["role_id"]))
                    if member is not None and role is not None and role in member.roles:
                        await member.remove_roles(role, reason="Nex CodeX invitation ended")
                await self.db.execute(
                    "DELETE FROM role_leases WHERE guild_id=? AND user_id=? AND role_id=?",
                    (str(guild.id), lease["user_id"], lease["role_id"]),
                )
            except DISCORD_ERRORS as exc:
                log.warning(
                    "Role cleanup failed in guild %s for user %s (%s); retained lease.",
                    guild.id,
                    lease["user_id"],
                    failure_label(exc),
                )
                failures.append(exc)
        if failures:
            raise failures[0]

    async def activate(self, guild, bot_id: int):
        """Called under the shared guild lock; fetch confirms actual bot membership."""
        pending = await self.db.rows(
            "SELECT * FROM registrations WHERE guild_id=? AND bot_id=? AND state='pending'",
            (str(guild.id), str(bot_id)),
        )
        if not pending:
            return
        session = pending[0]
        if session["expires"] <= time.time():
            await self.db.forget(guild.id, bot_id)
            return
        member = await member_or_none(guild, bot_id)
        owner = await member_or_none(guild, int(session["owner_id"]))
        if member is None or not member.bot or owner is None:
            return
        changed = await self.db.execute(
            "UPDATE registrations SET state='active',activated=? WHERE guild_id=? AND bot_id=? "
            "AND state='pending' AND expires>?",
            (time.time(), str(guild.id), str(bot_id), time.time()),
        )
        if changed:
            await self.db.audit(
                int(session["owner_id"]), guild.id, "registration", bot_id, "active"
            )

    async def recover_guild(self, guild):
        async with self.bot.guild_lock(guild.id):
            pending = await self.db.registrations(guild.id, "pending")
            failures = []
            for session in pending:
                if session["expires"] <= time.time():
                    await self.db.forget(guild.id, int(session["bot_id"]))
                    await self.db.audit(
                        int(session["owner_id"]),
                        guild.id,
                        "invite",
                        int(session["bot_id"]),
                        "expired; reservation released",
                    )
                else:
                    try:
                        await self.activate(guild, int(session["bot_id"]))
                    except DISCORD_ERRORS as exc:
                        failures.append(exc)
            await self.cleanup_roles(guild)
            if failures:
                raise failures[0]

    async def reconcile_active(self, guild):
        async with self.bot.guild_lock(guild.id):
            for record in await self.db.registrations(guild.id):
                if await member_or_none(guild, int(record["bot_id"])) is None:
                    await self.db.forget(guild.id, int(record["bot_id"]))
                    await self.db.audit(
                        0,
                        guild.id,
                        "registration",
                        int(record["bot_id"]),
                        "released: membership confirmed absent",
                    )
