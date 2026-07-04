"""Tests for bot/moderation/action_guard.py – hard safety ceiling and forbidden-action guard."""
from __future__ import annotations

import pytest

from bot.moderation.action_guard import (
    ABSOLUTE_MAX_TIMEOUT_MINUTES,
    ForbiddenActionError,
    assert_action_allowed,
    clamp_timeout_minutes,
)


class TestAbsoluteCeiling:
    def test_ceiling_is_60(self):
        assert ABSOLUTE_MAX_TIMEOUT_MINUTES == 60

    def test_ceiling_is_a_plain_int_not_env(self):
        # Must be a hard-coded int constant, not read from any config/env source.
        assert isinstance(ABSOLUTE_MAX_TIMEOUT_MINUTES, int)


class TestClampTimeout:
    def test_value_within_both_limits(self):
        assert clamp_timeout_minutes(10, 60) == 10

    def test_value_exceeds_config_but_within_ceiling(self):
        assert clamp_timeout_minutes(45, 30) == 30

    def test_value_exceeds_absolute_ceiling(self):
        assert clamp_timeout_minutes(120, 120) == ABSOLUTE_MAX_TIMEOUT_MINUTES

    def test_misconfigured_max_still_clamped(self):
        """Even MAX_TIMEOUT_MINUTES=9999 must yield 60."""
        assert clamp_timeout_minutes(9999, 9999) == ABSOLUTE_MAX_TIMEOUT_MINUTES

    def test_exactly_ceiling(self):
        assert clamp_timeout_minutes(60, 60) == 60

    def test_zero_raises(self):
        with pytest.raises(ValueError):
            clamp_timeout_minutes(0, 60)

    def test_negative_raises(self):
        with pytest.raises(ValueError):
            clamp_timeout_minutes(-5, 60)


class TestAssertActionAllowed:
    @pytest.mark.parametrize("action", [
        "warn", "delete_message", "timeout", "untimeout", "none", "escalate",
    ])
    def test_allowed_actions_do_not_raise(self, action):
        assert_action_allowed(action)  # must not raise

    @pytest.mark.parametrize("forbidden", [
        "kick", "ban", "ban_user", "KICK", "soft_ban",
        "permission_change", "role_grant", "role_revoke",
    ])
    def test_forbidden_actions_raise(self, forbidden):
        with pytest.raises(ForbiddenActionError):
            assert_action_allowed(forbidden)
