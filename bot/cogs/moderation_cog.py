from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from bot.config import get_settings
from bot.moderation.service import ModerationError, ModerationService
from bot.services.logging_service import format_ts

settings = get_settings()


class ModerationCog(commands.Cog):
    """Warnings, message deletion, and timeouts (capped at MAX_TIMEOUT_MINUTES)."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.moderation_service = ModerationService()

    @property
    def _log_service(self):
        return self.bot.db_log_service  # type: ignore[attr-defined]

    @app_commands.command(name="warn", description="Warn a member.")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def warn(self, interaction: discord.Interaction, member: discord.Member, reason: str) -> None:
        action = await self.moderation_service.warn(
            guild=interaction.guild,  # type: ignore[arg-type]
            member=member,
            moderator=interaction.user,  # type: ignore[arg-type]
            reason=reason,
        )
        await self._log_service.log(
            level="INFO",
            source="moderation.warn",
            message=f"{interaction.user} warned {member} for: {reason}",
            guild_id=interaction.guild_id,
        )
        await interaction.response.send_message(f"⚠️ {member.mention} has been warned. (Warning #{action.id})")

    @app_commands.command(name="warnings", description="List warnings for a member.")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def warnings(self, interaction: discord.Interaction, member: discord.Member) -> None:
        items = await self.moderation_service.list_warnings(
            guild_id=interaction.guild_id,  # type: ignore[arg-type]
            user_id=member.id,
        )
        if not items:
            await interaction.response.send_message(f"{member.mention} has no warnings.", ephemeral=True)
            return
        lines = [f"`{w.id}` {format_ts(w.created_at)} — {w.reason}" for w in items]
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @app_commands.command(name="purge", description="Delete a message by ID and log the action.")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def purge(self, interaction: discord.Interaction, message_id: str, reason: str | None = None) -> None:
        channel = interaction.channel
        try:
            message = await channel.fetch_message(int(message_id))  # type: ignore[union-attr]
        except (discord.NotFound, ValueError):
            await interaction.response.send_message("Message not found in this channel.", ephemeral=True)
            return

        try:
            await self.moderation_service.delete_message(
                message=message,
                moderator=interaction.user,  # type: ignore[arg-type]
                reason=reason,
            )
        except ModerationError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        await self._log_service.log(
            level="INFO",
            source="moderation.purge",
            message=f"{interaction.user} deleted message {message_id} in #{getattr(channel, 'name', channel)}: {reason}",
            guild_id=interaction.guild_id,
        )
        await interaction.response.send_message("🗑️ Message deleted.", ephemeral=True)

    @app_commands.command(name="timeout", description="Timeout a member for up to 60 minutes.")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def timeout(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        minutes: app_commands.Range[int, 1, 60],
        reason: str | None = None,
    ) -> None:
        try:
            await self.moderation_service.timeout(
                member=member,
                moderator=interaction.user,  # type: ignore[arg-type]
                minutes=minutes,
                reason=reason,
            )
        except ModerationError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        await self._log_service.log(
            level="INFO",
            source="moderation.timeout",
            message=f"{interaction.user} timed out {member} for {minutes}m: {reason}",
            guild_id=interaction.guild_id,
        )
        await interaction.response.send_message(f"⏱️ {member.mention} has been timed out for {minutes} minute(s).")

    @app_commands.command(name="untimeout", description="Remove an active timeout from a member.")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def untimeout(self, interaction: discord.Interaction, member: discord.Member, reason: str | None = None) -> None:
        await self.moderation_service.remove_timeout(
            member=member,
            moderator=interaction.user,  # type: ignore[arg-type]
            reason=reason,
        )
        await self._log_service.log(
            level="INFO",
            source="moderation.untimeout",
            message=f"{interaction.user} removed timeout for {member}: {reason}",
            guild_id=interaction.guild_id,
        )
        await interaction.response.send_message(f"✅ Timeout removed for {member.mention}.")


    @app_commands.command(name="bulkdelete", description="Delete recent messages from a member in this channel.")
    @app_commands.describe(
        member="Whose messages to delete",
        count="How many messages to delete (max 100)",
        reason="Reason for deletion",
    )
    @app_commands.checks.has_permissions(manage_messages=True)
    async def bulkdelete(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        count: app_commands.Range[int, 1, 100] = 10,
        reason: str | None = None,
    ) -> None:
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message("This command only works in a text channel.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        import datetime as dt
        cutoff = discord.utils.utcnow() - dt.timedelta(days=14)
        deleted = 0
        skipped = 0

        try:
            # Use purge with error_handler to skip already-deleted or too-old messages
            def check(m: discord.Message) -> bool:
                return m.author.id == member.id and not m.pinned

            msgs = await interaction.channel.purge(
                limit=count * 5,
                check=check,
                bulk=True,
                before=None,
                oldest_first=False,
                error_handler=lambda msg, exc: None,  # silently skip failures
            )
            deleted = len(msgs)
        except discord.Forbidden:
            await interaction.followup.send("❌ I don't have permission to delete messages.", ephemeral=True)
            return
        except Exception:
            # Fallback: manual one-by-one deletion with error handling
            try:
                fetched = [m async for m in interaction.channel.history(limit=count * 5) if m.author.id == member.id and not m.pinned]
                for msg in fetched[:count]:
                    try:
                        await msg.delete()
                        deleted += 1
                    except (discord.NotFound, discord.HTTPException):
                        skipped += 1
                        continue
            except Exception as e:
                await interaction.followup.send(f"❌ Failed: {e}", ephemeral=True)
                return

        await self._log_service.log(
            level="INFO",
            source="moderation.bulkdelete",
            message=f"{interaction.user} bulk-deleted {deleted} messages from {member} in #{interaction.channel.name}: {reason}",
            guild_id=interaction.guild_id,
        )
        result = f"🗑️ Deleted **{deleted}** message(s) from {member.mention}."
        if skipped:
            result += f" ({skipped} skipped — already deleted or too old)"
        await interaction.followup.send(result, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ModerationCog(bot))
