from __future__ import annotations

import logging

import discord
from discord.ext import commands, tasks

logger = logging.getLogger(__name__)

# How often (minutes) to purge expired short-term memory entries and log model health.
_MEMORY_PURGE_INTERVAL_MINUTES = 15
_HEALTH_LOG_INTERVAL_MINUTES = 60


class BackgroundTasksCog(commands.Cog, name="BackgroundTasks"):
    """Long-running periodic background tasks (memory purge, health reporting).

    These are registered with discord.py's task loop system so they start when the
    bot connects and stop cleanly when the bot closes.
    """

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._memory_purge.start()
        self._health_report.start()

    def cog_unload(self) -> None:
        self._memory_purge.cancel()
        self._health_report.cancel()

    @tasks.loop(minutes=_MEMORY_PURGE_INTERVAL_MINUTES)
    async def _memory_purge(self) -> None:
        """Remove expired short-term memory entries so the table doesn't grow unboundedly."""
        try:
            memory_svc = self.bot.memory_service  # type: ignore[attr-defined]
            removed = await memory_svc.purge_expired()
            if removed:
                logger.debug("Memory purge: removed %d expired entries.", removed)
        except Exception:
            logger.exception("Background memory purge failed.")

    @_memory_purge.before_loop
    async def _before_memory_purge(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=_HEALTH_LOG_INTERVAL_MINUTES)
    async def _health_report(self) -> None:
        """Log a brief model health summary so operators can spot degraded providers."""
        try:
            routing_svc = self.bot.model_routing_service  # type: ignore[attr-defined]
            statuses = await routing_svc.list_status()

            # Only flag models that have *actually failed* — health=None means never called yet.
            unhealthy = [s for s in statuses if s.health is not None and not s.health.is_healthy]
            never_called = [s for s in statuses if s.health is None]

            if unhealthy:
                names = ", ".join(f"{s.config.provider}/{s.config.model_name}" for s in unhealthy)
                logger.warning(
                    "Model health report: %d model(s) currently unhealthy: %s",
                    len(unhealthy), names,
                )
                db_log = self.bot.db_log_service  # type: ignore[attr-defined]
                await db_log.log(
                    level="WARNING",
                    source="background.health_report",
                    message=f"{len(unhealthy)} model(s) unhealthy: {names}",
                )
            else:
                called = [s for s in statuses if s.health is not None]
                if called:
                    logger.debug(
                        "Model health report: all %d called model(s) healthy (%d never called).",
                        len(called), len(never_called),
                    )
        except Exception:
            logger.exception("Background health report failed.")

    @_health_report.before_loop
    async def _before_health_report(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BackgroundTasksCog(bot))
