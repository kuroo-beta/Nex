import logging
import re
from pathlib import Path

import discord

from nex.errors import DISCORD_ERRORS

log = logging.getLogger(__name__)
FALLBACKS = {
    "success": "✅",
    "error": "❌",
    "warning": "⚠️",
    "info": "ℹ️",
    "loading": "⏳",
    "verified": "☑️",
    "ai": "🤖",
    "level_up": "⬆️",
    "coin": "🪙",
    "trophy": "🏆",
    "ticket": "🎫",
    "lock": "🔒",
    "unlock": "🔓",
    "ban": "⛔",
    "mute": "🔇",
    "kick": "👢",
    "role": "🏷️",
    "announce": "📣",
    "secure": "🛡️",
}


def normalize(name: str) -> str:
    return re.sub(r"[^a-z0-9_]", "", name.lower())


class EmojiManager:
    def __init__(self):
        self.mapping = dict(FALLBACKS)

    def get(self, name: str) -> str:
        return self.mapping.get(name, "ℹ️")

    async def initialize(self, client, directory: Path, provision: bool):
        try:
            existing = await client.fetch_application_emojis()
        except (*DISCORD_ERRORS, discord.ClientException) as exc:
            log.warning(
                "Emoji listing failed (%s, HTTP %s); using Unicode.",
                type(exc).__name__,
                getattr(exc, "status", "n/a"),
            )
            return
        by_name = {normalize(emoji.name): emoji for emoji in existing}
        for name in FALLBACKS:
            if name in by_name:
                self.mapping[name] = str(by_name[name])
                continue
            if not provision:
                continue
            asset = next(
                (
                    directory / f"{name}{ext}"
                    for ext in (".png", ".gif", ".jpg")
                    if (directory / f"{name}{ext}").is_file()
                ),
                None,
            )
            if asset is None:
                continue
            try:
                if asset.stat().st_size > 256 * 1024:
                    log.warning("Emoji asset %s exceeds 256 KiB; using fallback.", name)
                    continue
                emoji = await client.create_application_emoji(name=name, image=asset.read_bytes())
            except (*DISCORD_ERRORS, ValueError, discord.ClientException) as exc:
                log.warning(
                    "Emoji %s upload failed (%s, HTTP %s); using fallback.",
                    name,
                    type(exc).__name__,
                    getattr(exc, "status", "n/a"),
                )
            else:
                by_name[name] = emoji
                self.mapping[name] = str(emoji)
