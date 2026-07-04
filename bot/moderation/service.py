from __future__ import annotations

import datetime as dt
import logging

import discord
from sqlalchemy import select

from bot.config import get_settings
from bot.database.models import ModerationAction, ModerationActionType
from bot.database.session import get_session
from bot.moderation.action_guard import ABSOLUTE_MAX_TIMEOUT_MINUTES, clamp_timeout_minutes

logger = logging.getLogger(__name__)
settings = get_settings()


class ModerationError(RuntimeError):
    """Raised for invalid moderation requests (e.g. timeout out of bounds)."""


class ModerationService:
    """Implements warnings, message deletion, and timeouts, with a full audit trail."""

    async def warn(
        self,
        *,
        guild: discord.Guild,
        member: discord.Member,
        moderator: discord.Member,
        reason: str,
        check_repeat_offender: bool = True,
    ) -> ModerationAction:
        async with get_session() as session:
            action = ModerationAction(
                guild_id=guild.id,
                user_id=member.id,
                moderator_id=moderator.id,
                action_type=ModerationActionType.WARN,
                reason=reason,
            )
            session.add(action)
            await session.flush()
            await session.refresh(action)

        # Repeat-offender check: fires after the warn is committed so the count is accurate.
        # Import is deferred to avoid a circular import (intelligence_service imports this module).
        if check_repeat_offender:
            try:
                from bot.moderation.intelligence_service import ModerationIntelligenceService  # noqa: PLC0415
                # We don't have an orchestrator reference here; the cog passes one when needed.
                # The check is done inside check_repeat_offender using only DB queries.
                await ModerationIntelligenceService._static_repeat_check(guild.id, member.id)
            except Exception:
                logger.exception("Repeat-offender check failed for user %s (non-fatal)", member.id)

        return action

    async def list_warnings(self, *, guild_id: int, user_id: int) -> list[ModerationAction]:
        stmt = (
            select(ModerationAction)
            .where(
                ModerationAction.guild_id == guild_id,
                ModerationAction.user_id == user_id,
                ModerationAction.action_type == ModerationActionType.WARN,
            )
            .order_by(ModerationAction.created_at.desc())
        )
        async with get_session() as session:
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def delete_message(
        self,
        *,
        message: discord.Message,
        moderator: discord.Member,
        reason: str | None = None,
    ) -> None:
        guild = message.guild
        if guild is None:
            raise ModerationError("Cannot delete a message outside of a guild.")
        author_id = message.author.id
        await message.delete()
        async with get_session() as session:
            session.add(
                ModerationAction(
                    guild_id=guild.id,
                    user_id=author_id,
                    moderator_id=moderator.id,
                    action_type=ModerationActionType.DELETE_MESSAGE,
                    reason=reason,
                )
            )

    async def timeout(
        self,
        *,
        member: discord.Member,
        moderator: discord.Member,
        minutes: int,
        reason: str | None = None,
    ) -> None:
        if minutes <= 0 or minutes > settings.max_timeout_minutes:
            raise ModerationError(
                f"Timeout must be between 1 and "
                f"{min(settings.max_timeout_minutes, ABSOLUTE_MAX_TIMEOUT_MINUTES)} minutes."
            )
        minutes = clamp_timeout_minutes(minutes, settings.max_timeout_minutes)
        until = discord.utils.utcnow() + dt.timedelta(minutes=minutes)
        await member.timeout(until, reason=reason)
        async with get_session() as session:
            session.add(
                ModerationAction(
                    guild_id=member.guild.id,
                    user_id=member.id,
                    moderator_id=moderator.id,
                    action_type=ModerationActionType.TIMEOUT,
                    reason=reason,
                    duration_minutes=minutes,
                )
            )

    async def remove_timeout(
        self,
        *,
        member: discord.Member,
        moderator: discord.Member,
        reason: str | None = None,
    ) -> None:
        await member.timeout(None, reason=reason)
        async with get_session() as session:
            session.add(
                ModerationAction(
                    guild_id=member.guild.id,
                    user_id=member.id,
                    moderator_id=moderator.id,
                    action_type=ModerationActionType.UNTIMEOUT,
                    reason=reason,
                )
            )
