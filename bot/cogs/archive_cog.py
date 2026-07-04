from __future__ import annotations

import logging

import discord
from discord.ext import commands

from bot.knowledge.indexer import KnowledgeIndexer, is_knowledge_channel
from bot.knowledge.learning_service import KnowledgeLearningService
from bot.services.archive_service import ArchiveService

logger = logging.getLogger(__name__)


class ArchiveCog(commands.Cog):
    """Archives every message into Postgres and indexes knowledge channels."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.archive_service = ArchiveService()
        self.knowledge_indexer = KnowledgeIndexer()
        self.knowledge_learning = KnowledgeLearningService()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot:
            return

        knowledge = is_knowledge_channel(message.channel)
        try:
            await self.archive_service.archive_message(message, is_knowledge_channel=knowledge)
            if knowledge:
                await self.knowledge_indexer.index_message(message)
        except Exception:
            logger.exception("Failed to archive message %s", message.id)

        # Still allow prefix-style commands (if any are added later) to function.
        await self.bot.process_commands(message)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        if after.guild is None or after.author.bot:
            return
        try:
            await self.archive_service.update_content(after)
            if is_knowledge_channel(after.channel):
                # Snapshot the OLD content as a version before overwriting it.
                await self.knowledge_learning.on_knowledge_message_edited(before)
                await self.knowledge_indexer.update_message(after)
        except Exception:
            logger.exception("Failed to update archived message %s", after.id)

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message) -> None:
        if message.guild is None:
            return
        try:
            await self.archive_service.mark_deleted(message.id)
            if is_knowledge_channel(message.channel):
                await self.knowledge_indexer.remove_message(message.id)
        except Exception:
            logger.exception("Failed to mark archived message %s as deleted", message.id)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ArchiveCog(bot))
