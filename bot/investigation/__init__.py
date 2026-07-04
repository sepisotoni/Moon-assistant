"""Pluggable join-issue / punishment / account investigation tools (Phase 2/3).

Each tool implements InvestigationTool and is registered in bot/investigation/registry.py.
The InvestigationService (bot/services/investigation_service.py) runs every tool relevant to the
detected intent and aggregates their findings.
"""
