from __future__ import annotations

import datetime as dt
from collections import defaultdict, deque

from bot.config import get_settings

settings = get_settings()


class SpamDetector:
    """Sliding-window message-rate spam detector, per (guild, user)."""

    def __init__(self) -> None:
        self._windows: dict[tuple[int, int], deque[dt.datetime]] = defaultdict(deque)

    def record_and_check(self, guild_id: int, user_id: int) -> bool:
        now = dt.datetime.now(dt.timezone.utc)
        key = (guild_id, user_id)
        window = self._windows[key]
        window.append(now)

        cutoff = now - dt.timedelta(seconds=settings.spam_window_seconds)
        while window and window[0] < cutoff:
            window.popleft()

        return len(window) >= settings.spam_message_threshold


class RaidDetector:
    """Sliding-window join-rate detector, per guild, for raid/brigading detection."""

    def __init__(self) -> None:
        self._windows: dict[int, deque[dt.datetime]] = defaultdict(deque)

    def record_join_and_check(self, guild_id: int) -> bool:
        now = dt.datetime.now(dt.timezone.utc)
        window = self._windows[guild_id]
        window.append(now)

        cutoff = now - dt.timedelta(seconds=settings.raid_window_seconds)
        while window and window[0] < cutoff:
            window.popleft()

        return len(window) >= settings.raid_join_threshold

    def current_join_count(self, guild_id: int) -> int:
        return len(self._windows.get(guild_id, ()))
