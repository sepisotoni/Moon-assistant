from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

logger = logging.getLogger(__name__)


class ErrorHandlerCog(commands.Cog, name="ErrorHandler"):
    """Global error handler that surfaces clean messages to users while logging the full
    exception server-side. Prevents unhandled exceptions from silently swallowing errors
    or leaking stack traces into Discord messages.
    """

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # Register the app_commands (slash command) error handler.
        bot.tree.on_error = self._on_app_command_error  # type: ignore[method-assign]

    # ------------------------------------------------------------------ #
    # Slash command errors
    # ------------------------------------------------------------------ #
    async def _on_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.CheckFailure):
            msg = "❌ You don't have permission to use this command."
        elif isinstance(error, app_commands.CommandOnCooldown):
            msg = f"⏳ Slow down — try again in {error.retry_after:.1f}s."
        elif isinstance(error, app_commands.MissingPermissions):
            missing = ", ".join(error.missing_permissions)
            msg = f"❌ You need these permissions: `{missing}`"
        elif isinstance(error, app_commands.BotMissingPermissions):
            missing = ", ".join(error.missing_permissions)
            msg = f"❌ I'm missing permissions needed for this: `{missing}`"
        else:
            logger.exception("Unhandled slash command error in /%s", getattr(interaction.command, "name", "?"), exc_info=error)
            msg = "❌ Something went wrong. The error has been logged."

        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except discord.HTTPException:
            pass  # interaction already expired; nothing we can do

    # ------------------------------------------------------------------ #
    # Prefix command errors
    # ------------------------------------------------------------------ #
    @commands.Cog.listener()
    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        # Ignore commands that are handled by their own local error handlers.
        if hasattr(ctx.command, "on_error"):
            return

        if isinstance(error, commands.CommandNotFound):
            return  # silently ignore unknown prefix commands

        if isinstance(error, commands.CheckFailure):
            await ctx.reply("❌ You don't have permission to use this command.", mention_author=False)
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.reply(
                f"❌ Missing argument: `{error.param.name}`. See `{ctx.prefix}help {ctx.invoked_with}`.",
                mention_author=False,
            )
        elif isinstance(error, commands.BadArgument):
            await ctx.reply(f"❌ Invalid argument: {error}", mention_author=False)
        elif isinstance(error, commands.MissingPermissions):
            missing = ", ".join(error.missing_permissions)
            await ctx.reply(f"❌ You need these permissions: `{missing}`", mention_author=False)
        elif isinstance(error, commands.BotMissingPermissions):
            missing = ", ".join(error.missing_permissions)
            await ctx.reply(f"❌ I'm missing permissions needed for this: `{missing}`", mention_author=False)
        elif isinstance(error, commands.CommandOnCooldown):
            await ctx.reply(f"⏳ Slow down — try again in {error.retry_after:.1f}s.", mention_author=False)
        else:
            logger.exception("Unhandled prefix command error for command %r", ctx.invoked_with, exc_info=error)
            await ctx.reply("❌ Something went wrong. The error has been logged.", mention_author=False)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ErrorHandlerCog(bot))
