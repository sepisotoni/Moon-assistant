import datetime as dt
import json
import logging
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import discord

from bot.config import get_settings
from bot.database.models import BotLog
from bot.database.session import get_session

settings = get_settings()

logger = logging.getLogger(__name__)


class _OffsetFormatter(logging.Formatter):
    """Logging formatter that displays timestamps in a fixed UTC offset timezone.

    stdlib's Formatter.formatTime() always uses local system time or UTC.
    This subclass converts the record's UTC epoch to UTC+<offset_hours> so logs
    show times in the server's configured timezone regardless of where the process runs.
    """

    def __init__(self, fmt: str, offset_hours: int = 0) -> None:
        super().__init__(fmt=fmt)
        self._tz = dt.timezone(dt.timedelta(hours=offset_hours))
        self._offset_hours = offset_hours

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        ts = dt.datetime.fromtimestamp(record.created, tz=self._tz)
        return ts.strftime(datefmt or "%Y-%m-%d %H:%M:%S") + f" UTC{self._offset_hours:+d}"


def setup_logging() -> None:
    """Configure stdlib logging for the whole process. Call once at startup."""
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        _OffsetFormatter(
            fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            offset_hours=settings.tz_offset_hours,
        )
    )
    root.addHandler(handler)

    # Quiet noisy third-party loggers.
    logging.getLogger("discord.http").setLevel(logging.WARNING)
    logging.getLogger("discord.gateway").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def format_ts(ts: dt.datetime | None = None) -> str:
    """Format a datetime (default: now) in the configured display timezone.

    Use this in Discord embeds and command responses wherever a human-readable
    timestamp is needed, so all displayed times are consistent with the log output.

    Example:
        embed.set_footer(text=f"As of {format_ts()}")
        # → "As of 2026-07-04 15:32:10 UTC+2"
    """
    tz = dt.timezone(dt.timedelta(hours=settings.tz_offset_hours))
    when = (ts or dt.datetime.now(dt.timezone.utc)).astimezone(tz)
    return when.strftime("%Y-%m-%d %H:%M:%S") + f" UTC{settings.tz_offset_hours:+d}"


class DatabaseLogService:
    """Persists important events to bot_logs, and optionally mirrors WARNING+ events to a
    configured Discord log channel (LOG_CHANNEL_ID) so staff see them without querying the DB.

    Pass a discord.Client (or Bot) instance via ``attach_client`` after the bot is ready.
    """

    def __init__(self) -> None:
        self._client: "discord.Client | None" = None

    def attach_client(self, client: "discord.Client") -> None:
        """Call after the bot is ready so we can post to the log channel."""
        self._client = client

    async def log(
        self,
        *,
        level: str,
        source: str,
        message: str,
        guild_id: int | None = None,
        meta: dict | None = None,
    ) -> None:
        # 1. Persist to DB.
        try:
            async with get_session() as session:
                session.add(
                    BotLog(
                        level=level.upper(),
                        source=source,
                        message=message,
                        guild_id=guild_id,
                        meta=json.dumps(meta) if meta else None,
                    )
                )
        except Exception:
            logger.exception("DatabaseLogService: failed to persist log entry")

        # 2. Mirror WARNING/ERROR/CRITICAL to the Discord log channel (if configured).
        if (
            level.upper() in ("WARNING", "ERROR", "CRITICAL")
            and settings.log_channel_id
            and self._client is not None
        ):
            try:
                import discord

                channel = self._client.get_channel(settings.log_channel_id)
                if isinstance(channel, discord.TextChannel):
                    prefix = {"WARNING": "⚠️", "ERROR": "❌", "CRITICAL": "🚨"}.get(level.upper(), "ℹ️")
                    guild_tag = f"[guild:{guild_id}] " if guild_id else ""
                    await channel.send(f"{prefix} **{source}** {guild_tag}— {message[:1800]}")
            except Exception:
                logger.exception("DatabaseLogService: failed to send log to Discord channel")
