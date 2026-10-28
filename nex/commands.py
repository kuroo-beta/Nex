"""Prefix command adapters. Services recheck authority at execution time."""

import asyncio
import math
from datetime import UTC, datetime

import aiosqlite
from discord.ext import commands

from nex.config import UserError, snowflake
from nex.errors import DISCORD_ERRORS, failure_label
from nex.services.invitations import member_or_none
from nex.services.moderation import status_of
from nex.ui.components import confirm, respond, safe, send_pages


def meta(category: str, privilege: str = "public") -> dict:
    return {"category": category, "privilege": privilege}


class General(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(extras=meta("General"), help="Show Discord gateway latency.")
    async def ping(self, ctx):
        latency = self.bot.latency
        metric = f"{latency * 1000:.0f} ms" if math.isfinite(latency) else "unavailable"
        await respond(
            ctx,
            "Nex CodeX health",
            f"Gateway latency: {metric}\nInitialization: ready\n"
            "Command response timing is not measured.",
        )

    @commands.command(extras=meta("General"), help="Show the configured support server.")
    async def support(self, ctx):
        await respond(
            ctx, "Support", self.bot.config.support_url or "Support URL is not configured."
        )

    @commands.command(extras=meta("Registration"), help="Reserve your slot and invite a bot.")
    async def invite(self, ctx, bot_id: str):
        url = await self.bot.invitations.create(ctx.guild, ctx.author.id, snowflake(bot_id))
        await respond(
            ctx,
            "Invitation pending",
            f"[Invite the bot]({url})\n"
            f"Your Bot Adder role lease expires in {self.bot.config.invite_timeout} seconds. "
            "Registration activates only after the expected bot joins. "
            "Discord requires Manage Server to authorize bot installations.",
        )

    @commands.command(
        name="list", extras=meta("Registration"), help="List this guild's registered bots."
    )
    async def list_bots(self, ctx):
        lines = []
        for row in await self.bot.db.registrations(ctx.guild.id):
            member = ctx.guild.get_member(int(row["bot_id"]))
            owner = ctx.guild.get_member(int(row["owner_id"]))
            status = status_of(self.bot, ctx.guild, member) if member else "unknown"
            date = datetime.fromtimestamp(row["activated"] or row["created"], UTC).isoformat()
            lines.append(
                f"**{safe(member.display_name) if member else 'Name unavailable'}** — "
                f"{row['bot_id']}\nOwner: {safe(owner.display_name) if owner else 'Unknown'} "
                f"({row['owner_id']}) · {status}\nRegistered: {date}"
            )
        await send_pages(ctx, "Registered bots", lines)

    @commands.command(extras=meta("Registration"), help="Confirm removal of your registered bot.")
    async def remove(self, ctx, bot_id: str):
        target = snowflake(bot_id)
        row = await self.bot.db.registration(ctx.guild.id, target)
        if row is None:
            raise UserError("This bot is not registered in this guild.")
        if int(row["owner_id"]) != ctx.author.id and not self.bot.auth.developer(ctx.author.id):
            raise UserError("Only the registered owner or a developer may remove this bot.")
        await self.bot.moderation.eligible(ctx.guild, target, offline=False)

        async def action():
            return [await self.bot.moderation.remove(ctx.guild, ctx.author.id, target)]

        await confirm(
            ctx, "Remove registered bot", f"Kick bot {target} and release its registration?", action
        )

    @commands.command(
        extras=meta("Moderation", "purge"), help="Preview and confirm an offline bot purge."
    )
    async def purge(self, ctx):
        await self.bot.auth.require(ctx.author.id, ctx.guild.id, "purge")
        targets, skips = await self.bot.moderation.preview(ctx.guild)
        await send_pages(
            ctx,
            "Purge preview",
            [f"Eligible: {len(targets)}"] + [f"Target: {target}" for target in targets] + skips,
        )
        if not targets:
            return

        async def action():
            return await self.bot.moderation.purge(ctx.guild, ctx.author.id, targets)

        await confirm(
            ctx,
            "Purge offline registered bots",
            f"Kick up to {len(targets)} bots in "
            f"guild {ctx.guild.id}? Each target will be revalidated.",
            action,
        )

    @commands.command(
        extras=meta("Developer", "code"), help="Show the configured public source link."
    )
    async def code(self, ctx):
        await self.bot.auth.require(ctx.author.id, ctx.guild.id, "code")
        await respond(
            ctx,
            "Source",
            self.bot.config.repository_url
            or "Public repository URL is not configured. Source packaging is disabled.",
        )


class Administration(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def global_preview(self, ctx, target: int, action: str):
        await self.bot.auth.require(ctx.author.id, ctx.guild.id, "developer")
        guild_ids = [guild.id for guild in self.bot.guilds]
        lines = []
        for guild in self.bot.guilds:
            protected = await self.bot.auth.protected(
                guild.id, target, "user"
            ) or await self.bot.auth.protected(guild.id, target, "bot")
            eligibility = (
                "protected; will skip" if protected else "permission/hierarchy checks required"
            )
            lines.append(f"{safe(guild.name)} ({guild.id}): {eligibility}")
        await send_pages(ctx, f"Global {action} impact", lines)

        async def execute():
            return await self.bot.moderation.global_action(ctx.author.id, target, action, guild_ids)

        await confirm(
            ctx,
            f"Global {action}",
            f"Target user: {target}\n"
            f"Attempt {action} in up to {len(guild_ids)} guilds. "
            "Ban can affect absent users; kick only affects present users.",
            execute,
        )

    @commands.command(
        extras=meta("Developer", "developer"), help="Confirm bans across eligible guilds."
    )
    async def globalban(self, ctx, user_id: str):
        await self.global_preview(ctx, snowflake(user_id), "ban")

    @commands.command(
        extras=meta("Developer", "developer"), help="Confirm kicks across eligible guilds."
    )
    async def globalkick(self, ctx, user_id: str):
        await self.global_preview(ctx, snowflake(user_id), "kick")

    @commands.command(
        extras=meta("Developer", "developer"), help="Confirm unbans across eligible guilds."
    )
    async def globalunban(self, ctx, user_id: str):
        await self.global_preview(ctx, snowflake(user_id), "unban")

    @commands.command(
        extras=meta("Developer", "developer"), help="Confirm leaving one specified guild."
    )
    async def serverleave(self, ctx, guild_id: str):
        await self.bot.auth.require(ctx.author.id, ctx.guild.id, "developer")
        target = snowflake(guild_id)
        guild = self.bot.get_guild(target)
        if guild is None:
            raise UserError("That guild is unavailable.")
        if await self.bot.auth.protected(target, target, "guild"):
            raise UserError("This guild is protected.")

        async def action():
            await self.bot.auth.require(ctx.author.id, ctx.guild.id, "developer")
            async with self.bot.guild_lock(target):
                current = self.bot.get_guild(target)
                if current is None or await self.bot.auth.protected(target, target, "guild"):
                    raise UserError("The guild is unavailable or protected.")
                # Do not strand a temporary role on an intentional departure.
                await self.bot.db.execute(
                    "DELETE FROM registrations WHERE guild_id=? AND state='pending'", (str(target),)
                )
                await self.bot.invitations.cleanup_roles(current)
                await current.leave()
                await self.bot.db.execute(
                    "DELETE FROM registrations WHERE guild_id=?", (str(target),)
                )
                await self.bot.db.audit(ctx.author.id, target, "serverleave", target, "left")
            return [f"Left guild {target}."]

        await confirm(ctx, "Leave guild", f"Leave {safe(guild.name)} ({target})?", action)

    @commands.command(
        extras=meta("Developer", "developer"), help="Preview purge in configured guilds."
    )
    async def globalpurge(self, ctx):
        await self.bot.auth.require(ctx.author.id, ctx.guild.id, "developer")
        scope = self.bot.config.global_purge_guilds
        if not scope:
            raise UserError("Global purge is disabled. Configure GLOBAL_PURGE_GUILD_IDS first.")
        snapshots, lines = {}, []
        for guild_id in sorted(scope):
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                lines.append(f"Guild {guild_id}: unavailable; skipped")
                continue
            targets, skips = await self.bot.moderation.preview(guild)
            snapshots[guild_id] = targets
            lines.extend(
                [f"Guild {guild_id}: {len(targets)} targets"]
                + [f"Guild {guild_id}: target {target}" for target in targets]
                + [f"Guild {guild_id}: {skip}" for skip in skips]
            )
        await send_pages(ctx, "Global purge preview", lines)
        if not any(snapshots.values()):
            return

        async def action():
            await self.bot.auth.require(ctx.author.id, ctx.guild.id, "developer")
            semaphore = asyncio.Semaphore(self.bot.config.concurrency)

            async def run(guild_id, targets):
                async with semaphore:
                    await self.bot.auth.require(ctx.author.id, guild_id, "developer")
                    guild = self.bot.get_guild(guild_id)
                    if guild is None or guild_id not in self.bot.config.global_purge_guilds:
                        return [f"Guild {guild_id}: skipped, scope or availability changed"]
                    try:
                        results = await self.bot.moderation.purge(guild, ctx.author.id, targets)
                    except UserError as exc:
                        results = [f"skipped: {exc}"]
                    except DISCORD_ERRORS as exc:
                        results = [f"failed: {failure_label(exc)}; check audit for partial results"]
                    except aiosqlite.Error:
                        results = ["failed: storage unavailable; results may be partial"]
                    return [f"Guild {guild_id}: {line}" for line in results]

            groups = await asyncio.gather(
                *(run(gid, targets) for gid, targets in snapshots.items() if targets)
            )
            return [line for group in groups for line in group]

        await confirm(
            ctx,
            "Global purge",
            f"Purge up to {sum(map(len, snapshots.values()))} "
            f"offline registered bots in {len(snapshots)} configured guilds?",
            action,
        )

    @commands.command(extras=meta("Moderation", "ban"), help="Confirm a ban in this guild only.")
    async def ban(self, ctx, user_id: str):
        await self.bot.auth.require(ctx.author.id, ctx.guild.id, "ban")
        target = snowflake(user_id)

        async def action():
            try:
                result = await self.bot.moderation.moderate_one(
                    ctx.guild, ctx.author.id, target, "ban"
                )
            except (UserError, *DISCORD_ERRORS) as exc:
                await self.bot.db.audit(
                    ctx.author.id, ctx.guild.id, "ban", target, f"failed: {type(exc).__name__}"
                )
                raise
            await self.bot.db.audit(ctx.author.id, ctx.guild.id, "ban", target, result)
            return [result]

        await confirm(
            ctx, "Current guild ban", f"Ban user {target} in guild {ctx.guild.id}?", action
        )

    @commands.group(
        invoke_without_command=True,
        extras=meta("Developer", "developer"),
        help="Manage guild-scoped trusted privileges: list/add/remove.",
    )
    async def trusted(self, ctx):
        await self.bot.auth.require(ctx.author.id, ctx.guild.id, "developer")
        if ctx.invoked_subcommand is None:
            await respond(
                ctx,
                "Trusted users",
                "Use Nx trusted list | add <user_id> "
                "<purge|ban|ignore> | remove <user_id> <purge|ban|ignore>.",
            )

    @trusted.command(name="list")
    async def trusted_list(self, ctx):
        await self.bot.auth.require(ctx.author.id, ctx.guild.id, "developer")
        rows = await self.bot.db.rows(
            "SELECT * FROM trusted WHERE guild_id=?", (str(ctx.guild.id),)
        )
        await send_pages(
            ctx, "Trusted privileges", [f"{row['user_id']}: {row['privilege']}" for row in rows]
        )

    async def edit_trusted(self, ctx, user_id: str, privilege: str, add: bool):
        await self.bot.auth.require(ctx.author.id, ctx.guild.id, "developer")
        target = snowflake(user_id)
        if privilege not in {"purge", "ban", "ignore"}:
            raise UserError(
                "Privilege must be purge, ban, or ignore. Trusted users are never developers."
            )
        if add and await member_or_none(ctx.guild, target) is None:
            raise UserError("Trusted user must belong to this guild.")
        query = (
            "INSERT OR IGNORE INTO trusted VALUES(?,?,?)"
            if add
            else ("DELETE FROM trusted WHERE guild_id=? AND user_id=? AND privilege=?")
        )
        await self.bot.db.execute(query, (str(ctx.guild.id), str(target), privilege))
        await self.bot.db.audit(
            ctx.author.id, ctx.guild.id, "trusted", target, {"add": add, "privilege": privilege}
        )
        await respond(
            ctx,
            "Trusted privileges updated",
            f"{target}: {privilege} {'granted' if add else 'revoked'}.",
            kind="success",
        )

    @trusted.command(name="add")
    async def trusted_add(self, ctx, user_id: str, privilege: str):
        await self.edit_trusted(ctx, user_id, privilege, True)

    @trusted.command(name="remove")
    async def trusted_remove(self, ctx, user_id: str, privilege: str):
        await self.edit_trusted(ctx, user_id, privilege, False)

    @commands.group(
        invoke_without_command=True,
        extras=meta("Developer", "developer"),
        help="Manage global or guild-scoped protection: list/add/remove.",
    )
    async def whitelist(self, ctx):
        await self.bot.auth.require(ctx.author.id, ctx.guild.id, "developer")
        if ctx.invoked_subcommand is None:
            await respond(
                ctx,
                "Whitelist",
                "Use Nx whitelist list | add <bot|user|guild> "
                "<target_id> [guild|global] | remove <bot|user|guild> <target_id> [guild|global]. "
                "Default scope is this guild.",
            )

    @whitelist.command(name="list")
    async def whitelist_list(self, ctx):
        await self.bot.auth.require(ctx.author.id, ctx.guild.id, "developer")
        rows = await self.bot.db.rows(
            "SELECT * FROM whitelist WHERE scope IN ('0',?)", (str(ctx.guild.id),)
        )
        await send_pages(
            ctx,
            "Protections",
            [
                f"{r['kind']} {r['target_id']}: {'global' if r['scope'] == '0' else 'this guild'}"
                for r in rows
            ],
        )

    async def edit_whitelist(self, ctx, kind: str, target_id: str, scope: str, add: bool):
        await self.bot.auth.require(ctx.author.id, ctx.guild.id, "developer")
        target = snowflake(target_id)
        if kind not in {"bot", "user", "guild"} or scope not in {"guild", "global"}:
            raise UserError("Kinds: bot/user/guild. Scopes: guild/global.")
        if kind == "guild" and scope == "guild" and target != ctx.guild.id:
            raise UserError("Use global scope to protect a different guild.")
        actual_scope = "0" if scope == "global" else str(ctx.guild.id)
        query = (
            "INSERT OR IGNORE INTO whitelist VALUES(?,?,?)"
            if add
            else ("DELETE FROM whitelist WHERE scope=? AND kind=? AND target_id=?")
        )
        await self.bot.db.execute(query, (actual_scope, kind, str(target)))
        await self.bot.db.audit(
            ctx.author.id,
            ctx.guild.id,
            "whitelist",
            target,
            {"add": add, "kind": kind, "scope": scope},
        )
        await respond(
            ctx,
            "Protection updated",
            f"{kind} {target}: {scope} protection {'enabled' if add else 'removed'}.",
            kind="success",
        )

    @whitelist.command(name="add")
    async def whitelist_add(self, ctx, kind: str, target_id: str, scope: str = "guild"):
        await self.edit_whitelist(ctx, kind, target_id, scope, True)

    @whitelist.command(name="remove")
    async def whitelist_remove(self, ctx, kind: str, target_id: str, scope: str = "guild"):
        await self.edit_whitelist(ctx, kind, target_id, scope, False)

    @commands.group(
        invoke_without_command=True,
        extras=meta("Configuration", "ignore"),
        help="Manage ignored channels in this guild: add/remove/list.",
    )
    async def ignore(self, ctx):
        await self.bot.auth.require(ctx.author.id, ctx.guild.id, "ignore")
        if ctx.invoked_subcommand is None:
            await respond(
                ctx, "Ignored channels", "Use Nx ignore add/remove <channel_id> or Nx ignore list."
            )

    @ignore.command(name="list")
    async def ignore_list(self, ctx):
        await self.bot.auth.require(ctx.author.id, ctx.guild.id, "ignore")
        rows = await self.bot.db.rows(
            "SELECT * FROM ignored_channels WHERE guild_id=?", (str(ctx.guild.id),)
        )
        await send_pages(
            ctx,
            "Ignored channels",
            [
                f"{row['channel_id']} "
                f"({bool(ctx.guild.get_channel_or_thread(int(row['channel_id'])))}: cached)"
                for row in rows
            ],
        )

    @ignore.command(name="add")
    async def ignore_add(self, ctx, channel_id: str):
        await self.bot.auth.require(ctx.author.id, ctx.guild.id, "ignore")
        target = snowflake(channel_id)
        channel = await self.bot.fetch_channel(target)
        if getattr(channel, "guild", None) is None or channel.guild.id != ctx.guild.id:
            raise UserError("The channel must belong to the current guild.")
        await self.bot.db.execute(
            "INSERT OR IGNORE INTO ignored_channels VALUES(?,?)", (str(ctx.guild.id), str(target))
        )
        await self.bot.db.audit(ctx.author.id, ctx.guild.id, "ignore-add", target, "enabled")
        await respond(
            ctx,
            "Channel ignored",
            f"{target}. Authorized ignore controls and developer controls remain available.",
            kind="success",
        )

    @ignore.command(name="remove")
    async def ignore_remove(self, ctx, channel_id: str):
        await self.bot.auth.require(ctx.author.id, ctx.guild.id, "ignore")
        target = snowflake(channel_id)
        await self.bot.db.execute(
            "DELETE FROM ignored_channels WHERE guild_id=? AND channel_id=?",
            (str(ctx.guild.id), str(target)),
        )
        await self.bot.db.audit(ctx.author.id, ctx.guild.id, "ignore-remove", target, "disabled")
        await respond(
            ctx,
            "Ignore removed",
            f"{target} removed from this guild's ignore list.",
            kind="success",
        )
