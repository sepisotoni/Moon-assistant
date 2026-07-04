"""Tests for spam and raid heuristic detectors."""
from __future__ import annotations

import pytest

from bot.moderation.heuristics import RaidDetector, SpamDetector


class TestSpamDetector:
    def test_below_threshold_no_trigger(self):
        det = SpamDetector()
        for _ in range(5):
            assert not det.record_and_check(1, 100)

    def test_triggers_at_threshold(self):
        det = SpamDetector()
        results = [det.record_and_check(1, 200) for _ in range(6)]
        assert results[-1] is True

    def test_different_users_independent(self):
        det = SpamDetector()
        for _ in range(6):
            det.record_and_check(1, 300)
        # User 400 has only 1 message; should not trigger.
        assert not det.record_and_check(1, 400)

    def test_different_guilds_independent(self):
        det = SpamDetector()
        for _ in range(6):
            det.record_and_check(guild_id=1, user_id=500)
        assert not det.record_and_check(guild_id=2, user_id=500)

    def test_return_type_is_bool(self):
        det = SpamDetector()
        result = det.record_and_check(99, 99)
        assert isinstance(result, bool)


class TestRaidDetector:
    def test_below_threshold_no_trigger(self):
        det = RaidDetector()
        for _ in range(7):
            assert not det.record_join_and_check(1)

    def test_triggers_at_threshold(self):
        det = RaidDetector()
        results = [det.record_join_and_check(1) for _ in range(8)]
        assert results[-1] is True

    def test_join_count_tracks_correctly(self):
        det = RaidDetector()
        for _ in range(3):
            det.record_join_and_check(99)
        assert det.current_join_count(99) == 3

    def test_different_guilds_independent(self):
        det = RaidDetector()
        for _ in range(8):
            det.record_join_and_check(1)
        assert not det.record_join_and_check(2)

    def test_unknown_guild_count_is_zero(self):
        det = RaidDetector()
        assert det.current_join_count(99999) == 0
