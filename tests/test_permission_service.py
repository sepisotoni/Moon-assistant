"""Tests for bot/services/permission_service.py."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from bot.services.permission_service import get_member_visible_channel_ids, has_owner_or_founder_role


def _make_member(role_names: list[str], *, admin: bool = False) -> MagicMock:
    member = MagicMock()
    member.guild_permissions = MagicMock(administrator=admin)
    roles = []
    for name in role_names:
        r = MagicMock()
        r.name = name
        roles.append(r)
    member.roles = roles
    return member


class TestOwnerCheck:
    def test_owner_role_grants_elevated(self):
        member = _make_member(["Owner"])
        assert has_owner_or_founder_role(member)

    def test_founder_role_grants_elevated(self):
        member = _make_member(["Founder"])
        assert has_owner_or_founder_role(member)

    def test_owner_founder_case_insensitive(self):
        assert has_owner_or_founder_role(_make_member(["OWNER"]))
        assert has_owner_or_founder_role(_make_member(["founder"]))
        assert has_owner_or_founder_role(_make_member(["FOUNDER"]))

    def test_admin_flag_grants_elevated(self):
        member = _make_member([], admin=True)
        assert has_owner_or_founder_role(member)

    def test_regular_member_not_elevated(self):
        member = _make_member(["Member", "Verified"])
        assert not has_owner_or_founder_role(member)

    def test_no_roles_not_elevated(self):
        member = _make_member([])
        assert not has_owner_or_founder_role(member)


class TestVisibleChannelIds:
    def _make_guild_with_channels(self, channel_ids_visible: list[tuple[int, bool]]) -> tuple:
        guild = MagicMock()
        channels = []
        for ch_id, visible in channel_ids_visible:
            ch = MagicMock()
            ch.id = ch_id
            perms = MagicMock()
            perms.view_channel = visible
            ch.permissions_for = MagicMock(return_value=perms)
            channels.append(ch)
        guild.text_channels = channels
        guild.threads = []
        member = MagicMock()
        return guild, member

    def test_visible_channels_included(self):
        guild, member = self._make_guild_with_channels([(100, True), (200, False), (300, True)])
        visible = get_member_visible_channel_ids(guild, member)
        assert 100 in visible
        assert 300 in visible
        assert 200 not in visible

    def test_no_visible_channels_returns_empty_set(self):
        guild, member = self._make_guild_with_channels([(100, False), (200, False)])
        visible = get_member_visible_channel_ids(guild, member)
        assert visible == set()
