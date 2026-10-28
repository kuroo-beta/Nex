from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from nex.config import Config, UserError, snowflake
from nex.services.emoji import EmojiManager


class Emoji:
    name = "SUCCESS"

    def __str__(self):
        return "<:success:123>"


async def test_existing_reused_and_duplicate_prevented(tmp_path):
    client = SimpleNamespace(
        fetch_application_emojis=AsyncMock(return_value=[Emoji()]),
        create_application_emoji=AsyncMock(),
    )
    (tmp_path / "success.png").write_bytes(b"synthetic asset")
    manager = EmojiManager()
    await manager.initialize(client, tmp_path, True)
    await manager.initialize(client, tmp_path, True)
    assert manager.get("success") == "<:success:123>"
    client.create_application_emoji.assert_not_awaited()


async def test_provision_success_and_missing_files(tmp_path):
    client = SimpleNamespace(
        fetch_application_emojis=AsyncMock(return_value=[]),
        create_application_emoji=AsyncMock(return_value=Emoji()),
    )
    (tmp_path / "success.png").write_bytes(b"synthetic asset")
    manager = EmojiManager()
    await manager.initialize(client, tmp_path, True)
    client.create_application_emoji.assert_awaited_once()
    assert manager.get("success") == "<:success:123>"
    assert manager.get("error") == "❌"


async def test_upload_failure_and_listing_failure_use_unicode(tmp_path):
    error = discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "no access")
    client = SimpleNamespace(
        fetch_application_emojis=AsyncMock(return_value=[]),
        create_application_emoji=AsyncMock(side_effect=error),
    )
    (tmp_path / "success.png").write_bytes(b"synthetic asset")
    manager = EmojiManager()
    await manager.initialize(client, tmp_path, True)
    assert manager.get("success") == "✅"
    client.fetch_application_emojis.side_effect = error
    await manager.initialize(client, tmp_path, True)
    assert manager.get("success") == "✅"


async def test_upload_opt_in_and_size_limit(tmp_path):
    client = SimpleNamespace(
        fetch_application_emojis=AsyncMock(return_value=[]), create_application_emoji=AsyncMock()
    )
    (tmp_path / "success.png").write_bytes(b"x" * (256 * 1024 + 1))
    manager = EmojiManager()
    await manager.initialize(client, tmp_path, False)
    await manager.initialize(client, tmp_path, True)
    client.create_application_emoji.assert_not_awaited()


@pytest.mark.parametrize("value", ["", "-1", "0", "abc", "2;DROP TABLE", str(2**64), "１２３"])
def test_id_validation(value):
    with pytest.raises(UserError):
        snowflake(value)


def test_config_missing_required_is_actionable(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for key in ("DISCORD_TOKEN", "BOT_OWNER_ID", "BOT_CLIENT_ID", "BOT_ADDER_ROLE_ID"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(ValueError, match="Missing required"):
        Config.from_env()


def test_config_validates_permissions_and_hides_token(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for key, value in {
        "DISCORD_TOKEN": "synthetic-secret",
        "BOT_OWNER_ID": "1",
        "BOT_CLIENT_ID": "2",
        "BOT_ADDER_ROLE_ID": "30",
    }.items():
        monkeypatch.setenv(key, value)
    config = Config.from_env()
    assert config.database == "data/nex_codex.db"
    assert "synthetic-secret" not in repr(config)
    assert config.emoji_directory == Path("assets/emojis")
    monkeypatch.setenv("INVITE_PERMISSIONS", "8")
    with pytest.raises(ValueError, match="Administrator"):
        Config.from_env()


async def test_emoji_transport_failure_uses_fallback(tmp_path):
    import aiohttp

    client = SimpleNamespace(
        fetch_application_emojis=AsyncMock(side_effect=aiohttp.ServerDisconnectedError())
    )
    manager = EmojiManager()
    await manager.initialize(client, tmp_path, True)
    assert manager.get("success") == "✅"


def test_per_guild_role_configuration(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for key, value in {
        "DISCORD_TOKEN": "synthetic",
        "BOT_OWNER_ID": "1",
        "BOT_CLIENT_ID": "2",
        "BOT_ADDER_ROLE_ID": "30",
        "BOT_ADDER_ROLE_IDS": "100:40,101:41",
    }.items():
        monkeypatch.setenv(key, value)
    config = Config.from_env()
    assert config.role_for(100) == 40
    assert config.role_for(101) == 41
    assert config.role_for(102) == 30
    monkeypatch.setenv("BOT_ADDER_ROLE_IDS", "100:40,100:41")
    with pytest.raises(ValueError, match="duplicate guild"):
        Config.from_env()
