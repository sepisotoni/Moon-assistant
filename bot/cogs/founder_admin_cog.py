from __future__ import annotations

import logging

import discord
from discord.ext import commands

from bot.services.logging_service import format_ts
from bot.services.model_routing_service import ModelRoutingService

logger = logging.getLogger(__name__)


def _is_elevated(member: discord.Member | discord.User) -> bool:
    from bot.services.permission_service import has_owner_or_founder_role
    if isinstance(member, discord.User):
        return False
    return has_owner_or_founder_role(member)


def _require_elevated():
    async def predicate(ctx: commands.Context) -> bool:
        if not isinstance(ctx.author, discord.Member) or not _is_elevated(ctx.author):
            await ctx.reply("❌ This command requires the Owner or Founder role.", mention_author=False)
            return False
        return True
    return commands.check(predicate)


class FounderAdminCog(commands.Cog, name="FounderAdmin"):
    """Owner/Founder-only prefix admin commands (!ai-status, !ai-rules, !ai-memory, etc.)"""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @property
    def _routing_svc(self) -> ModelRoutingService:
        return self.bot.model_routing_service  # type: ignore[attr-defined]

    # ---- !ai-status -------------------------------------------------------
    @commands.command(name="ai-status")
    @_require_elevated()
    async def ai_status(self, ctx: commands.Context) -> None:
        """Show overall AI system status: providers, model health, enabled/disabled."""
        if ctx.guild is None:
            return
        enabled = await self._routing_svc.is_ai_enabled(ctx.guild.id)
        statuses = await self._routing_svc.list_status()
        lines = [f"**AI system**: {'✅ enabled' if enabled else '🚫 disabled'}",
                 f"**Dual review**: {'on' if self.bot.settings.dual_review_enabled else 'off'}",  # type: ignore[attr-defined]
                 ""]
        for ms in statuses[:15]:
            enabled_flag = "✅" if ms.config.is_enabled else "⬜"
            lines.append(f"{enabled_flag} `{ms.config.id}` **{ms.config.provider}/** {ms.config.model_name} — {ms.display}")
        await ctx.reply("\n".join(lines) or "No models configured.", mention_author=False)

    # ---- !ai-rules --------------------------------------------------------
    @commands.command(name="ai-rules")
    @_require_elevated()
    async def ai_rules(self, ctx: commands.Context, action: str = "list", *, args: str = "") -> None:
        """Manage AI constitution rules. Usage: !ai-rules list | add <title>|<rule_text> | disable <id>"""
        if ctx.guild is None:
            return
        constitution = self.bot.constitution_service  # type: ignore[attr-defined]
        if action == "list":
            rules = await constitution.list_rules(ctx.guild.id)
            if not rules:
                await ctx.reply("No constitution rules configured.", mention_author=False)
                return
            lines = [f"`{r.id}` [T{r.tier.value}] **{r.title}** {'✅' if r.is_enabled else '⬜'}: {r.rule_text[:80]}..." for r in rules]
            await ctx.reply("\n".join(lines), mention_author=False)
        elif action == "add":
            if "|" not in args:
                await ctx.reply("Usage: `!ai-rules add Title|Rule text here`", mention_author=False)
                return
            title, _, rule_text = args.partition("|")
            await constitution.add_server_rule(guild_id=ctx.guild.id, title=title.strip(), rule_text=rule_text.strip(), created_by=ctx.author.id)
            await ctx.reply(f"✅ Added rule: **{title.strip()}**", mention_author=False)
        elif action in ("disable", "enable"):
            try:
                rule_id = int(args.strip())
            except ValueError:
                await ctx.reply("Provide a numeric rule ID.", mention_author=False)
                return
            rule = await constitution.set_enabled(rule_id, is_enabled=(action == "enable"))
            if rule is None:
                await ctx.reply("Rule not found.", mention_author=False)
            else:
                await ctx.reply(f"Rule `{rule_id}` {'enabled' if action == 'enable' else 'disabled'}.", mention_author=False)
        else:
            await ctx.reply("Usage: `!ai-rules list | add Title|Text | enable <id> | disable <id>`", mention_author=False)

    # ---- !ai-memory -------------------------------------------------------
    @commands.command(name="ai-memory")
    @_require_elevated()
    async def ai_memory(self, ctx: commands.Context, action: str = "list", *, args: str = "") -> None:
        """Inspect / manage server memory. Usage: !ai-memory list | set <key>|<value> | purge"""
        if ctx.guild is None:
            return
        memory = self.bot.memory_service  # type: ignore[attr-defined]
        if action == "list":
            facts = await memory.list_server_facts(ctx.guild.id)
            top_recurring = await memory.top_recurring(ctx.guild.id, limit=5)
            lines = ["**Server facts:**"]
            lines += [f"  `{f.key}`: {f.value[:80]}" for f in facts] or ["  (none)"]
            lines += ["", "**Top recurring topics:**"]
            lines += [f"  `{r.key}` (seen {r.hit_count}×): {r.value[:80]}" for r in top_recurring] or ["  (none)"]
            await ctx.reply("\n".join(lines), mention_author=False)
        elif action == "set":
            if "|" not in args:
                await ctx.reply("Usage: `!ai-memory set <key>|<value>`", mention_author=False)
                return
            key, _, value = args.partition("|")
            await memory.set_server_fact(guild_id=ctx.guild.id, fact_key=key.strip(), value=value.strip())
            await ctx.reply(f"✅ Set server fact `{key.strip()}`.", mention_author=False)
        elif action == "purge":
            removed = await memory.purge_expired()
            await ctx.reply(f"Purged {removed} expired memory entries.", mention_author=False)
        else:
            await ctx.reply("Usage: `!ai-memory list | set <key>|<value> | purge`", mention_author=False)

    # ---- !ai-knowledge ----------------------------------------------------
    @commands.command(name="ai-knowledge")
    @_require_elevated()
    async def ai_knowledge(self, ctx: commands.Context, action: str = "list", *, args: str = "") -> None:
        """Manage knowledge corrections. Usage: !ai-knowledge list | approve <id> | reject <id>"""
        if ctx.guild is None:
            return
        learning = self.bot.knowledge_learning_service  # type: ignore[attr-defined]
        if action == "list":
            pending = await learning.list_pending(ctx.guild.id)
            if not pending:
                await ctx.reply("No pending knowledge corrections.", mention_author=False)
                return
            lines = [f"`{e.id}` by {e.author_name}: {e.content[:100]}..." for e in pending]
            await ctx.reply("**Pending corrections:**\n" + "\n".join(lines), mention_author=False)
        elif action in ("approve", "reject"):
            try:
                entry_id = int(args.strip())
            except ValueError:
                await ctx.reply("Provide a numeric entry ID.", mention_author=False)
                return
            if action == "approve":
                entry = await learning.approve(entry_id, reviewed_by=ctx.author.id)
                msg = f"✅ Approved correction #{entry_id}." if entry else "Entry not found."
            else:
                entry = await learning.reject(entry_id, reviewed_by=ctx.author.id)
                msg = f"❌ Rejected correction #{entry_id}." if entry else "Entry not found."
            await ctx.reply(msg, mention_author=False)
        else:
            await ctx.reply("Usage: `!ai-knowledge list | approve <id> | reject <id>`", mention_author=False)

    # ---- !ai-investigations -----------------------------------------------
    @commands.command(name="ai-investigations")
    @_require_elevated()
    async def ai_investigations(self, ctx: commands.Context) -> None:
        """List the 10 most recent investigation records for this server."""
        if ctx.guild is None:
            return
        inv_repo = self.bot.investigation_repo  # type: ignore[attr-defined]
        items = await inv_repo.recent(ctx.guild.id, limit=10)
        if not items:
            await ctx.reply("No investigations on record.", mention_author=False)
            return
        lines = [
            f"`{i.id}` {format_ts(i.created_at)} | intent={i.intent} | conf={i.confidence:.0%} | {i.question[:60]}"
            for i in items
        ]
        await ctx.reply("**Recent investigations:**\n" + "\n".join(lines), mention_author=False)

    # ---- !ai-reload -------------------------------------------------------
    @commands.command(name="ai-reload")
    @_require_elevated()
    async def ai_reload(self, ctx: commands.Context) -> None:
        """Clear all cached AI constitution prompts (they rebuild on next use)."""
        self.bot.constitution_service.invalidate_cache()  # type: ignore[attr-defined]
        await ctx.reply("✅ AI constitution cache cleared; rules will reload on next request.", mention_author=False)

    # ---- !ai-enable / !ai-disable ----------------------------------------
    @commands.command(name="ai-enable")
    @_require_elevated()
    async def ai_enable(self, ctx: commands.Context) -> None:
        """Re-enable the AI assistant for this server."""
        if ctx.guild is None:
            return
        await self._routing_svc.set_ai_enabled(ctx.guild.id, True, updated_by=ctx.author.id)
        await ctx.reply("✅ AI assistant enabled.", mention_author=False)

    @commands.command(name="ai-disable")
    @_require_elevated()
    async def ai_disable(self, ctx: commands.Context) -> None:
        """Disable the AI assistant for this server (all AI commands will refuse gracefully)."""
        if ctx.guild is None:
            return
        await self._routing_svc.set_ai_enabled(ctx.guild.id, False, updated_by=ctx.author.id)
        await ctx.reply("🚫 AI assistant disabled.", mention_author=False)

    # ---- !ai-debug --------------------------------------------------------
    @commands.command(name="ai-debug")
    @_require_elevated()
    async def ai_debug(self, ctx: commands.Context) -> None:
        """Dump recent AI decision log entries for debugging."""
        if ctx.guild is None:
            return
        decision_repo = self.bot.decision_log_repo  # type: ignore[attr-defined]
        records = await decision_repo.recent(ctx.guild.id, limit=5)
        if not records:
            await ctx.reply("No AI decisions logged yet.", mention_author=False)
            return
        lines = []
        for r in records:
            lines.append(
                f"`{r.id}` {format_ts(r.created_at)} task=**{r.task_type}** conf={r.confidence:.0%} "
                f"escalated={'yes' if r.escalated else 'no'} provider={r.primary_provider}/{r.primary_model}"
            )
        await ctx.reply("**Recent AI decisions:**\n" + "\n".join(lines), mention_author=False)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(FounderAdminCog(bot))
