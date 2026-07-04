from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from bot.services.search_service import SearchService


class SearchCog(commands.Cog):
    """Permission-aware message search."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.search_service = SearchService()

    @app_commands.command(name="search", description="Search archived messages (only in channels you can see).")
    @app_commands.describe(query="Text to search for")
    async def search(self, interaction: discord.Interaction, query: str) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        results = await self.search_service.search(guild=interaction.guild, member=interaction.user, query=query)

        if not results:
            await interaction.followup.send(
                "No matching messages found in channels you have access to.", ephemeral=True
            )
            return

        lines = []
        for r in results:
            channel = interaction.guild.get_channel(r.channel_id)
            channel_mention = channel.mention if channel else f"<#{r.channel_id}>"
            snippet = r.content if len(r.content) <= 200 else r.content[:200] + "…"
            lines.append(f"**{channel_mention}** — {r.author_name}: {snippet}")

        embed = discord.Embed(
            title=f"Search results for: {query}",
            description="\n\n".join(lines),
            color=discord.Color.blurple(),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SearchCog(bot))
