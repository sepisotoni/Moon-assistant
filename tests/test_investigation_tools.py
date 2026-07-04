"""Tests for bot/investigation – pluggable tool registry and base tool behavior."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.ai.intent_service import Intent
from bot.investigation.base import InvestigationContext
from bot.investigation.registry import all_tool_keys, get_tool, tools_for_intent
from bot.investigation.tools import KnownIssuesTool, PunishmentHistoryTool


class TestRegistry:
    def test_all_tools_have_keys(self):
        keys = all_tool_keys()
        assert len(keys) > 0
        for k in keys:
            assert isinstance(k, str) and len(k) > 0

    def test_get_tool_returns_correct_type(self):
        tool = get_tool("whitelist_status")
        assert tool is not None
        assert tool.key == "whitelist_status"

    def test_get_tool_returns_none_for_unknown(self):
        assert get_tool("nonexistent_tool_xyz") is None

    def test_join_issue_fires_multiple_tools(self):
        tools = tools_for_intent(Intent.JOIN_ISSUE)
        assert len(tools) >= 3

    def test_general_question_fires_no_tools(self):
        tools = tools_for_intent(Intent.GENERAL_QUESTION)
        assert tools == []

    def test_investigation_fires_all_tools(self):
        tools = tools_for_intent(Intent.INVESTIGATION)
        assert len(tools) >= 5


@pytest.fixture
def mock_ctx():
    guild = MagicMock()
    guild.id = 1
    requester = MagicMock()
    return InvestigationContext(guild=guild, requester=requester, target_user_id=None, question="test")


@pytest.mark.asyncio
class TestKnownIssuesTool:
    async def test_no_issues_returns_finding(self, mock_ctx):
        tool = KnownIssuesTool()
        tool._repo = AsyncMock()
        tool._repo.known_issues = AsyncMock(return_value=[])
        finding = await tool.run(mock_ctx)
        assert finding.tool_key == "known_issues"
        assert "no open" in finding.finding_text.lower()
        assert 0.0 <= finding.confidence <= 1.0

    async def test_open_issues_appear_in_finding(self, mock_ctx):
        tool = KnownIssuesTool()
        issue = MagicMock()
        issue.title = "EU Maintenance"
        issue.description = "Servers offline for maintenance"
        tool._repo = AsyncMock()
        tool._repo.known_issues = AsyncMock(return_value=[issue])
        finding = await tool.run(mock_ctx)
        assert "EU Maintenance" in finding.finding_text
        assert finding.confidence > 0.8


@pytest.mark.asyncio
class TestPunishmentHistoryTool:
    async def test_no_target_user(self, mock_ctx):
        """Should gracefully handle missing target_user_id."""
        tool = PunishmentHistoryTool()
        finding = await tool.run(mock_ctx)
        assert finding.tool_key == "punishment_history"
        assert finding.confidence < 0.3  # no target = low confidence finding
