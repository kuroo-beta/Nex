import logging
from collections.abc import Awaitable, Callable

import aiosqlite
import discord

from nex.config import UserError
from nex.errors import DISCORD_ERRORS, failure_label

log = logging.getLogger(__name__)
COLORS = {
    "success": 0x43B581,
    "error": 0xED4245,
    "warning": 0xFEE75C,
    "info": 0x5865F2,
    "loading": 0x5865F2,
}
ICONS = {"success": "✅", "error": "❌", "warning": "⚠️", "info": "ℹ️", "loading": "⏳"}


def safe(value) -> str:
    return discord.utils.escape_markdown(discord.utils.escape_mentions(str(value)))


def panel(title: str, body: str, *, kind: str = "info", emojis=None) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=180)
    icon = emojis.get(kind) if emojis else ICONS.get(kind, "ℹ️")
    view.add_item(
        discord.ui.Container(
            discord.ui.TextDisplay(f"## {icon} {title}\n{body[:3500]}"),
            accent_colour=COLORS.get(kind, COLORS["info"]),
        )
    )
    return view


async def respond(ctx, title: str, body: str, *, kind="info"):
    return await ctx.send(
        view=panel(title, body, kind=kind, emojis=ctx.bot.emoji_manager),
        allowed_mentions=discord.AllowedMentions.none(),
    )


def pages(lines: list[str], size: int = 8) -> list[str]:
    result, current = [], []
    for line in lines:
        line = line[:1800]
        if current and (len(current) >= size or len("\n".join(current)) + len(line) > 3000):
            result.append("\n".join(current))
            current = []
        current.append(line)
    if current:
        result.append("\n".join(current))
    return result or ["No entries."]


class OwnedView(discord.ui.LayoutView):
    def __init__(self, owner_id: int, *, timeout=90):
        super().__init__(timeout=timeout)
        self.owner_id = owner_id
        self.message = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                view=panel(
                    "Access denied",
                    "Only the requesting user can use these controls.",
                    kind="error",
                ),
                ephemeral=True,
            )
            return False
        return True

    async def on_timeout(self):
        for item in self.walk_children():
            if isinstance(item, (discord.ui.Button, discord.ui.Select)):
                item.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except DISCORD_ERRORS:
                log.info("Expired interface could not be edited (deleted or inaccessible).")
        self.stop()

    async def on_error(self, interaction, error, item):
        log.error("Component callback failed: %s", type(error).__name__)
        if not interaction.response.is_done():
            await interaction.response.send_message(
                view=panel("Action failed", "Please retry the command.", kind="error"),
                ephemeral=True,
            )
        else:
            try:
                await interaction.followup.send(
                    view=panel("Action failed", "Please retry the command.", kind="error"),
                    ephemeral=True,
                )
            except DISCORD_ERRORS:
                log.info("Could not deliver component error response.")


class Pager(OwnedView):
    def __init__(self, owner_id: int, title: str, entries: list[str]):
        super().__init__(owner_id, timeout=180)
        self.title, self.entries, self.index = title, entries, 0
        self.render()

    def render(self):
        self.clear_items()
        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(
                    f"## {self.title}\n{self.entries[self.index]}\n\n"
                    f"Page {self.index + 1}/{len(self.entries)}"
                )
            )
        )
        if len(self.entries) > 1:
            previous = discord.ui.Button(label="Previous", disabled=self.index == 0)
            following = discord.ui.Button(
                label="Next", disabled=self.index == len(self.entries) - 1
            )

            async def back(interaction):
                self.index = max(0, self.index - 1)
                self.render()
                await interaction.response.edit_message(view=self)

            async def forward(interaction):
                self.index = min(len(self.entries) - 1, self.index + 1)
                self.render()
                await interaction.response.edit_message(view=self)

            previous.callback, following.callback = back, forward
            self.add_item(discord.ui.ActionRow(previous, following))


async def send_pages(ctx, title: str, lines: list[str]):
    view = Pager(ctx.author.id, title, pages(lines))
    view.message = await ctx.send(view=view, allowed_mentions=discord.AllowedMentions.none())


class Confirmation(OwnedView):
    def __init__(
        self, owner_id: int, title: str, summary: str, action: Callable[[], Awaitable[list[str]]]
    ):
        super().__init__(owner_id)
        self.action = action
        self.used = False
        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(f"## ⚠️ {title}\n{summary[:3000]}\n\nExpires in 90 seconds.")
            )
        )
        confirm = discord.ui.Button(label="Confirm", style=discord.ButtonStyle.danger)
        cancel = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.secondary)
        confirm.callback = self.confirm
        cancel.callback = self.cancel
        self.add_item(discord.ui.ActionRow(confirm, cancel))

    async def cancel(self, interaction):
        if self.used:
            return
        self.used = True
        self.stop()
        await interaction.response.edit_message(view=panel("Cancelled", "No action was requested."))

    async def confirm(self, interaction):
        if self.used:
            await interaction.response.send_message(
                view=panel("Already handled", "Run a new command."), ephemeral=True
            )
            return
        self.used = True
        self.stop()
        await interaction.response.defer()
        await interaction.edit_original_response(
            view=panel("Working", "Processing…", kind="loading")
        )
        try:
            lines = await self.action()
        except UserError as exc:
            await interaction.edit_original_response(
                view=panel("Action declined", str(exc), kind="error")
            )
            return
        except DISCORD_ERRORS as exc:
            await interaction.edit_original_response(
                view=panel(
                    "Discord request failed",
                    f"{failure_label(exc)}. Check permissions and retry.",
                    kind="error",
                )
            )
            return
        except aiosqlite.Error:
            log.error("Database failure during confirmed action.")
            await interaction.edit_original_response(
                view=panel(
                    "Storage unavailable",
                    "The operation may be partial. Check the audit log before retrying.",
                    kind="error",
                )
            )
            return
        result = Pager(self.owner_id, "Results", pages(lines))
        result.message = await interaction.edit_original_response(view=result)


async def confirm(ctx, title: str, summary: str, action):
    view = Confirmation(ctx.author.id, title, summary, action)
    view.message = await ctx.send(view=view, allowed_mentions=discord.AllowedMentions.none())
