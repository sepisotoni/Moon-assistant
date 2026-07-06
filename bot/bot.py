from __future__ import annotations

import logging

import discord
from discord.ext import commands

from bot.ai.constitution_service import ConstitutionService
from bot.ai.gemini_provider import GeminiProvider
from bot.ai.intent_service import IntentDetectionService
from bot.ai.manager import AIProviderManager
from bot.ai.model_routing import ModelRouter
from bot.ai.openrouter_provider import OpenRouterProvider
from bot.ai.orchestrator import AIOrchestrator
from bot.config import Settings
from bot.database.base import Base
from bot.database.session import get_engine
from bot.knowledge.learning_service import KnowledgeLearningService
from bot.moderation.intelligence_service import ModerationIntelligenceService
from bot.repositories.ai_repository import DecisionLogRepository
from bot.repositories.moderation_intel_repository import InvestigationRepository as InvRepo
from bot.services.agent_service import AgentService
from bot.services.assistant_tools_service import AssistantToolsService
from bot.services.conversation_service import ConversationService
from bot.services.investigation_service import InvestigationService
from bot.services.logging_service import DatabaseLogService
from bot.services.memory_service import MemoryService
from bot.services.model_routing_service import ModelRoutingService
from bot.services.support_engine import SupportEngine

logger = logging.getLogger(__name__)

EXTENSIONS: tuple[str, ...] = (
    # Phase 1 (unchanged)
    "bot.cogs.archive_cog",
    "bot.cogs.search_cog",
    "bot.cogs.ai_cog",
    "bot.cogs.ai_rules_cog",
    "bot.cogs.moderation_cog",
    "bot.cogs.admin_cog",
    # Phase 2/3
    "bot.cogs.assistant_cog",
    "bot.cogs.moderation_intel_cog",
    "bot.cogs.founder_admin_cog",
    "bot.cogs.model_routing_cog",
    "bot.cogs.background_tasks_cog",
    "bot.cogs.error_handler_cog",
    "bot.cogs.conversation_cog",
    "bot.cogs.music_cog",
)


class AIModerationBot(commands.Bot):
    """The bot, extended for Phase 2/3 services and the constitution / orchestrator layer."""

    def __init__(self, settings: Settings) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        intents.guilds = True

        super().__init__(command_prefix=settings.command_prefix, intents=intents)
        self.settings = settings

        # ------------------------------------------------------------------ #
        # Build the Phase 1 legacy manager (still used as fallback)
        # ------------------------------------------------------------------ #
        primary = OpenRouterProvider(
            api_key=settings.openrouter_api_key or "",
            model=settings.openrouter_model,
            base_url=settings.openrouter_base_url,
        )
        fallback = (
            GeminiProvider(api_key=settings.gemini_api_key, model=settings.gemini_model)
            if settings.gemini_api_key
            else None
        )
        legacy_manager = AIProviderManager(primary=primary, fallback=fallback)

        # For Phase 1's ai_cog which uses self.ai_manager directly:
        self.ai_manager = legacy_manager

        # ------------------------------------------------------------------ #
        # Phase 2/3 service graph (injected onto self so cogs can access them
        # via bot.<service>, keeping all construction here not in cogs)
        # ------------------------------------------------------------------ #
        self._router = ModelRouter(settings)
        self.orchestrator = AIOrchestrator(settings, self._router, legacy_manager)

        self.constitution_service = ConstitutionService()
        self.intent_service = IntentDetectionService(orchestrator=self.orchestrator)
        self.memory_service = MemoryService()
        self.knowledge_learning_service = KnowledgeLearningService()
        self.model_routing_service = ModelRoutingService()
        self.decision_log_repo = DecisionLogRepository()
        self.investigation_repo = InvRepo()

        self.support_engine = SupportEngine(
            orchestrator=self.orchestrator,
            constitution=self.constitution_service,
            intent_service=self.intent_service,
        )
        self.assistant_tools = AssistantToolsService(
            orchestrator=self.orchestrator,
            constitution=self.constitution_service,
            intent_service=self.intent_service,
            support_engine=self.support_engine,
        )
        self.investigation_service = InvestigationService(orchestrator=self.orchestrator)
        self.moderation_intel_service = ModerationIntelligenceService(orchestrator=self.orchestrator)
        self.agent_service = AgentService(orchestrator=self.orchestrator)
        self.conversation = ConversationService()
        self.db_log_service = DatabaseLogService()

    async def setup_hook(self) -> None:
        # Warn early if no AI keys are configured.
        if not self.settings.openrouter_api_key and not self.settings.gemini_api_key:
            logger.warning(
                "⚠️  No AI provider keys are configured. "
                "Set OPENROUTER_API_KEY and/or GEMINI_API_KEY in your .env file. "
                "All AI commands will fail until at least one key is provided."
            )
        elif not self.settings.openrouter_api_key:
            logger.warning("OPENROUTER_API_KEY not set — only Gemini models will be available.")
        elif not self.settings.gemini_api_key:
            logger.info("GEMINI_API_KEY not set — Gemini fallback is disabled.")

        # Convenience: create tables for fresh dev environments.
        # `alembic upgrade head` is canonical for production.
        from bot.database.session import get_engine
        engine = await get_engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        # Seed model registry + constitution once tables exist.
        await self._router.seed_defaults()
        await self.constitution_service.ensure_seeded()

        for extension in EXTENSIONS:
            try:
                await self.load_extension(extension)
                logger.info("Loaded extension: %s", extension)
            except Exception:
                logger.exception("Failed to load extension: %s", extension)

        await self.tree.sync()
        logger.info("Slash commands synced globally.")

    async def close(self) -> None:
        await self.orchestrator.close()
        await super().close()

    async def on_ready(self) -> None:
        logger.info("Logged in as %s (id=%s)", self.user, getattr(self.user, "id", "?"))
        self.db_log_service.attach_client(self)
        # Set bot presence so members can see it's online and what it does.
        await self.change_presence(
            status=discord.Status.online,
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="the server | /ask or @mention me",
            ),
        )

    async def on_message(self, message: discord.Message) -> None:
        """Single global on_message — dispatches to cog listeners then processes prefix commands
        exactly once. Keeping process_commands here (not in any cog) prevents double-invocation."""
        if not message.author.bot:
            await self.process_commands(message)
