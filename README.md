# Discord AI Moderation & Support Bot

A modular Discord bot built with `discord.py`, PostgreSQL, and SQLAlchemy that:

- Archives every message into Postgres
- Provides **permission-aware** message search (members only search channels they can see; `Owner`/`Founder` roles can search everything; inaccessible content is never revealed)
- Auto-indexes "knowledge channels" (`#ai-ip`, `#ai-faq`, `#ai-news`, `#ai-store`) into a dedicated knowledge table for RAG-style grounding
- Answers questions via an AI provider abstraction: **OpenRouter (primary) → Gemini (fallback)**
- Stores AI behavior **rules** in the database and injects them into the system prompt
- Implements a moderation framework: warnings, message deletion, timeouts (capped at 60 minutes)
- Implements a logging framework (stdlib logging + a `bot_logs` audit table)

## Project layout

```
bot/
  config.py            # pydantic-settings driven .env config
  bot.py                # AIModerationBot (commands.Bot subclass), extension loading
  __main__.py           # entrypoint: `python -m bot`
  database/             # SQLAlchemy models, async engine/session
  ai/                   # AI provider abstraction (OpenRouter, Gemini), rules service
  moderation/           # warnings / deletion / timeout business logic
  knowledge/            # knowledge-channel detection, indexing, retrieval
  services/             # archive, search, permissions, logging (cross-cutting services)
  cogs/                 # discord.py Cogs wiring slash commands to services
alembic/                # DB migrations (async-aware env.py)
```

## Requirements

- Python 3.12
- PostgreSQL 14+
- A Discord application/bot token with the `message_content`, `guilds`, and `members` privileged intents enabled in the Developer Portal
- An OpenRouter API key (primary AI provider)
- A Gemini API key (fallback AI provider) — optional, but recommended

## Setup

1. **Clone & create a virtualenv**

   ```bash
   python3.12 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Configure environment**

   ```bash
   cp .env.example .env
   # edit .env: DISCORD_TOKEN, DATABASE_URL, OPENROUTER_API_KEY, GEMINI_API_KEY, ...
   ```

3. **Start Postgres** (optional convenience via Docker)

   ```bash
   docker compose up -d db
   ```

4. **Run database migrations**

   ```bash
   alembic upgrade head
   ```

   > Note: `bot.bot.AIModerationBot.setup_hook` also calls `Base.metadata.create_all` as a
   > convenience for fresh dev environments, but Alembic should be treated as the source of
   > truth for schema changes in any real deployment.

5. **Run the bot**

   ```bash
   python -m bot
   ```

   Or with Docker Compose (bot + db):

   ```bash
   docker compose up --build
   ```

## Slash commands (skeleton)

| Command | Description | Access |
|---|---|---|
| `/search query:` | Permission-aware search over archived messages | Everyone (results filtered to visible channels) |
| `/ask question:` | Ask the AI assistant, grounded in knowledge channels + AI rules | Everyone |
| `/airules add|list|remove` | Manage AI behavior rules stored in Postgres | `Owner` / `Founder` roles only |
| `/warn member: reason:` | Warn a member (persisted) | `moderate_members` permission |
| `/warnings member:` | List a member's warnings | `moderate_members` permission |
| `/purge message_id:` | Delete a message and log the action | `manage_messages` permission |
| `/timeout member: minutes:(1-60)` | Timeout a member, capped at 60 minutes | `moderate_members` permission |
| `/untimeout member:` | Remove an active timeout | `moderate_members` permission |
| `/ping` | Health check | Everyone |
| `/sync` | Force re-sync of slash commands | Bot owner |

## Extending

- **New AI provider**: implement `bot.ai.base.AIProvider` and wire it into `bot.bot.build_ai_manager`.
- **New knowledge channel**: add its name to `bot.knowledge.constants.KNOWLEDGE_CHANNEL_NAMES`.
- **New moderation action**: add an enum value to `ModerationActionType` and a method on `ModerationService`.
- **New migration**: `alembic revision --autogenerate -m "describe change"` then review the generated file before applying.

This is a code **skeleton**: business logic for archiving, permission filtering, AI fallback, and
moderation is fully implemented, but you should review timeout/permission edge cases, add retry/
rate-limit handling around AI calls, and add tests before running it against a production community.
