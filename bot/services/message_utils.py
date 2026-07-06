from __future__ import annotations

"""Message utilities — adapted from openclaw's draft-chunking.ts and send.messages.ts.

Discord caps messages at 2000 characters. openclaw solves this with draft chunking
(splitting on paragraph/sentence boundaries). We do the same here, plus provide a
streaming simulation that edits a placeholder message progressively while the AI
thinks, so users see activity immediately instead of waiting in silence.
"""

import asyncio
import discord

DISCORD_CHAR_LIMIT = 1990  # 2000 minus a small safety margin


def chunk_text(text: str, limit: int = DISCORD_CHAR_LIMIT) -> list[str]:
    """Split text into ≤limit-character chunks, preferring paragraph → sentence → word
    boundaries (same approach as openclaw's draft-chunking.ts).

    >>> chunks = chunk_text("Hello.\\n\\nWorld.")
    >>> all(len(c) <= 1990 for c in chunks)
    True
    """
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break

        # Try paragraph boundary first
        cut = text.rfind("\n\n", 0, limit)
        if cut == -1:
            # Try sentence boundary
            for sep in (". ", "! ", "? ", "\n"):
                pos = text.rfind(sep, 0, limit)
                if pos != -1:
                    cut = pos + len(sep)
                    break
        if cut == -1:
            # Hard cut at word boundary
            cut = text.rfind(" ", 0, limit)
        if cut == -1:
            # No boundary found — hard cut
            cut = limit

        chunks.append(text[:cut].rstrip())
        text = text[cut:].lstrip()

    return [c for c in chunks if c]


async def send_chunked(
    target: discord.abc.Messageable,
    text: str,
    *,
    reference: discord.Message | None = None,
    mention_author: bool = False,
) -> list[discord.Message]:
    """Send text that may exceed 2000 chars as multiple messages.
    The first chunk uses reply/mention logic; subsequent chunks are plain sends.
    """
    chunks = chunk_text(text)
    sent: list[discord.Message] = []
    for i, chunk in enumerate(chunks):
        if i == 0 and reference is not None:
            msg = await reference.reply(chunk, mention_author=mention_author)
        else:
            msg = await target.send(chunk)
        sent.append(msg)
        if i < len(chunks) - 1:
            await asyncio.sleep(0.3)  # small delay so Discord doesn't rate-limit
    return sent


async def stream_response(
    target: discord.abc.Messageable,
    coro,
    *,
    reference: discord.Message | None = None,
    mention_author: bool = False,
    thinking_text: str = "⏳ Thinking...",
) -> tuple[str, list[discord.Message]]:
    """Simulate streaming: send a placeholder immediately, then edit it with the real
    response once the AI coroutine completes. Adapted from openclaw's draft-stream.ts.

    Returns (final_text, list_of_sent_messages).
    """
    # Send placeholder right away so the user sees activity immediately.
    if reference is not None:
        placeholder = await reference.reply(thinking_text, mention_author=mention_author)
    else:
        placeholder = await target.send(thinking_text)

    try:
        result_text: str = await coro
    except Exception:
        await placeholder.edit(content="❌ Something went wrong.")
        raise

    chunks = chunk_text(result_text)
    messages: list[discord.Message] = []

    # Edit the placeholder with the first chunk.
    await placeholder.edit(content=chunks[0])
    messages.append(placeholder)

    # Send any additional chunks as new messages.
    for chunk in chunks[1:]:
        await asyncio.sleep(0.3)
        msg = await target.send(chunk)
        messages.append(msg)

    return result_text, messages
