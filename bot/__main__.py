from __future__ import annotations

import asyncio
import logging

from bot.bot import AIModerationBot
from bot.config import get_settings
from bot.services.logging_service import setup_logging

logger = logging.getLogger(__name__)


async def main() -> None:
    setup_logging()
    settings = get_settings()
    bot = AIModerationBot(settings)

    async with bot:
        await bot.start(settings.discord_token)


if __name__ == "__main__":
    asyncio.run(main())
