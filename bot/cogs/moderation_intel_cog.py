from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.moderation.heuristics import RaidDetector, SpamDetector
from bot.moderation.intelligence_service import ModerationIntelligenceService

logger = logging.getLogger(__name__)


class ModerationIntelCog(commands.Cog, name="ModerationIntel"):
    """AI-assisted moderation intelligence: report analysis, spam/raid heuristics."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._spam_detector = SpamDetector()
        self._raid_detector = RaidDetector()

    @property
    def _log_svc(self):
        return self.bot.db_log_service  # type: ignore[attr-defined]

    @property
    def _intel_svc(self) -> ModerationIntelligenceService:
        return self.bot.moderation_intel_service  # type: ignore[attr-defined]

    # ------------------------------------------------------------------
    # !report prefix command
    # ------------------------------------------------------------------
    @commands.command(name="report", help="!report @user <reason>  — submits an AI-assisted moderation report")
    async def report(self, ctx: commands.Context, member: discord.Member, *, reason: str) -> None:
        if ctx.guild is None:
            return
        async with ctx.typing():
            decision, parsed = await self._intel_svc.analyze_report(
                guild=ctx.guild,
                reported_user_id=member.id,
                reporter_id=ctx.author.id,
                channel_id=ctx.channel.id,
                reported_message_id=None,
                reason=reason,
                source="user",
            )

        embed = discord.Embed(
            title=f"Report submitted for {member.display_name}",
            color=discord.Color.orange(),
        )
        embed.add_field(name="Risk score", value=f"{parsed.get('risk_score', '?'):.2f}", inline=True)
        embed.add_field(name="AI confidence", value=f"{decision.confidence:.0%}", inline=True)
        embed.add_field(name="Recommended action", value=parsed.get("recommended_action", "—"), inline=True)
        embed.add_field(name="Evidence summary", value=parsed.get("evidence_summary", "—")[:1024], inline=False)
        if decision.escalate:
            embed.add_field(name="⚠️ Status", value="Escalated to staff for review.", inline=False)

        await ctx.reply(embed=embed, mention_author=False)
        await self._log_svc.log(
            level="INFO", source="moderation_intel.report",
            message=f"{ctx.author} reported {member} (risk={parsed.get('risk_score', '?'):.2f}, "
                    f"action={parsed.get('recommended_action')})",
            guild_id=ctx.guild.id,
        )

    # ------------------------------------------------------------------
    # Spam detection via on_message
    # ------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot:
            return
        if self._spam_detector.record_and_check(message.guild.id, message.author.id):
            try:
                await self._intel_svc.analyze_report(
                    guild=message.guild,
                    reported_user_id=message.author.id,
                    reporter_id=None,
                    channel_id=message.channel.id,
                    reported_message_id=message.id,
                    reason="Spam heuristic: exceeded message rate threshold",
                    source="heuristic:spam",
                )
                logger.info("Spam heuristic triggered for user %s in guild %s", message.author.id, message.guild.id)
            except Exception:
                logger.exception("Spam auto-report failed for user %s", message.author.id)

    # ------------------------------------------------------------------
    # Raid/brigading detection via on_member_join
    # ------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if self._raid_detector.record_join_and_check(member.guild.id):
            join_count = self._raid_detector.current_join_count(member.guild.id)
            logger.warning("Raid/brigading heuristic triggered in guild %s (%d joins in window)", member.guild.id, join_count)
            await self._log_svc.log(
                level="WARNING", source="moderation_intel.raid",
                message=f"Raid/brigading alert: {join_count} members joined in the detection window.",
                guild_id=member.guild.id,
            )
            # Notify the staff escalation channel if configured.
            esc_channel_id = self.bot.settings.escalation_channel_id  # type: ignore[attr-defined]
            if esc_channel_id:
                channel = member.guild.get_channel(esc_channel_id)
                if isinstance(channel, discord.TextChannel):
                    await channel.send(
                        f"⚠️ **Raid/brigading alert** — {join_count} members joined in quick succession. "
                        "Consider enabling slowmode or temporarily pausing invites."
                    )

    # ------------------------------------------------------------------
    # /report slash command (mirrors !report for convenience)
    # ------------------------------------------------------------------
    @app_commands.command(name="report", description="Report a member for AI-assisted moderation review.")
    @app_commands.describe(member="The member to report", reason="What happened?")
    async def slash_report(self, interaction: discord.Interaction, member: discord.Member, reason: str) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Servers only.", ephemeral=True)
            return
        await interaction.response.defer(thinking=True, ephemeral=True)
        decision, parsed = await self._intel_svc.analyze_report(
            guild=interaction.guild,
            reported_user_id=member.id,
            reporter_id=interaction.user.id,
            channel_id=interaction.channel_id,
            reported_message_id=None,
            reason=reason,
            source="user",
        )
        status = "⚠️ Escalated to staff." if decision.escalate else f"Recommended action: **{parsed.get('recommended_action')}**"
        await interaction.followup.send(
            f"Report submitted for {member.mention}. {status}\n"
            f"Risk: {parsed.get('risk_score', 0):.2f} | Confidence: {decision.confidence:.0%}",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ModerationIntelCog(bot))
