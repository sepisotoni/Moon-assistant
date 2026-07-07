from __future__ import annotations

"""ControlPanelCog — the bot's own live console and scheduler.

Owner/Founder-only commands:
  /run <code>         — execute Python code immediately
  /do <request>       — AI writes and runs code to fulfil a natural language request
  /schedule           — schedule a task (one-off or recurring)
  /tasks              — list scheduled tasks
  /cancel <task_id>   — cancel a scheduled task
  /scan ip            — scan visible channels for the server IP
  /scan store         — scan visible channels for store links
  /identity           — view/update the bot's identity and known facts
"""

import logging
import uuid

import discord
from discord import app_commands
from discord.ext import commands

from bot.services.permission_service import has_owner_or_founder_role

logger = logging.getLogger(__name__)


def _elevated():
    async def predicate(interaction: discord.Interaction) -> bool:
        if not isinstance(interaction.user, discord.Member) or not has_owner_or_founder_role(interaction.user):
            await interaction.response.send_message("❌ Owner or Founder role required.", ephemeral=True)
            return False
        return True
    return app_commands.check(predicate)


class ControlPanelCog(commands.Cog, name="ControlPanel"):

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @property
    def _exec(self):
        return self.bot.code_executor  # type: ignore[attr-defined]

    @property
    def _scheduler(self):
        return self.bot.scheduler  # type: ignore[attr-defined]

    @property
    def _identity(self):
        return self.bot.identity_service  # type: ignore[attr-defined]

    @property
    def _orch(self):
        return self.bot.orchestrator  # type: ignore[attr-defined]

    # ------------------------------------------------------------------
    # /run — execute Python directly
    # ------------------------------------------------------------------
    @app_commands.command(name="run", description="[Owner] Execute Python code immediately.")
    @app_commands.describe(code="Python code to run (use print() for output)")
    @_elevated()
    async def run(self, interaction: discord.Interaction, code: str) -> None:
        await interaction.response.defer(thinking=True, ephemeral=True)
        result = await self._exec.execute(code, guild_id=interaction.guild_id or 0)
        icon = "✅" if result.success else "❌"
        output = result.output or result.error or "(no output)"
        embed = discord.Embed(
            title=f"{icon} Code executed ({result.duration_ms}ms)",
            color=discord.Color.green() if result.success else discord.Color.red(),
        )
        embed.add_field(name="Code", value=f"```python\n{code[:500]}\n```", inline=False)
        embed.add_field(name="Output", value=f"```\n{output[:1000]}\n```", inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------
    # /do — AI generates and runs code for a natural language request
    # ------------------------------------------------------------------
    @app_commands.command(name="do", description="[Owner] Ask the bot to do something — it writes and runs the code.")
    @app_commands.describe(request="What do you want the bot to do? (e.g. 'count messages today', 'list active users')")
    @_elevated()
    async def do(self, interaction: discord.Interaction, request: str) -> None:
        await interaction.response.defer(thinking=True)
        result = await self._exec.ai_generate_and_run(
            request=request,
            guild_id=interaction.guild_id or 0,
            orchestrator=self._orch,
        )
        icon = "✅" if result.success else "❌"
        output = result.output if result.success else result.error
        embed = discord.Embed(
            title=f"{icon} Task: {request[:80]}",
            color=discord.Color.green() if result.success else discord.Color.red(),
        )
        if result.code:
            embed.add_field(name="Generated code", value=f"```python\n{result.code[:800]}\n```", inline=False)
        embed.add_field(name="Result", value=f"```\n{output[:1200]}\n```" if output else "(no output)", inline=False)
        embed.set_footer(text=f"{result.duration_ms}ms")
        await interaction.followup.send(embed=embed)

    # ------------------------------------------------------------------
    # /schedule — add a scheduled task
    # ------------------------------------------------------------------
    @app_commands.command(name="schedule", description="[Owner] Schedule a recurring or one-off task.")
    @app_commands.describe(
        description="What the task does",
        code="Python code to run (use print() for output)",
        interval_minutes="Run every N minutes (0 = run once)",
        delay_minutes="Wait N minutes before first run (default: 0)",
    )
    @_elevated()
    async def schedule(
        self,
        interaction: discord.Interaction,
        description: str,
        code: str,
        interval_minutes: int = 0,
        delay_minutes: int = 0,
    ) -> None:
        if interaction.guild is None:
            return
        task_id = str(uuid.uuid4())[:8]
        self._scheduler.add_task(
            task_id=task_id,
            guild_id=interaction.guild.id,
            channel_id=interaction.channel_id or interaction.guild.text_channels[0].id,
            description=description,
            code=code,
            interval_seconds=interval_minutes * 60,
            delay_seconds=delay_minutes * 60,
            created_by=interaction.user.id,
        )
        freq = f"every {interval_minutes} min" if interval_minutes > 0 else "once"
        start = f"in {delay_minutes} min" if delay_minutes > 0 else "now"
        await interaction.response.send_message(
            f"⏰ Scheduled task `{task_id}` — **{description}** ({freq}, starting {start}).",
            ephemeral=True,
        )

    # ------------------------------------------------------------------
    # /schedule-ai — AI writes the code for a scheduled task
    # ------------------------------------------------------------------
    @app_commands.command(name="schedule-ai", description="[Owner] Tell the bot what to schedule — it writes the code.")
    @app_commands.describe(
        description="What you want it to do (e.g. 'post daily activity stats every morning')",
        interval_minutes="How often to run in minutes (0 = once)",
        delay_minutes="Delay before first run in minutes",
    )
    @_elevated()
    async def schedule_ai(
        self,
        interaction: discord.Interaction,
        description: str,
        interval_minutes: int = 60,
        delay_minutes: int = 0,
    ) -> None:
        if interaction.guild is None:
            return
        await interaction.response.defer(thinking=True, ephemeral=True)

        from bot.ai.base import AIMessage
        system = (
            "Write Python code for a scheduled Discord bot task. Globals: bot, guild, discord, asyncio, dt, print(). "
            "The code should be self-contained and use print() to report what it did. No imports needed."
        )
        messages = [AIMessage(role="system", content=system), AIMessage(role="user", content=description)]
        decision = await self._orch.generate_for_task("support", messages, guild_id=interaction.guild.id, dual_review=False)
        code = self._exec._strip_fences(decision.text)

        task_id = str(uuid.uuid4())[:8]
        self._scheduler.add_task(
            task_id=task_id,
            guild_id=interaction.guild.id,
            channel_id=interaction.channel_id or interaction.guild.text_channels[0].id,
            description=description,
            code=code,
            interval_seconds=interval_minutes * 60,
            delay_seconds=delay_minutes * 60,
            created_by=interaction.user.id,
        )
        embed = discord.Embed(title=f"⏰ Scheduled: {description[:60]}", color=discord.Color.blurple())
        embed.add_field(name="Task ID", value=f"`{task_id}`")
        embed.add_field(name="Interval", value=f"every {interval_minutes} min" if interval_minutes else "once")
        embed.add_field(name="Code", value=f"```python\n{code[:600]}\n```", inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------
    # /tasks — list scheduled tasks
    # ------------------------------------------------------------------
    @app_commands.command(name="tasks", description="[Owner] List all scheduled tasks for this server.")
    @_elevated()
    async def tasks(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        task_list = self._scheduler.list_tasks(interaction.guild.id)
        if not task_list:
            await interaction.response.send_message("No scheduled tasks.", ephemeral=True)
            return
        from bot.services.logging_service import format_ts
        lines = []
        for t in task_list:
            freq = f"every {t.interval_seconds//60}min" if t.interval_seconds > 0 else "once"
            lines.append(f"`{t.task_id}` **{t.description}** ({freq}) — next: {format_ts(t.next_run)}")
        embed = discord.Embed(title="⏰ Scheduled Tasks", description="\n".join(lines), color=discord.Color.blurple())
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------
    # /cancel — cancel a task
    # ------------------------------------------------------------------
    @app_commands.command(name="cancel", description="[Owner] Cancel a scheduled task.")
    @app_commands.describe(task_id="Task ID from /tasks")
    @_elevated()
    async def cancel(self, interaction: discord.Interaction, task_id: str) -> None:
        if self._scheduler.remove_task(task_id):
            await interaction.response.send_message(f"✅ Task `{task_id}` cancelled.", ephemeral=True)
        else:
            await interaction.response.send_message(f"No task `{task_id}` found.", ephemeral=True)

    # ------------------------------------------------------------------
    # /scan — scan channels for facts
    # ------------------------------------------------------------------
    scan_group = app_commands.Group(name="scan", description="[Owner] Scan channels for specific information")

    @scan_group.command(name="ip", description="Scan visible channels for the server IP or hostname.")
    @_elevated()
    async def scan_ip(self, interaction: discord.Interaction) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return
        await interaction.response.defer(thinking=True, ephemeral=True)
        ip = await self._identity.scan_for_ip(interaction.guild, interaction.user)
        if ip:
            await interaction.followup.send(f"✅ Found IP/hostname: `{ip}`\nI've saved this — I'll now answer IP questions from memory.", ephemeral=True)
        else:
            await interaction.followup.send("Couldn't find an IP or hostname in any accessible channel. Try posting it in a knowledge channel like `#ai-ip`.", ephemeral=True)

    @scan_group.command(name="store", description="Scan visible channels for a store link.")
    @_elevated()
    async def scan_store(self, interaction: discord.Interaction) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return
        await interaction.response.defer(thinking=True, ephemeral=True)
        link = await self._identity.scan_for_links(interaction.guild, interaction.user, "store")
        if link:
            await interaction.followup.send(f"✅ Found store link: {link}", ephemeral=True)
        else:
            await interaction.followup.send("No store link found in accessible channels.", ephemeral=True)

    # ------------------------------------------------------------------
    # /identity — view/update bot identity
    # ------------------------------------------------------------------
    @app_commands.command(name="identity", description="[Owner] View what the bot knows about this server.")
    @_elevated()
    async def identity(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            return
        ident = self._identity.get(interaction.guild)
        embed = discord.Embed(
            title=f"🤖 {ident.name} — Server Identity",
            description=f"**Server:** {ident.guild_name} · {ident.member_count} members",
            color=discord.Color.blurple(),
        )
        if ident.known_facts:
            facts = "\n".join(f"• **{k}**: {v}" for k, v in ident.known_facts.items())
            embed.add_field(name="Known Facts", value=facts, inline=False)
        else:
            embed.add_field(name="Known Facts", value="None yet — use `/scan ip` and `/scan store` to teach me, or post info in `#ai-ip`", inline=False)
        embed.set_footer(text="Use /scan to discover more facts automatically")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ControlPanelCog(bot))
