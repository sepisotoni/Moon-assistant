"""Tests confirming /airules and !ai-rules both write to constitution_rules."""
from __future__ import annotations

import pytest

from bot.database.models_ai import ConstitutionTier


@pytest.mark.asyncio
class TestRulesUnification:
    """Both /airules (AIRulesCog) and !ai-rules (FounderAdminCog) now delegate to
    ConstitutionService, which writes to constitution_rules. This test verifies a
    rule added via the constitution service appears in the same list that SupportEngine
    will read.
    """

    async def test_server_rule_readable_by_constitution_service(self, patched_get_session):
        from bot.ai.constitution_service import ConstitutionService

        svc = ConstitutionService()
        await svc.add_server_rule(
            guild_id=500,
            title="No advertising",
            rule_text="Do not advertise other servers.",
            created_by=1,
        )
        rules = await svc.list_rules(500)
        assert any(r.title == "No advertising" for r in rules)
        assert all(r.tier == ConstitutionTier.SERVER for r in rules if r.title == "No advertising")

    async def test_rule_injected_into_system_prompt(self, patched_get_session):
        from bot.ai.constitution_service import ConstitutionService

        svc = ConstitutionService()
        await svc.add_server_rule(
            guild_id=501,
            title="English only",
            rule_text="Only speak English in general channels.",
            created_by=1,
        )
        prompt = await svc.build_system_prompt(
            guild_id=501, base_prompt="You are a helpful assistant."
        )
        assert "English only" in prompt or "Only speak English" in prompt

    async def test_disabled_rule_not_in_prompt(self, patched_get_session):
        from bot.ai.constitution_service import ConstitutionService

        svc = ConstitutionService()
        rule = await svc._repo.add(
            tier=ConstitutionTier.SERVER,
            title="Rule to disable",
            rule_text="This should not appear.",
            guild_id=502,
            created_by=1,
        )
        await svc.set_enabled(rule.id, is_enabled=False)
        svc.invalidate_cache(502)
        prompt = await svc.build_system_prompt(guild_id=502, base_prompt="Base.")
        assert "This should not appear" not in prompt
