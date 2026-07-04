from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from bot.database.models_ai import ConstitutionTier
from bot.services.permission_service import has_owner_or_founder_role


def owner_or_founder_only():
    async def predicate(interaction: discord.Interaction) -> bool:
        if not isinstance(interaction.user, discord.Member):
            return False
        return has_owner_or_founder_role(interaction.user)
    return app_commands.check(predicate)


class AIRulesCog(commands.Cog):
    """/airules slash commands — now unified with the Phase 2/3 ConstitutionService.

    Previously this cog wrote to the `ai_rules` table via AIRulesService. It has been
    redirected to write to `constitution_rules` via ConstitutionService so that rules
    added here are immediately visible to /ask, !ask, and all AI commands.
    """

    group = app_commands.Group(name="airules", description="Manage AI assistant rules (alias for /constitution)")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @property
    def _constitution(self):
        return self.bot.constitution_service  # type: ignore[attr-defined]

    @group.command(name="add", description="Add a new server AI rule (applied to all AI responses)")
    @owner_or_founder_only()
    async def add(self, interaction: discord.Interaction, name: str, rule_text: str, priority: int = 100) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message("Server only.", ephemeral=True)
            return
        await self._constitution.add_server_rule(
            guild_id=interaction.guild_id,
            title=name,
            rule_text=rule_text,
            created_by=interaction.user.id,
        )
        await interaction.response.send_message(
            f"✅ Added rule **{name}** — it will be applied to all future AI responses in this server.",
            ephemeral=True,
        )

    @group.command(name="list", description="List all active AI rules for this server")
    @owner_or_founder_only()
    async def list_rules(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            return
        rules = await self._constitution.list_rules(interaction.guild_id)
        if not rules:
            await interaction.response.send_message("No AI rules configured.", ephemeral=True)
            return
        lines = [
            f"`{r.id}` [T{r.tier.value}/{r.tier.name}] **{r.title}** {'✅' if r.is_enabled else '⬜'}\n  → {r.rule_text[:100]}"
            for r in rules
        ]
        embed = discord.Embed(title="Active AI Rules", description="\n\n".join(lines), color=discord.Color.blurple())
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @group.command(name="remove", description="Disable a rule by id (use !ai-rules list to find ids)")
    @owner_or_founder_only()
    async def remove(self, interaction: discord.Interaction, rule_id: int) -> None:
        rule = await self._constitution.set_enabled(rule_id, is_enabled=False)
        if rule is None:
            await interaction.response.send_message(f"No rule with id `{rule_id}` found.", ephemeral=True)
        else:
            await interaction.response.send_message(
                f"Disabled rule `{rule_id}` — **{rule.title}**. Re-enable it with `!ai-rules enable {rule_id}`.",
                ephemeral=True,
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AIRulesCog(bot))
