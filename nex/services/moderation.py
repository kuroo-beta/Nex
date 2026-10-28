"""Confirmed, authorized moderation; registration state follows successful Discord actions."""

import asyncio
import time

import discord

from nex.config import UserError
from nex.errors import DISCORD_ERRORS, failure_label
from nex.services.invitations import member_or_none


def status_of(bot, guild, member) -> str:
    # REST member objects do not carry presence. Use a fully chunked gateway cache only.
    if (
        not bot.is_ready()
        or guild.unavailable
        or not bot.intents.presences
        or not bot.intents.members
        or not guild.chunked
    ):
        return "unknown"
    cached = guild.get_member(member.id)
    if cached is None:
        return "unknown"
    if cached.status == discord.Status.offline:
        return "offline"
    if cached.status in {discord.Status.online, discord.Status.idle, discord.Status.dnd}:
        return "online"
    return "unknown"


class Moderation:
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
        self.auth = bot.auth
        self.config = bot.config

    async def eligible(self, guild, target: int, *, offline: bool):
        record = await self.db.registration(guild.id, target)
        if record is None:
            raise UserError("Skipped: target is no longer registered in this guild.")
        if await self.auth.protected(guild.id, target, "bot"):
            raise UserError("Skipped: target or guild is protected.")
        member = await member_or_none(guild, target)
        if member is None:
            return record, None
        if not member.bot:
            raise UserError("Skipped: target is not a bot.")
        if offline and status_of(self.bot, guild, member) != "offline":
            raise UserError("Skipped: status is online or unknown.")
        me = await guild.fetch_member(self.bot.user.id)
        if not me.guild_permissions.kick_members or member.top_role >= me.top_role:
            raise UserError("Missing Kick Members permission or role hierarchy prevents kicking.")
        if await self.auth.protected(guild.id, target, "bot"):
            raise UserError("Skipped: target or guild is protected.")
        if offline and status_of(self.bot, guild, member) != "offline":
            raise UserError("Skipped: status changed or is unknown.")
        return record, member

    async def preview(self, guild) -> tuple[list[int], list[str]]:
        targets, skips = [], []
        for row in await self.db.registrations(guild.id):
            target = int(row["bot_id"])
            try:
                _, member = await self.eligible(guild, target, offline=True)
                if member is None:
                    skips.append(f"{target}: absent; reconciliation will release its registration")
                else:
                    targets.append(target)
            except UserError as exc:
                skips.append(f"{target}: {exc}")
            except DISCORD_ERRORS:
                skips.append(f"{target}: membership/permissions unavailable")
        return targets, skips

    async def remove(self, guild, actor: int, target: int) -> str:
        async with self.bot.guild_lock(guild.id):
            record, member = await self.eligible(guild, target, offline=False)
            if int(record["owner_id"]) != actor and not self.auth.developer(actor):
                raise UserError("Only the registered owner or a developer may remove this bot.")
            if member is not None:
                try:
                    await member.kick(reason=f"Nex CodeX removal confirmed by {actor}")
                except DISCORD_ERRORS as exc:
                    await self.db.audit(
                        actor,
                        guild.id,
                        "remove",
                        target,
                        f"failed: {failure_label(exc)}; registration retained",
                    )
                    raise
            await self.db.forget(guild.id, target)
            result = (
                "kicked; registration released" if member else "confirmed absent; slot released"
            )
            await self.db.audit(actor, guild.id, "remove", target, result)
            return result

    async def purge(self, guild, actor: int, targets: list[int]) -> list[str]:
        async with self.bot.guild_lock(guild.id):
            await self.auth.require(actor, guild.id, "purge")
            cooldown = await self.db.rows(
                "SELECT until FROM cooldowns WHERE guild_id=?", (str(guild.id),)
            )
            if cooldown and cooldown[0]["until"] > time.time():
                raise UserError(
                    f"Purge cooldown: {int(cooldown[0]['until'] - time.time()) + 1}s left."
                )
            # Claim cooldown before acting; it survives a process crash.
            await self.db.execute(
                "INSERT INTO cooldowns VALUES(?,?) ON CONFLICT(guild_id) "
                "DO UPDATE SET until=excluded.until",
                (str(guild.id), time.time() + self.config.purge_cooldown),
            )
            results = []
            for target in targets:
                try:
                    await self.auth.require(actor, guild.id, "purge")
                    _, member = await self.eligible(guild, target, offline=True)
                    await self.auth.require(actor, guild.id, "purge")
                    if member is None:
                        await self.db.forget(guild.id, target)
                        result = "skipped: confirmed absent; slot released"
                    else:
                        await member.kick(reason=f"Nex CodeX offline bot purge by {actor}")
                        await self.db.forget(guild.id, target)
                        result = "success: kicked; slot released"
                except UserError as exc:
                    result = f"skipped: {exc}"
                except DISCORD_ERRORS as exc:
                    result = f"failed: {failure_label(exc)}; registration retained"
                await self.db.audit(actor, guild.id, "purge", target, result)
                results.append(f"{target}: {result}")
            return results or ["No eligible targets."]

    async def check_protection(self, guild, target):
        if (
            target == guild.owner_id
            or await self.auth.protected(guild.id, target, "user")
            or await self.auth.protected(guild.id, target, "bot")
        ):
            raise UserError("Protected user or guild; action skipped.")

    async def moderate_one(self, guild, actor: int, target: int, action: str) -> str:
        async with self.bot.guild_lock(guild.id):
            await self.auth.require(actor, guild.id, "ban" if action == "ban" else "developer")
            if (
                target == guild.owner_id
                or await self.auth.protected(guild.id, target, "user")
                or await self.auth.protected(guild.id, target, "bot")
            ):
                raise UserError("Protected user or guild; action skipped.")
            me = await guild.fetch_member(self.bot.user.id)
            permission = (
                me.guild_permissions.kick_members
                if action == "kick"
                else (me.guild_permissions.ban_members)
            )
            if not permission:
                raise UserError("Missing Kick Members or Ban Members permission.")
            if action == "unban":
                await self.check_protection(guild, target)
                await guild.unban(discord.Object(target), reason=f"Nex CodeX confirmed by {actor}")
            else:
                member = await member_or_none(guild, target)
                if member is not None and member.top_role >= me.top_role:
                    raise UserError("Role hierarchy prevents this action.")
                await self.check_protection(guild, target)
                await self.auth.require(actor, guild.id, "ban" if action == "ban" else "developer")
                if action == "kick":
                    if member is None:
                        return "skipped: user is absent"
                    await member.kick(reason=f"Nex CodeX global kick by {actor}")
                else:
                    await guild.ban(
                        discord.Object(target),
                        delete_message_seconds=0,
                        reason=f"Nex CodeX ban confirmed by {actor}",
                    )
            return f"success: {action} confirmed by Discord"

    async def global_action(
        self, actor: int, target: int, action: str, guild_ids: list[int]
    ) -> list[str]:
        semaphore = asyncio.Semaphore(self.config.concurrency)

        async def run(guild_id):
            async with semaphore:
                await self.auth.require(actor, guild_id, "developer")
                guild = self.bot.get_guild(guild_id)
                try:
                    if guild is None:
                        result = "skipped: guild no longer available"
                    else:
                        result = await self.moderate_one(guild, actor, target, action)
                except UserError as exc:
                    result = f"skipped: {exc}"
                except discord.NotFound:
                    result = "skipped: target/ban no longer exists"
                except DISCORD_ERRORS as exc:
                    result = f"failed: {failure_label(exc)}"
                await self.db.audit(actor, guild_id, "global" + action, target, result)
                return f"Guild {guild_id}: {result}"

        return await asyncio.gather(*(run(guild_id) for guild_id in guild_ids))
