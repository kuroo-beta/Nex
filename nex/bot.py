"""Application lifecycle, message routing, and private diagnostic logging."""

import asyncio
import logging
import re
import signal
from collections import defaultdict

import aiosqlite
import discord
from discord.ext import commands

from nex.commands import Administration, General
from nex.config import Config, UserError
from nex.database import Database
from nex.errors import DISCORD_ERRORS, failure_label
from nex.help import Help
from nex.services.authorization import Authorization
from nex.services.emoji import EmojiManager
from nex.services.invitations import Invitations
from nex.services.moderation import Moderation
from nex.ui.components import respond

log = logging.getLogger(__name__)


def prefix(bot, message):
    match = re.match(r"(?i)^nx\s+", message.content)
    return match.group() if match else "\x00"


class NexBot(commands.Bot):
    def __init__(self, config: Config, database: Database):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        intents.presences = True
        super().__init__(
            command_prefix=prefix,
            intents=intents,
            help_command=None,
            case_insensitive=True,
            strip_after_prefix=True,
            allowed_mentions=discord.AllowedMentions.none(),
            owner_id=config.owner_id,
        )
        self.config, self.db = config, database
        self.auth = Authorization(config, database)
        self.emoji_manager = EmojiManager()
        self.invitations = Invitations(self)
        self.moderation = Moderation(self)
        self.locks = defaultdict(asyncio.Lock)
        self.operational = asyncio.Event()
        self.initialized = False
        self.initializing = asyncio.Lock()
        self.worker = None
        self.add_check(self.command_gate)

    def guild_lock(self, guild_id):
        return self.locks[guild_id]

    async def setup_hook(self):
        await self.add_cog(General(self))
        await self.add_cog(Administration(self))
        await self.add_cog(Help())
        for command in self.walk_commands():
            command.ignore_extra = False
        # Worker waits for Discord readiness. No gateway calls in setup_hook.
        self.worker = asyncio.create_task(self.maintenance(), name="nex-invitation-recovery")

    async def on_ready(self):
        async with self.initializing:
            if self.application_id != self.config.client_id:
                log.error(
                    "BOT_CLIENT_ID does not match the authenticated application; shutting down."
                )
                await self.close()
                return
            if not self.initialized:
                await self.emoji_manager.initialize(
                    self, self.config.emoji_directory, self.config.emoji_provisioning
                )
                for guild in self.guilds:
                    try:
                        await self.invitations.recover_guild(guild)
                        await self.invitations.reconcile_active(guild)
                    except (*DISCORD_ERRORS, aiosqlite.Error):
                        log.warning("Startup recovery deferred for guild %s", guild.id)
                self.initialized = True
            self.operational.set()
            log.info("Nex CodeX ready in %s guilds", len(self.guilds))

    async def on_disconnect(self):
        self.operational.clear()

    async def maintenance(self):
        cycle = 0
        while not self.is_closed():
            await self.operational.wait()
            for guild in list(self.guilds):
                try:
                    await self.invitations.recover_guild(guild)
                    if cycle % 30 == 0:
                        await self.invitations.reconcile_active(guild)
                except (*DISCORD_ERRORS, aiosqlite.Error):
                    log.warning("Recovery failed in guild %s; will retry.", guild.id)
            cycle += 1
            await asyncio.sleep(2)

    async def on_member_join(self, member):
        if member.bot:
            try:
                async with self.guild_lock(member.guild.id):
                    await self.invitations.activate(member.guild, member.id)
                    await self.invitations.cleanup_roles(member.guild)
            except (*DISCORD_ERRORS, aiosqlite.Error):
                log.warning("Join recovery deferred in guild %s", member.guild.id)

    async def on_member_remove(self, member):
        try:
            async with self.guild_lock(member.guild.id):
                # Remove only the departed bot's registration, or a departed owner's pending slot.
                if member.bot:
                    await self.db.forget(member.guild.id, member.id)
                await self.db.execute(
                    "DELETE FROM registrations WHERE guild_id=? AND owner_id=? AND state='pending'",
                    (str(member.guild.id), str(member.id)),
                )
                await self.invitations.cleanup_roles(member.guild)
                await self.db.audit(0, member.guild.id, "member-left", member.id, "reconciled")
        except (*DISCORD_ERRORS, aiosqlite.Error):
            log.warning("Departure recovery deferred in guild %s", member.guild.id)

    async def on_message(self, message):
        if message.author.bot or message.webhook_id is not None:
            return
        ctx = await self.get_context(message)
        if ctx.prefix is None:
            return
        try:
            if await self.is_ignored(ctx):
                return
        except aiosqlite.Error as exc:
            await self.on_command_error(ctx, exc)
            return
        await self.invoke(ctx)

    async def is_ignored(self, ctx):
        if ctx.guild is None:
            return False
        ignored = await self.db.rows(
            "SELECT 1 FROM ignored_channels WHERE guild_id=? AND channel_id=?",
            (str(ctx.guild.id), str(ctx.channel.id)),
        )
        if not ignored or self.auth.developer(ctx.author.id):
            return False
        root = (ctx.command.root_parent or ctx.command) if ctx.command else None
        return not (
            root is not None
            and root.name == "ignore"
            and await self.auth.allowed(ctx.author.id, ctx.guild.id, "ignore")
        )

    async def command_gate(self, ctx):
        if ctx.guild is None:
            raise UserError("Use this command in a server channel.")
        if not self.operational.is_set():
            raise UserError("Nex CodeX is reconnecting or initializing. Please try again shortly.")
        return not await self.is_ignored(ctx)

    async def on_command_error(self, ctx, error):
        error = getattr(error, "original", error)
        if isinstance(error, commands.CommandNotFound):
            message = "Unknown command. Use Nx help."
        elif isinstance(
            error,
            (commands.MissingRequiredArgument, commands.TooManyArguments, commands.BadArgument),
        ):
            message = f"Invalid arguments. Use Nx help {ctx.command.qualified_name}."
        elif isinstance(error, UserError):
            message = str(error)
        elif isinstance(error, commands.CheckFailure):
            return  # Ignored channels intentionally have no response.
        elif isinstance(error, DISCORD_ERRORS):
            message = f"{failure_label(error)}. Check permissions and retry."
            log.warning("Command failed: %s", failure_label(error))
        elif isinstance(error, aiosqlite.Error):
            message = (
                "Database unavailable. The operation may be partial; "
                "check audit history before retrying."
            )
            log.error("Command database failure: %s", type(error).__name__)
        else:
            message = "An internal error occurred. Please contact the bot operator."
            log.error("Command failure: %s", type(error).__name__)
        try:
            await respond(ctx, "Command error", message, kind="error")
        except DISCORD_ERRORS:
            log.warning(
                "Unable to send command error; check View Channel/Send Messages permissions."
            )

    async def close(self):
        self.operational.clear()
        if self.worker is not None:
            self.worker.cancel()
            await asyncio.gather(self.worker, return_exceptions=True)
            self.worker = None
        # Durable leases and expiries are recovered on next startup. Do not erase them here.
        await super().close()


async def run(config):
    database = Database(config.database)
    try:
        await database.open()
        async with NexBot(config, database) as bot:
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, lambda: asyncio.create_task(bot.close()))
            await bot.start(config.token)
    finally:
        await database.close()


def main():
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    # Avoid HTTP debug logging, environment dumps, and raw Discord request bodies.
    try:
        config = Config.from_env()
    except (ValueError, UserError) as exc:
        raise SystemExit(f"Configuration error: {exc}") from None
    try:
        asyncio.run(run(config))
    except discord.LoginFailure:
        raise SystemExit(
            "Discord rejected DISCORD_TOKEN. Check your private environment."
        ) from None
    except discord.PrivilegedIntentsRequired:
        raise SystemExit(
            "Enable Message Content, Server Members, and Presence intents in the Portal."
        ) from None


if __name__ == "__main__":
    main()
