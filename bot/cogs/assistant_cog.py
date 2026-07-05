from __future__ import annotations

import logging
import re

import discord
from discord.ext import commands

from bot.services.assistant_tools_service import AssistantToolsService
from bot.services.investigation_service import InvestigationService
from bot.services.model_routing_service import ModelRoutingService

logger = logging.getLogger(__name__)

_MEMBER_RE = re.compile(r"<@!?(\d+)>")


def _parse_mention(text: str) -> tuple[int | None, str]:
    """Split a possible leading @mention from the rest of the text."""
    m = _MEMBER_RE.match(text.strip())
    if m:
        uid = int(m.group(1))
        rest = text[m.end():].strip()
        return uid, rest
    return None, text.strip()


class AssistantCog(commands.Cog, name="Assistant"):
    """Prefix-command assistant tools: !ask, !translate, !summarize, !explain, !investigate, !draft."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @property
    def _tools(self) -> AssistantToolsService:
        return self.bot.assistant_tools  # type: ignore[attr-defined]

    @property
    def _investigation(self) -> InvestigationService:
        return self.bot.investigation_service  # type: ignore[attr-defined]

    @property
    def _routing_svc(self) -> ModelRoutingService:
        return self.bot.model_routing_service  # type: ignore[attr-defined]

    async def _check_ai_enabled(self, ctx: commands.Context) -> bool:
        if ctx.guild and not await self._routing_svc.is_ai_enabled(ctx.guild.id):
            await ctx.reply("⚠️ The AI assistant is currently disabled for this server.", mention_author=False)
            return False
        return True

    def _build_embed(self, title: str, decision) -> discord.Embed:
        embed = discord.Embed(description=decision.text[:4096], color=discord.Color.blurple())
        embed.set_footer(
            text=f"{title} · {decision.provider}/{decision.model} · confidence {decision.confidence:.0%}"
        )
        if decision.escalate:
            embed.add_field(name="⚠️ Low confidence", value="This response may need staff review.", inline=False)
        return embed

    # ------------------------------------------------------------------
    # !ask
    # ------------------------------------------------------------------
    @commands.command(name="ask", help="!ask <question>  or  !ask @user <question>")
    async def ask(self, ctx: commands.Context, *, text: str) -> None:
        if not await self._check_ai_enabled(ctx):
            return
        if ctx.guild is None or not isinstance(ctx.author, discord.Member):
            await ctx.reply("This command only works in a server.", mention_author=False)
            return

        uid, question = _parse_mention(text)
        target_member: discord.Member | None = None
        if uid is not None:
            target_member = ctx.guild.get_member(uid)

        async with ctx.typing():
            conversation = self.bot.conversation  # type: ignore[attr-defined]
            history = conversation.history_messages(ctx.channel.id)
            decision = await self._tools.handle_ask(ctx.message, question, target_member=target_member, history=history)
            conversation.record(
                channel_id=ctx.channel.id,
                user_content=question,
                assistant_content=decision.text,
                user_id=ctx.author.id,
                user_name=ctx.author.display_name,
            )

        await ctx.reply(embed=self._build_embed("!ask", decision), mention_author=False)

    # ------------------------------------------------------------------
    # !translate
    # ------------------------------------------------------------------
    @commands.command(name="translate", help="!translate [to <language>]  (reply to a message to translate it)")
    async def translate(self, ctx: commands.Context, *, target_lang: str = "English") -> None:
        if not await self._check_ai_enabled(ctx):
            return
        if ctx.message.reference is None and not ctx.message.content.strip().removeprefix("!translate").strip():
            await ctx.reply("Reply to a message to translate it.", mention_author=False)
            return
        async with ctx.typing():
            decision = await self._tools.handle_translate(ctx.message, target_lang=target_lang)
        await ctx.reply(embed=self._build_embed("!translate", decision), mention_author=False)

    # ------------------------------------------------------------------
    # !summarize
    # ------------------------------------------------------------------
    @commands.command(name="summarize", aliases=["summarise"], help="!summarize [N]  (default: 20 messages)")
    async def summarize(self, ctx: commands.Context, last_n: int = 20) -> None:
        if not await self._check_ai_enabled(ctx):
            return
        last_n = max(5, min(last_n, 50))
        async with ctx.typing():
            decision = await self._tools.handle_summarize(ctx.message, last_n=last_n)
        await ctx.reply(embed=self._build_embed("!summarize", decision), mention_author=False)

    # ------------------------------------------------------------------
    # !explain
    # ------------------------------------------------------------------
    @commands.command(name="explain", help="!explain  (explains the current conversation or a replied-to message)")
    async def explain(self, ctx: commands.Context) -> None:
        if not await self._check_ai_enabled(ctx):
            return
        async with ctx.typing():
            decision = await self._tools.handle_explain(ctx.message)
        await ctx.reply(embed=self._build_embed("!explain", decision), mention_author=False)

    # ------------------------------------------------------------------
    # !investigate
    # ------------------------------------------------------------------
    @commands.command(name="investigate", help="!investigate @user [reason]  or  !investigate <question>")
    async def investigate(self, ctx: commands.Context, *, text: str = "") -> None:
        if not await self._check_ai_enabled(ctx):
            return
        if ctx.guild is None or not isinstance(ctx.author, discord.Member):
            await ctx.reply("This command only works in a server.", mention_author=False)
            return

        uid, question = _parse_mention(text)
        if not question:
            question = "Investigate this user's recent activity and any known issues."

        from bot.ai.intent_service import Intent
        intent = Intent.INVESTIGATION

        async with ctx.typing():
            decision = await self._investigation.investigate(
                guild=ctx.guild,
                requester=ctx.author,
                target_user_id=uid,
                intent=intent,
                question=question,
            )
        await ctx.reply(embed=self._build_embed("!investigate", decision), mention_author=False)

    # ------------------------------------------------------------------
    # !draft
    # ------------------------------------------------------------------
    @commands.command(name="draft", help="!draft <context>  (drafts a response, optionally to a replied-to message)")
    async def draft(self, ctx: commands.Context, *, context: str = "") -> None:
        if not await self._check_ai_enabled(ctx):
            return
        if ctx.guild is None:
            await ctx.reply("This command only works in a server.", mention_author=False)
            return
        async with ctx.typing():
            decision = await self._tools.handle_draft(ctx.message, context)
        await ctx.reply(embed=self._build_embed("!draft", decision), mention_author=False)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AssistantCog(bot))
