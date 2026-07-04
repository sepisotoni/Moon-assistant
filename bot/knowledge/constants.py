from __future__ import annotations

# Channel names (without the leading '#') that are automatically indexed into
# the knowledge_entries table and used to ground /ask responses.
KNOWLEDGE_CHANNEL_NAMES: set[str] = {"ai-ip", "ai-faq", "ai-news", "ai-store"}
