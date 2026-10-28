"""Authorization-aware help, generated from the registered command tree."""

import discord
from discord.ext import commands

from nex.config import UserError
from nex.ui.components import OwnedView, pages, respond


async def visible_commands(ctx):
    visible = []
    for command in sorted(ctx.bot.commands, key=lambda c: c.name):
        if not command.enabled or command.hidden:
            continue
        if command.name == "globalpurge" and not ctx.bot.config.global_purge_guilds:
            continue
        privilege = command.extras.get("privilege", "developer")
        if privilege == "public" or await ctx.bot.auth.allowed(
            ctx.author.id, ctx.guild.id, privilege
        ):
            visible.append(command)
    return visible


def describe(command):
    usage = f"Nx {command.qualified_name} {command.signature}".strip()
    text = f"**{usage}**\n{command.help or 'See subcommands.'}"
    if isinstance(command, commands.Group):
        for child in sorted(command.commands, key=lambda c: c.name):
            if child.enabled and not child.hidden:
                text += f"\n`Nx {child.qualified_name} {child.signature}`"
    return text


class HelpView(OwnedView):
    def __init__(self, owner_id, available):
        super().__init__(owner_id, timeout=180)
        self.available = available
        self.category = None
        self.index = 0
        self.selected = None
        self.render()

    def render(self):
        self.clear_items()
        categories = sorted({c.extras["category"] for c in self.available})
        filtered = [
            c
            for c in self.available
            if self.category is None or c.extras["category"] == self.category
        ]
        entries = pages([describe(c) for c in filtered], size=4)
        self.index = min(self.index, len(entries) - 1)
        content = describe(self.selected) if self.selected else entries[self.index]
        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(
                    f"## Nex CodeX help\n{content}\n\nPage {self.index + 1}/{len(entries)}"
                )
            )
        )
        category_select = discord.ui.Select(
            placeholder="Choose category",
            options=[discord.SelectOption(label="All categories", value="all")]
            + [discord.SelectOption(label=name, value=name) for name in categories],
        )
        command_select = discord.ui.Select(
            placeholder="Choose command",
            options=[discord.SelectOption(label=c.name, value=c.name) for c in filtered],
        )

        async def choose_category(interaction):
            self.category = (
                None if category_select.values[0] == "all" else category_select.values[0]
            )
            self.index, self.selected = 0, None
            self.render()
            await interaction.response.edit_message(view=self)

        async def choose_command(interaction):
            self.selected = next(c for c in filtered if c.name == command_select.values[0])
            self.render()
            await interaction.response.edit_message(view=self)

        category_select.callback, command_select.callback = choose_category, choose_command
        self.add_item(discord.ui.ActionRow(category_select))
        self.add_item(discord.ui.ActionRow(command_select))
        previous = discord.ui.Button(label="Previous", disabled=self.index == 0)
        following = discord.ui.Button(label="Next", disabled=self.index == len(entries) - 1)

        async def back(interaction):
            self.selected = None
            self.index = max(0, self.index - 1)
            self.render()
            await interaction.response.edit_message(view=self)

        async def forward(interaction):
            self.selected = None
            self.index = min(len(entries) - 1, self.index + 1)
            self.render()
            await interaction.response.edit_message(view=self)

        previous.callback, following.callback = back, forward
        self.add_item(discord.ui.ActionRow(previous, following))


class Help(commands.Cog):
    @commands.command(
        name="help",
        extras={"category": "General", "privilege": "public"},
        help="Browse categories, commands and command-specific help.",
    )
    async def help_command(self, ctx, *, command_name: str = ""):
        available = await visible_commands(ctx)
        if command_name:
            command = ctx.bot.get_command(command_name)
            root = command.root_parent or command if command else None
            if command is None or root not in available or not command.enabled:
                raise UserError("That command is unavailable or you are not authorized to view it.")
            return await respond(ctx, "Command help", describe(command))
        view = HelpView(ctx.author.id, available)
        view.message = await ctx.send(view=view, allowed_mentions=discord.AllowedMentions.none())
