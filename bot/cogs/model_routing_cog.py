from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from bot.services.model_routing_service import ModelRoutingService
from bot.services.permission_service import has_owner_or_founder_role

VALID_TASK_TYPES = ("support", "moderation_review", "investigation", "translation", "summarization",
                    "explanation", "draft", "intent_classification", "*")


def _elevated_only():
    async def predicate(interaction: discord.Interaction) -> bool:
        if not isinstance(interaction.user, discord.Member) or not has_owner_or_founder_role(interaction.user):
            await interaction.response.send_message("❌ Requires Owner or Founder role.", ephemeral=True)
            return False
        return True
    return app_commands.check(predicate)


class ModelRoutingCog(commands.Cog, name="ModelRouting"):
    """Owner/Founder slash commands for the /aimodel model routing system."""

    group = app_commands.Group(name="aimodel", description="Manage dynamic AI model routing")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @property
    def _svc(self) -> ModelRoutingService:
        return self.bot.model_routing_service  # type: ignore[attr-defined]

    @group.command(name="list", description="List all configured AI models and their current health.")
    @_elevated_only()
    async def list_models(self, interaction: discord.Interaction) -> None:
        statuses = await self._svc.list_status()
        if not statuses:
            await interaction.response.send_message("No models in the registry.", ephemeral=True)
            return
        lines = []
        for ms in statuses:
            flag = "✅" if ms.config.is_enabled else "⬜"
            lines.append(f"{flag} `{ms.config.id}` **{ms.config.provider}** / {ms.config.model_name} "
                          f"(priority={ms.config.priority}, tasks={ms.config.task_types})\n"
                          f"    └ {ms.display}")
        embed = discord.Embed(title="AI Model Registry", description="\n".join(lines), color=discord.Color.blurple())
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @group.command(name="status", description="Show health stats for a specific model (by id).")
    @_elevated_only()
    async def model_status(self, interaction: discord.Interaction, model_id: int) -> None:
        statuses = await self._svc.list_status()
        ms = next((s for s in statuses if s.config.id == model_id), None)
        if ms is None:
            await interaction.response.send_message(f"No model with id {model_id}.", ephemeral=True)
            return
        embed = discord.Embed(title=f"{ms.config.provider}/{ms.config.model_name}", color=discord.Color.blurple())
        embed.add_field(name="Status", value=ms.display)
        embed.add_field(name="Enabled", value=str(ms.config.is_enabled))
        embed.add_field(name="Tasks", value=ms.config.task_types)
        if ms.health:
            embed.add_field(name="Successes", value=str(ms.health.success_count))
            embed.add_field(name="Failures", value=str(ms.health.failure_count))
            embed.add_field(name="Consecutive failures", value=str(ms.health.consecutive_failures))
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @group.command(name="set", description="Pin a specific model for a task type.")
    @_elevated_only()
    async def set_model(self, interaction: discord.Interaction, task_type: str, model_id: int) -> None:
        if interaction.guild is None:
            return
        config = await self._svc.get_model_by_id(model_id)
        if config is None:
            await interaction.response.send_message(f"No model with id {model_id}.", ephemeral=True)
            return
        await self._svc.set_override(
            guild_id=interaction.guild.id, task_type=task_type,
            model_config_id=model_id, set_by=interaction.user.id,
        )
        await interaction.response.send_message(
            f"✅ Pinned **{config.provider}/{config.model_name}** for task `{task_type}`.", ephemeral=True
        )

    @group.command(name="auto", description="Remove a manual model pin for a task type (restore automatic routing).")
    @_elevated_only()
    async def auto_route(self, interaction: discord.Interaction, task_type: str) -> None:
        if interaction.guild is None:
            return
        await self._svc.clear_override(guild_id=interaction.guild.id, task_type=task_type)
        await interaction.response.send_message(
            f"✅ Removed manual override for `{task_type}`. Routing is now automatic.", ephemeral=True
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ModelRoutingCog(bot))
