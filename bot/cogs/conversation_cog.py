from __future__ import annotations

"""ConversationCog — natural conversation with streaming, chunking, auto-threading,
and a model picker UI. Patterns adapted from openclaw's Discord plugin."""

import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.services.message_utils import chunk_text, send_chunked

logger = logging.getLogger(__name__)

# Auto-thread: if a channel has more than this many bot-turns, offer to move to a thread.
_AUTO_THREAD_TURN_THRESHOLD = 5


class ConversationCog(commands.Cog, name="Conversation"):
    """Natural conversation: @mention or DM the bot to chat without slash commands.

    Features (inspired by openclaw's Discord plugin):
    - @mention or DM the bot anywhere
    - Streaming simulation: shows ⏳ immediately, edits with final response
    - Long-response chunking: splits answers >2000 chars across messages
    - ✅ / ❌ reaction feedback
    - Auto-thread: offers to continue in a thread after several turns
    - /poll, /pin, /thread, /react, /clear commands
    - /model picker: inline buttons to switch AI model
    """

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @property
    def _conv(self):
        return self.bot.conversation  # type: ignore[attr-defined]

    @property
    def _engine(self):
        return self.bot.support_engine  # type: ignore[attr-defined]

    @property
    def _agent(self):
        return self.bot.agent_service  # type: ignore[attr-defined]

    # ------------------------------------------------------------------
    # @mention / DM  — core "natural conversation" listener
    # ------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return

        bot_mentioned = self.bot.user in message.mentions if self.bot.user else False
        is_dm = isinstance(message.channel, discord.DMChannel)

        if not bot_mentioned and not is_dm:
            return

        # Strip mention from text
        text = message.content
        if self.bot.user:
            text = text.replace(f"<@{self.bot.user.id}>", "").replace(f"<@!{self.bot.user.id}>", "").strip()

        if not text:
            await message.reply("Hey! What can I help you with? 👋", mention_author=False)
            return

        guild = message.guild
        member = message.author if isinstance(message.author, discord.Member) else None
        channel = message.channel

        # --- Moderation action path ---
        if guild and member and self._agent.looks_like_action_request(text):
            has_perms = isinstance(member, discord.Member) and (
                member.guild_permissions.manage_messages
                or member.guild_permissions.moderate_members
                or member.guild_permissions.administrator
            )
            if has_perms and isinstance(channel, discord.TextChannel):
                replied_msg = (
                    message.reference.resolved
                    if message.reference and isinstance(message.reference.resolved, discord.Message)
                    else None
                )
                async with channel.typing():
                    result = await self._agent.execute(
                        guild=guild, channel=channel, requester=member,
                        text=text, replied_message=replied_msg,
                    )
                await message.reply(result.response_text[:2000], mention_author=False)
                await message.add_reaction("✅" if result.any_executed else "⚠️")
                self._conv.record(channel_id=channel.id, user_content=text,
                                   assistant_content=result.response_text,
                                   user_id=message.author.id, user_name=message.author.display_name)
                return

        # --- Normal Q&A path with streaming simulation ---
        # Send placeholder immediately (openclaw draft-stream.ts pattern)
        placeholder = await message.reply("⏳", mention_author=False)

        try:
            async with channel.typing():
                history = self._conv.history_messages(channel.id)

                if guild and isinstance(member, discord.Member):
                    identity_facts = self.bot.identity_service.get(guild).known_facts  # type: ignore[attr-defined]
                    decision, intent = await self._engine.answer(
                        guild=guild, member=member,
                        channel_id=channel.id, question=text,
                        history=history, identity_facts=identity_facts,
                    )
                    response_text = decision.text
                else:
                    # DM — no guild context, plain generation
                    from bot.ai.base import AIMessage
                    msgs = history + [AIMessage(role="user", content=text)]
                    decision = await self.bot.orchestrator.generate_for_task(  # type: ignore[attr-defined]
                        "support", msgs, guild_id=0, dual_review=False
                    )
                    response_text = decision.text

            # Edit placeholder with first chunk; send additional chunks if needed
            chunks = chunk_text(response_text)
            await placeholder.edit(content=chunks[0])
            for extra in chunks[1:]:
                await channel.send(extra)

            await message.add_reaction("✅")
            self._conv.record(channel_id=channel.id, user_content=text,
                               assistant_content=response_text,
                               user_id=message.author.id, user_name=message.author.display_name)

            # Auto-thread suggestion after N turns
            history_obj = self._conv.get(channel.id)
            if (
                isinstance(channel, discord.TextChannel)
                and len(history_obj.turns) >= _AUTO_THREAD_TURN_THRESHOLD
                and not isinstance(channel, discord.Thread)
            ):
                view = _AutoThreadView(message, text[:50])
                await channel.send(
                    "💬 This conversation is getting long — want me to continue in a thread?",
                    view=view,
                    delete_after=30,
                )

        except Exception as exc:
            logger.exception("ConversationCog: failed responding to mention")
            await placeholder.edit(content=f"❌ Something went wrong: {exc}")
            await message.add_reaction("❌")

    # ------------------------------------------------------------------
    # /clear
    # ------------------------------------------------------------------
    @app_commands.command(name="clear", description="Clear my conversation memory for this channel.")
    async def clear(self, interaction: discord.Interaction) -> None:
        self._conv.clear(interaction.channel_id or 0)
        await interaction.response.send_message("🧹 Conversation history cleared!", ephemeral=True)

    # ------------------------------------------------------------------
    # /poll
    # ------------------------------------------------------------------
    @app_commands.command(name="poll", description="Create a quick poll.")
    @app_commands.describe(question="Poll question", options="Comma-separated options", duration_hours="Duration (1–168h, default 24)")
    async def poll(self, interaction: discord.Interaction, question: str, options: str,
                   duration_hours: app_commands.Range[int, 1, 168] = 24) -> None:
        choices = [o.strip() for o in options.split(",") if o.strip()][:10]
        if len(choices) < 2:
            await interaction.response.send_message("Provide at least 2 comma-separated options.", ephemeral=True)
            return
        import datetime as dt
        poll_obj = discord.Poll(question=question, duration=dt.timedelta(hours=duration_hours))
        for c in choices:
            poll_obj.add_answer(text=c)
        await interaction.response.send_message(poll=poll_obj)

    # ------------------------------------------------------------------
    # /pin
    # ------------------------------------------------------------------
    @app_commands.command(name="pin", description="Pin a message by ID.")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def pin(self, interaction: discord.Interaction, message_id: str) -> None:
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message("Text channels only.", ephemeral=True)
            return
        try:
            msg = await interaction.channel.fetch_message(int(message_id))
            await msg.pin()
            await interaction.response.send_message("📌 Pinned!", ephemeral=True)
        except discord.NotFound:
            await interaction.response.send_message("Message not found.", ephemeral=True)

    # ------------------------------------------------------------------
    # /thread
    # ------------------------------------------------------------------
    @app_commands.command(name="thread", description="Create a thread from a message.")
    @app_commands.checks.has_permissions(manage_threads=True)
    async def thread(self, interaction: discord.Interaction, message_id: str, name: str) -> None:
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message("Text channels only.", ephemeral=True)
            return
        try:
            msg = await interaction.channel.fetch_message(int(message_id))
            t = await msg.create_thread(name=name[:100])
            await interaction.response.send_message(f"🧵 Thread created: {t.mention}", ephemeral=True)
        except discord.NotFound:
            await interaction.response.send_message("Message not found.", ephemeral=True)

    # ------------------------------------------------------------------
    # /react
    # ------------------------------------------------------------------
    @app_commands.command(name="react", description="Add a reaction to a message.")
    async def react(self, interaction: discord.Interaction, message_id: str, emoji: str) -> None:
        if not isinstance(interaction.channel, (discord.TextChannel, discord.Thread)):
            await interaction.response.send_message("Text channels only.", ephemeral=True)
            return
        try:
            msg = await interaction.channel.fetch_message(int(message_id))
            await msg.add_reaction(emoji)
            await interaction.response.send_message(f"Reacted {emoji}!", ephemeral=True)
        except (discord.NotFound, discord.HTTPException) as e:
            await interaction.response.send_message(f"Failed: {e}", ephemeral=True)

    # ------------------------------------------------------------------
    # /model — model picker (adapted from openclaw's model-picker.ts)
    # ------------------------------------------------------------------
    @app_commands.command(name="model", description="Pick which AI model I use in this server.")
    @app_commands.describe(task="Task type (support, moderation_review, investigation, or * for all)")
    async def model_picker(self, interaction: discord.Interaction, task: str = "support") -> None:
        if not interaction.guild:
            return
        from bot.services.model_routing_service import ModelRoutingService
        from bot.services.permission_service import has_owner_or_founder_role
        if not isinstance(interaction.user, discord.Member) or not has_owner_or_founder_role(interaction.user):
            await interaction.response.send_message("❌ Requires Owner or Founder role.", ephemeral=True)
            return

        routing: ModelRoutingService = self.bot.model_routing_service  # type: ignore[attr-defined]
        statuses = await routing.list_status()
        healthy = [s for s in statuses if s.health is None or s.health.is_healthy]

        if not healthy:
            await interaction.response.send_message("⚠️ No healthy models available right now.", ephemeral=True)
            return

        view = _ModelPickerView(
            guild_id=interaction.guild.id,
            task=task,
            statuses=healthy,
            routing_svc=routing,
            requester_id=interaction.user.id,
        )
        await interaction.response.send_message(
            f"**Pick a model for task `{task}`:**",
            view=view,
            ephemeral=True,
        )


# ---------------------------------------------------------------------------
# Auto-thread view (button to move conversation to a thread)
# ---------------------------------------------------------------------------
class _AutoThreadView(discord.ui.View):
    def __init__(self, trigger_message: discord.Message, thread_name_hint: str) -> None:
        super().__init__(timeout=30)
        self._message = trigger_message
        self._name = f"Conversation: {thread_name_hint}"

    @discord.ui.button(label="Yes, create a thread", style=discord.ButtonStyle.primary, emoji="🧵")
    async def create(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not isinstance(self._message.channel, discord.TextChannel):
            await interaction.response.send_message("Can't create a thread here.", ephemeral=True)
            return
        try:
            t = await self._message.create_thread(name=self._name[:100])
            await interaction.response.send_message(f"Thread created: {t.mention}", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Failed: {e}", ephemeral=True)
        self.stop()

    @discord.ui.button(label="No thanks", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer()
        self.stop()


# ---------------------------------------------------------------------------
# Model picker view (buttons per model — openclaw model-picker.ts pattern)
# ---------------------------------------------------------------------------
class _ModelPickerView(discord.ui.View):
    def __init__(self, *, guild_id: int, task: str, statuses, routing_svc, requester_id: int) -> None:
        super().__init__(timeout=60)
        self._guild_id = guild_id
        self._task = task
        self._routing = routing_svc
        self._requester_id = requester_id

        for s in statuses[:5]:  # Discord limits to 25 buttons per view; show top 5
            label = f"{s.config.provider}/{s.config.model_name.split('/')[-1]}"[:40]
            btn = discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.primary,
                custom_id=str(s.config.id),
            )
            btn.callback = self._make_callback(s.config.id, label)
            self.add_item(btn)

        auto_btn = discord.ui.Button(label="🔄 Auto (reset)", style=discord.ButtonStyle.secondary)
        auto_btn.callback = self._auto_callback
        self.add_item(auto_btn)

    def _make_callback(self, config_id: int, label: str):
        async def callback(interaction: discord.Interaction) -> None:
            if interaction.user.id != self._requester_id:
                await interaction.response.send_message("Not your picker!", ephemeral=True)
                return
            await self._routing.set_override(
                guild_id=self._guild_id, task_type=self._task,
                model_config_id=config_id, set_by=interaction.user.id,
            )
            await interaction.response.edit_message(
                content=f"✅ **{label}** is now pinned for task `{self._task}`.", view=None
            )
            self.stop()
        return callback

    async def _auto_callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self._requester_id:
            await interaction.response.send_message("Not your picker!", ephemeral=True)
            return
        await self._routing.clear_override(guild_id=self._guild_id, task_type=self._task)
        await interaction.response.edit_message(
            content=f"✅ Task `{self._task}` is back to automatic routing.", view=None
        )
        self.stop()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ConversationCog(bot))
