from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiosqlite
import discord
import pytest
import pytest_asyncio
from discord.ext import commands

from nex.bot import NexBot
from nex.commands import Administration
from nex.config import UserError
from nex.help import HelpView, visible_commands
from nex.ui.components import Confirmation, Pager, panel


@pytest_asyncio.fixture
async def app(config, db, guild):
    async with NexBot(config, db) as bot:
        bot._connection.user = SimpleNamespace(id=2)
        await bot.setup_hook()
        bot.operational.set()
        yield bot


async def context(app, guild, text="Nx ping", author=10, channel=500):
    message = SimpleNamespace(
        content=text,
        author=SimpleNamespace(id=author, bot=False),
        guild=guild,
        channel=SimpleNamespace(id=channel),
        _state=app._connection,
        attachments=[],
        webhook_id=None,
    )
    ctx = await app.get_context(message)
    ctx.send = AsyncMock(return_value=SimpleNamespace(edit=AsyncMock()))
    return ctx


@pytest.mark.parametrize("prefix", ["Nx", "nx", "NX", "nX"])
async def test_prefix_and_command_execution(app, guild, prefix):
    ctx = await context(app, guild, f"{prefix} ping")
    assert ctx.command.name == "ping"
    await ctx.command.invoke(ctx)
    assert ctx.send.await_args.kwargs["view"].has_components_v2()


@pytest.mark.parametrize("text", ["Nxping", "annex ping", "hello Nx ping", " Nx ping"])
async def test_unrelated_text_never_executes(app, guild, text):
    assert (await context(app, guild, text)).command is None


@pytest.mark.parametrize(
    "text,exception",
    [
        ("Nx invite", commands.MissingRequiredArgument),
        ("Nx ping extra", commands.TooManyArguments),
        ("Nx remove 20 extra", commands.TooManyArguments),
    ],
)
async def test_strict_argument_parsing(app, guild, text, exception):
    ctx = await context(app, guild, text)
    with pytest.raises(exception):
        await ctx.command.invoke(ctx)


@pytest.mark.parametrize(
    "text",
    [
        "Nx globalban 20",
        "Nx trusted list",
        "Nx trusted add 11 ban",
        "Nx whitelist add bot 20",
        "Nx ignore add 500",
        "Nx code",
    ],
)
async def test_unauthorized_commands_rejected(app, guild, text):
    ctx = await context(app, guild, text)
    with pytest.raises(commands.CommandInvokeError) as exc:
        await ctx.command.invoke(ctx)
    assert isinstance(exc.value.original, UserError)
    ctx.send.assert_not_awaited()


async def test_trusted_privileges_narrow_and_guild_scoped(app, guild, db):
    assert await app.auth.allowed(1, 100, "developer")
    assert await app.auth.allowed(3, 100, "developer")
    assert not await app.auth.allowed(1, 100, "code")  # owner must be explicitly listed
    assert await app.auth.allowed(4, 100, "code")
    await db.execute("INSERT INTO trusted VALUES('100','10','purge')")
    assert await app.auth.allowed(10, 100, "purge")
    assert not await app.auth.allowed(10, 101, "purge")
    assert not await app.auth.allowed(10, 100, "ban")
    assert not await app.auth.allowed(10, 100, "developer")
    assert not await app.auth.allowed(10, 100, "code")


async def test_trusted_management(app, guild, db):
    cog = app.get_cog("Administration")
    ctx = await context(app, guild, author=1)
    await cog.edit_trusted(ctx, "11", "ban", True)
    assert await app.auth.allowed(11, 100, "ban")
    await cog.edit_trusted(ctx, "11", "ban", False)
    assert not await app.auth.allowed(11, 100, "ban")
    with pytest.raises(UserError):
        await cog.edit_trusted(ctx, "11", "developer", True)


async def test_whitelist_scopes(app, guild):
    cog = app.get_cog("Administration")
    ctx = await context(app, guild, author=1)
    await cog.edit_whitelist(ctx, "bot", "20", "guild", True)
    assert await app.auth.protected(100, 20, "bot")
    assert not await app.auth.protected(101, 20, "bot")
    await cog.edit_whitelist(ctx, "bot", "20", "guild", False)
    assert not await app.auth.protected(100, 20, "bot")


async def test_ignore_crud_and_guild_isolation(app, guild, db):
    cog = app.get_cog("Administration")
    app.fetch_channel = AsyncMock(return_value=SimpleNamespace(guild=guild))
    ctx = await context(app, guild, author=1)
    await Administration.ignore_add.callback(cog, ctx, "500")
    assert await db.rows("SELECT 1 FROM ignored_channels WHERE guild_id='100'")
    assert not await db.rows("SELECT 1 FROM ignored_channels WHERE guild_id='101'")
    ignored = await context(app, guild)
    assert not await app.command_gate(ignored)
    assert await app.command_gate(ctx)  # developer can recover settings
    await Administration.ignore_list.callback(cog, ctx)
    await Administration.ignore_remove.callback(cog, ctx, "500")
    assert not await db.rows("SELECT * FROM ignored_channels")
    app.fetch_channel = AsyncMock(return_value=SimpleNamespace(guild=SimpleNamespace(id=101)))
    with pytest.raises(UserError, match="current guild"):
        await Administration.ignore_add.callback(cog, ctx, "501")


async def test_code_returns_only_configured_link(app, guild, tmp_path, monkeypatch):
    # No package feature exists: secret/database fixtures cannot become attachments.
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("DISCORD_TOKEN=synthetic-secret")
    (tmp_path / "state.db").write_text("private audit records")
    ctx = await context(app, guild, "Nx code", author=4)
    await ctx.command.invoke(ctx)
    kwargs = ctx.send.await_args.kwargs
    assert "file" not in kwargs and "files" not in kwargs
    assert "synthetic-secret" not in str(kwargs["view"].to_components())
    assert "not configured" in str(kwargs["view"].to_components())


async def test_help_is_dynamic_authorized_and_v2(app, guild):
    ctx = await context(app, guild)
    available = await visible_commands(ctx)
    assert "globalban" not in {cmd.name for cmd in available}
    assert "ping" in {cmd.name for cmd in available}
    view = HelpView(10, available)
    assert view.has_components_v2()
    assert len([item for item in view.walk_children() if isinstance(item, discord.ui.Select)]) == 2
    ctx = await context(app, guild, author=1)
    assert "globalpurge" not in {cmd.name for cmd in await visible_commands(ctx)}


async def test_api_and_db_errors_safe_v2(app, guild):
    ctx = await context(app, guild)
    await app.on_command_error(ctx, aiosqlite.OperationalError("private SQL details"))
    payload = ctx.send.await_args.kwargs["view"].to_components()
    assert "private SQL details" not in str(payload)
    assert "Database unavailable" in str(payload)


async def test_bot_and_webhook_messages_ignored(app):
    app.process_commands = AsyncMock()
    await app.on_message(SimpleNamespace(author=SimpleNamespace(bot=True), webhook_id=None))
    await app.on_message(SimpleNamespace(author=SimpleNamespace(bot=False), webhook_id=20))
    app.process_commands.assert_not_awaited()


async def test_components_v2_serialization():
    view = panel("Title", "Body", kind="success")
    assert view.has_components_v2()
    assert view.to_components()[0]["type"] == 17  # real Discord Container
    pager = Pager(10, "Results", ["First", "Second"])
    assert pager.has_components_v2()
    assert len(pager.to_components()) == 2


async def test_confirmation_ownership_timeout_and_single_execution():
    action = AsyncMock(return_value=["done"])
    view = Confirmation(10, "Confirm", "Test", action)
    intruder = SimpleNamespace(
        user=SimpleNamespace(id=11), response=SimpleNamespace(send_message=AsyncMock())
    )
    assert not await view.interaction_check(intruder)
    action.assert_not_awaited()
    interaction = SimpleNamespace(
        user=SimpleNamespace(id=10),
        response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
        edit_original_response=AsyncMock(),
    )
    assert await view.interaction_check(interaction)
    await view.confirm(interaction)
    await view.confirm(interaction)
    action.assert_awaited_once()
    expiring = Confirmation(10, "Test", "Body", action)
    expiring.message = SimpleNamespace(edit=AsyncMock())
    await expiring.on_timeout()
    assert all(
        item.disabled for item in expiring.walk_children() if isinstance(item, discord.ui.Button)
    )


async def test_unknown_commands_silent_in_ignored_channel(app, guild, db):
    await db.execute("INSERT INTO ignored_channels VALUES('100','500')")
    ctx = await context(app, guild, "Nx nonexistent")
    assert ctx.command is None
    assert await app.is_ignored(ctx)
    app.get_context = AsyncMock(return_value=ctx)
    app.invoke = AsyncMock()
    await app.on_message(ctx.message)
    app.invoke.assert_not_awaited()


async def test_confirm_rechecks_revoked_purge_authority(app, guild, db):
    await db.execute("INSERT INTO trusted VALUES('100','10','purge')")
    await app.auth.require(10, 100, "purge")
    await db.execute("DELETE FROM trusted")
    with pytest.raises(UserError, match="authorized"):
        await app.moderation.purge(guild, 10, [])


async def test_layout_sets_required_discord_message_flag():
    from discord.http import handle_message_parameters

    view = panel("Title", "Body")
    with handle_message_parameters(view=view) as params:
        assert params.payload["flags"] & (1 << 15)
        assert params.payload["components"][0]["type"] == 17
        assert "embeds" not in params.payload


async def test_all_specified_commands_load_once_and_shutdown(app):
    assert {command.name for command in app.commands} == {
        "ping",
        "support",
        "invite",
        "list",
        "purge",
        "remove",
        "help",
        "globalban",
        "globalkick",
        "globalunban",
        "serverleave",
        "globalpurge",
        "trusted",
        "whitelist",
        "ban",
        "ignore",
        "code",
    }
    assert all(not command.ignore_extra for command in app.walk_commands())
    worker = app.worker
    await app.close()
    assert worker.done()


def test_documented_cli_rejects_missing_config(tmp_path):
    import os
    import subprocess
    import sys

    environment = os.environ.copy()
    for key in ("DISCORD_TOKEN", "BOT_OWNER_ID", "BOT_CLIENT_ID", "BOT_ADDER_ROLE_ID"):
        environment.pop(key, None)
    result = subprocess.run(
        [sys.executable, "-m", "nex.bot"],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 1
    assert "Missing required environment variables" in result.stderr
    assert "Traceback" not in result.stderr
