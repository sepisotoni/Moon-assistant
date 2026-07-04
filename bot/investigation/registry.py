from __future__ import annotations

from bot.ai.intent_service import Intent
from bot.investigation.base import InvestigationTool
from bot.investigation.tools import (
    KnownIssuesTool,
    LinkedAccountTool,
    MaintenanceStatusTool,
    PunishmentHistoryTool,
    RecentAnnouncementsTool,
    WhitelistStatusTool,
)

_TOOLS: dict[str, InvestigationTool] = {
    WhitelistStatusTool.key: WhitelistStatusTool(),
    KnownIssuesTool.key: KnownIssuesTool(),
    PunishmentHistoryTool.key: PunishmentHistoryTool(),
    LinkedAccountTool.key: LinkedAccountTool(),
    RecentAnnouncementsTool.key: RecentAnnouncementsTool(),
    MaintenanceStatusTool.key: MaintenanceStatusTool(),
}

# Which tools fire for which intent. Adding a new tool to an intent is a one-line change here --
# no cog or command code needs to change.
_INTENT_TOOL_MAP: dict[Intent, list[str]] = {
    Intent.JOIN_ISSUE: [
        MaintenanceStatusTool.key,
        WhitelistStatusTool.key,
        PunishmentHistoryTool.key,
        KnownIssuesTool.key,
    ],
    Intent.WHITELIST_ISSUE: [WhitelistStatusTool.key, LinkedAccountTool.key],
    Intent.PUNISHMENT_QUESTION: [PunishmentHistoryTool.key],
    Intent.ACCOUNT_LINKING: [LinkedAccountTool.key],
    Intent.INVESTIGATION: [
        WhitelistStatusTool.key,
        PunishmentHistoryTool.key,
        LinkedAccountTool.key,
        KnownIssuesTool.key,
        MaintenanceStatusTool.key,
        RecentAnnouncementsTool.key,
    ],
}


def tools_for_intent(intent: Intent) -> list[InvestigationTool]:
    keys = _INTENT_TOOL_MAP.get(intent, [])
    return [_TOOLS[k] for k in keys]


def get_tool(key: str) -> InvestigationTool | None:
    return _TOOLS.get(key)


def all_tool_keys() -> list[str]:
    return list(_TOOLS.keys())
