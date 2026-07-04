from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands


class AdminCog(commands.Cog):
    """Utility/admin commands."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="ping", description="Check that the bot is alive.")
    async def ping(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(f"🏓 Pong! {round(self.bot.latency * 1000)}ms")

    @app_commands.command(name="sync", description="Sync slash commands (bot owner only).")
    async def sync(self, interaction: discord.Interaction) -> None:
        if not await self.bot.is_owner(interaction.user):
            await interaction.response.send_message("Only the bot owner can run this.", ephemeral=True)
            return
        synced = await self.bot.tree.sync(guild=interaction.guild)
        await interaction.response.send_message(f"Synced {len(synced)} command(s).", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminCog(bot))
