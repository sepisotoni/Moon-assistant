"""Repository layer (introduced in Phase 2/3).

Phase 1's services queried the database directly through `get_session()`. That still works and
is left untouched. For the new Phase 2/3 subsystems we introduce a thin repository layer that
separates *data access* (these classes) from *business logic* (the services in bot/ai,
bot/moderation, bot/knowledge, bot/investigation, bot/services), per the requirement to use
"the existing repository pattern". Repositories never make Discord API calls and never contain
business rules -- they only translate between ORM rows and the service layer.
"""
