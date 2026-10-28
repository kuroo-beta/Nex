"""Safe descriptions for Discord HTTP, rate limit and transport failures."""

import aiohttp
import discord

DISCORD_ERRORS = (
    discord.HTTPException,
    discord.RateLimited,
    aiohttp.ClientError,
    OSError,
    TimeoutError,
)


def failure_label(error: BaseException) -> str:
    if isinstance(error, discord.HTTPException):
        return f"Discord HTTP {error.status}"
    if isinstance(error, discord.RateLimited):
        return "Discord rate limit; retry later"
    return "Discord connection unavailable"
