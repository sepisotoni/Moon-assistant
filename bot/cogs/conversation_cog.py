from __future__ import annotations

"""ConversationCog — makes the bot respond naturally to @mentions and DMs,
with full per-channel conversation history, typing indicators, reactions,
polls, thread creation, and pinning. Inspired by openclaw's Discord plugin.
"""

import logging

import discord
from discord import app_commands
from discord.ext import commands

logger = logging.getLogger(__name__)


class ConversationCog(commands.Cog, name="Conversation"):
    """Natural conversation: @mention or DM the bot to chat, no commands needed.

    Features inspired by openclaw:
    - Responds to @mentions anywhere in the server
    - Responds to DMs
    - Shows typing indicator while thinking
    - Adds ✅ reaction on success, ❌ on failure
    - /poll, /pin, /thread, /react, /clear commands
    - Full conversation history per channel (last 10 turns)
    """

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @property
    def _conversation(self):
        return self.bot.conversation  # type: ignore[attr-defined]

    @property
    def _support_engine(self):
        return self.bot.support_engine  # type: ignore[attr-defined]

    @property
    def _agent_service(self):
        return self.bot.agent_service  # type: ignore[attr-defined]

    # ------------------------------------------------------------------
    # @mention / DM listener — the core "natural conversation" feature
    # ------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return

        bot_mentioned = self.bot.user in message.mentions
        is_dm = isinstance(message.channel, discord.DMChannel)

        if not bot_mentioned and not is_dm:
            return

        # Strip the mention from the text
        text = message.content
        if self.bot.user:
            text = text.replace(f"<@{self.bot.user.id}>", "").replace(f"<@!{self.bot.user.id}>", "").strip()

        if not text:
            await message.reply("Hey! What can I help you with? 👋", mention_author=False)
            return

        async with message.channel.typing():
            guild = message.guild
            member = message.author if isinstance(message.author, discord.Member) else None

            # Inject conversation history
            history = self._conversation.history_messages(message.channel.id)

            # Check if this is a moderation action request
            if guild and member and self._agent_service.looks_like_action_request(text):
                has_perms = (
                    isinstance(member, discord.Member) and (
                        member.guild_permissions.manage_messages
                        or member.guild_permissions.moderate_members
                        or member.guild_permissions.administrator
                    )
                )
                if has_perms and isinstance(message.channel, discord.TextChannel):
                    result = await self._agent_service.execute(
                        guild=guild,
                        channel=message.channel,
                        requester=member,
                        text=text,
                        replied_message=message.reference.resolved
                            if message.reference and isinstance(message.reference.resolved, discord.Message)
                            else None,
                    )
                    response = result.response_text
                    await message.reply(response, mention_author=False)
                    await message.add_reaction("✅" if result.any_executed else "⚠️")
                    self._conversation.record(
                        channel_id=message.channel.id,
                        user_content=text,
                        assistant_content=response,
                        user_id=message.author.id,
                        user_name=message.author.display_name,
                    )
                    return

            # Normal support/Q&A response
            try:
                if guild and isinstance(member, discord.Member):
                    decision, intent = await self._support_engine.answer(
                        guild=guild,
                        member=member,
                        channel_id=message.channel.id,
                        question=text,
                        history=history,
                    )
                    response = decision.text
                else:
                    # DM fallback — no guild context
                    from bot.ai.base import AIMessage
                    from bot.ai.orchestrator import AIOrchestrator
                    orch: AIOrchestrator = self.bot.orchestrator  # type: ignore[attr-defined]
                    msgs = history + [AIMessage(role="user", content=text)]
                    decision = await orch.generate_for_task("support", msgs, guild_id=0, dual_review=False)
                    response = decision.text

                await message.reply(response[:2000], mention_author=False)
                await message.add_reaction("✅")

                self._conversation.record(
                    channel_id=message.channel.id,
                    user_content=text,
                    assistant_content=response,
                    user_id=message.author.id,
                    user_name=message.author.display_name,
                )
            except Exception as exc:
                logger.exception("ConversationCog: failed to respond to mention")
                await message.reply(f"Sorry, something went wrong. ({exc})", mention_author=False)
                await message.add_reaction("❌")

    # ------------------------------------------------------------------
    # /clear — reset conversation history for this channel
    # ------------------------------------------------------------------
    @app_commands.command(name="clear", description="Clear my conversation memory for this channel.")
    async def clear(self, interaction: discord.Interaction) -> None:
        self._conversation.clear(interaction.channel_id or 0)
        await interaction.response.send_message("🧹 Conversation history cleared!", ephemeral=True)

    # ------------------------------------------------------------------
    # /poll — create a Discord poll
    # ------------------------------------------------------------------
    @app_commands.command(name="poll", description="Create a poll.")
    @app_commands.describe(
        question="The poll question",
        options="Comma-separated options (e.g. Yes, No, Maybe)",
        duration_hours="How long the poll runs (default: 24 hours)",
    )
    async def poll(
        self,
        interaction: discord.Interaction,
        question: str,
        options: str,
        duration_hours: app_commands.Range[int, 1, 168] = 24,
    ) -> None:
        choices = [o.strip() for o in options.split(",") if o.strip()][:10]
        if len(choices) < 2:
            await interaction.response.send_message("Please provide at least 2 options separated by commas.", ephemeral=True)
            return

        answers = [discord.PollAnswer(text=c) for c in choices]
        poll_obj = discord.Poll(question=question, duration=__import__("datetime").timedelta(hours=duration_hours))
        for a in answers:
            poll_obj.add_answer(text=a.text)

        await interaction.response.send_message(poll=poll_obj)

    # ------------------------------------------------------------------
    # /pin — pin a message by ID
    # ------------------------------------------------------------------
    @app_commands.command(name="pin", description="Pin a message in this channel.")
    @app_commands.describe(message_id="The ID of the message to pin")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def pin(self, interaction: discord.Interaction, message_id: str) -> None:
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message("Works in text channels only.", ephemeral=True)
            return
        try:
            msg = await interaction.channel.fetch_message(int(message_id))
            await msg.pin()
            await interaction.response.send_message(f"📌 Message pinned!", ephemeral=True)
        except discord.NotFound:
            await interaction.response.send_message("Message not found.", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("I don't have permission to pin messages.", ephemeral=True)

    # ------------------------------------------------------------------
    # /thread — create a thread from a message
    # ------------------------------------------------------------------
    @app_commands.command(name="thread", description="Create a thread from a message.")
    @app_commands.describe(message_id="Message to attach the thread to", name="Thread name")
    @app_commands.checks.has_permissions(manage_threads=True)
    async def thread(self, interaction: discord.Interaction, message_id: str, name: str) -> None:
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message("Works in text channels only.", ephemeral=True)
            return
        try:
            msg = await interaction.channel.fetch_message(int(message_id))
            thread = await msg.create_thread(name=name)
            await interaction.response.send_message(f"🧵 Thread created: {thread.mention}", ephemeral=True)
        except discord.NotFound:
            await interaction.response.send_message("Message not found.", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("I don't have permission to create threads.", ephemeral=True)

    # ------------------------------------------------------------------
    # /react — add a reaction to a message
    # ------------------------------------------------------------------
    @app_commands.command(name="react", description="Add a reaction to a message.")
    @app_commands.describe(message_id="Message ID", emoji="Emoji to react with")
    async def react(self, interaction: discord.Interaction, message_id: str, emoji: str) -> None:
        if not isinstance(interaction.channel, (discord.TextChannel, discord.Thread)):
            await interaction.response.send_message("Works in text channels only.", ephemeral=True)
            return
        try:
            msg = await interaction.channel.fetch_message(int(message_id))
            await msg.add_reaction(emoji)
            await interaction.response.send_message(f"Reacted with {emoji}!", ephemeral=True)
        except discord.NotFound:
            await interaction.response.send_message("Message not found.", ephemeral=True)
        except discord.HTTPException:
            await interaction.response.send_message("Invalid emoji or missing permissions.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ConversationCog(bot))
