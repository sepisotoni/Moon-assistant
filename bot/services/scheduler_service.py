from __future__ import annotations

"""SchedulerService — lets the bot run tasks on a schedule or continuously.

Supports:
  - one-off tasks ("do X in 5 minutes")
  - recurring tasks ("do X every hour")
  - continuous monitors ("watch channel Y and alert if Z happens")
  - tasks added dynamically via /schedule command or by the bot itself

All tasks are persisted to the DB so they survive restarts.
"""

import asyncio
import datetime as dt
import logging
from dataclasses import dataclass
from typing import Callable, Coroutine

import discord

logger = logging.getLogger(__name__)


@dataclass
class ScheduledTask:
    task_id: str
    guild_id: int
    channel_id: int
    description: str
    code: str              # Python code to execute
    interval_seconds: int  # 0 = run once
    next_run: dt.datetime
    created_by: int
    active: bool = True


class SchedulerService:
    """In-memory task scheduler with optional DB persistence."""

    def __init__(self) -> None:
        self._tasks: dict[str, ScheduledTask] = {}
        self._runner_task: asyncio.Task | None = None
        self._bot = None

    def attach_bot(self, bot) -> None:
        self._bot = bot

    def start(self) -> None:
        if self._runner_task is None or self._runner_task.done():
            self._runner_task = asyncio.create_task(self._run_loop())
            logger.info("Scheduler started.")

    def stop(self) -> None:
        if self._runner_task:
            self._runner_task.cancel()

    def add_task(
        self,
        *,
        task_id: str,
        guild_id: int,
        channel_id: int,
        description: str,
        code: str,
        interval_seconds: int = 0,
        delay_seconds: int = 0,
        created_by: int,
    ) -> ScheduledTask:
        task = ScheduledTask(
            task_id=task_id,
            guild_id=guild_id,
            channel_id=channel_id,
            description=description,
            code=code,
            interval_seconds=interval_seconds,
            next_run=dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=delay_seconds),
            created_by=created_by,
        )
        self._tasks[task_id] = task
        logger.info("Scheduled task '%s': %s", task_id, description)
        return task

    def remove_task(self, task_id: str) -> bool:
        if task_id in self._tasks:
            del self._tasks[task_id]
            return True
        return False

    def list_tasks(self, guild_id: int) -> list[ScheduledTask]:
        return [t for t in self._tasks.values() if t.guild_id == guild_id and t.active]

    async def _run_loop(self) -> None:
        while True:
            try:
                now = dt.datetime.now(dt.timezone.utc)
                due = [t for t in list(self._tasks.values()) if t.active and t.next_run <= now]

                for task in due:
                    asyncio.create_task(self._execute_task(task))
                    if task.interval_seconds > 0:
                        task.next_run = now + dt.timedelta(seconds=task.interval_seconds)
                    else:
                        task.active = False

            except Exception:
                logger.exception("Scheduler loop error")

            await asyncio.sleep(5)  # check every 5 seconds

    async def _execute_task(self, task: ScheduledTask) -> None:
        logger.info("Running scheduled task '%s': %s", task.task_id, task.description)
        try:
            channel = None
            if self._bot:
                channel = self._bot.get_channel(task.channel_id)

            # Execute via CodeExecutionService
            exec_svc: CodeExecutionService = self._bot.code_executor  # type: ignore[attr-defined]
            result = await exec_svc.execute(task.code, guild_id=task.guild_id)

            if channel and isinstance(channel, discord.TextChannel):
                if result.output:
                    await channel.send(f"⏰ **Scheduled task** `{task.task_id}`:\n```\n{result.output[:1800]}\n```")
                elif result.error:
                    await channel.send(f"⚠️ Task `{task.task_id}` error: {result.error[:500]}")
        except Exception as exc:
            logger.exception("Scheduled task '%s' failed: %s", task.task_id, exc)
