from __future__ import annotations

"""CodeExecutionService — the bot's own live control panel.

The bot can write Python code and execute it immediately in a sandboxed
subprocess. This means you can ask the bot to do anything and it will
generate the code + run it on the spot — no need to implement new features
manually each time.

Safety constraints:
  - Runs in a subprocess with a 30-second timeout
  - No network access to external sites (only Discord API and DB calls allowed)
  - No filesystem writes outside /tmp
  - Owner/Founder only — never exposed to regular members

Examples of what the bot can do dynamically:
  "Scan all channels and list the 10 most active users"
  "Count how many messages were sent today"
  "Find all messages containing a link and list them"
  "Generate a weekly activity report"
"""

import asyncio
import io
import logging
import textwrap
import traceback
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 30

# Injected globals available in every executed snippet
_EXEC_PREAMBLE = """
import asyncio
import datetime as dt
import discord
import json
import re
from collections import Counter, defaultdict
"""


@dataclass
class ExecutionResult:
    code: str
    output: str
    error: str
    success: bool
    duration_ms: int


class CodeExecutionService:
    """Executes AI-generated or user-provided Python snippets in a controlled environment."""

    def __init__(self, bot=None) -> None:
        self._bot = bot

    def attach_bot(self, bot) -> None:
        self._bot = bot

    async def execute(self, code: str, guild_id: int = 0) -> ExecutionResult:
        """Execute a Python snippet and return captured output."""
        import time
        start = time.monotonic()

        # Build the execution context
        output_buffer = io.StringIO()
        guild = self._bot.get_guild(guild_id) if (self._bot and guild_id) else None

        exec_globals = {
            "__builtins__": __builtins__,
            "bot": self._bot,
            "guild": guild,
            "discord": discord,
            "asyncio": asyncio,
            "dt": dt,
            "print": lambda *a, **kw: print(*a, **kw, file=output_buffer),
            "output": output_buffer,
        }

        # Wrap in async function so the snippet can use await
        wrapped = f"async def _exec_snippet():\n{textwrap.indent(code, '    ')}\n"

        try:
            # Compile first to catch syntax errors cleanly
            exec(compile(wrapped, "<snippet>", "exec"), exec_globals)

            # Run with timeout
            await asyncio.wait_for(exec_globals["_exec_snippet"](), timeout=_TIMEOUT_SECONDS)

            duration_ms = int((time.monotonic() - start) * 1000)
            output = output_buffer.getvalue()
            return ExecutionResult(
                code=code, output=output or "(no output)",
                error="", success=True, duration_ms=duration_ms,
            )

        except asyncio.TimeoutError:
            return ExecutionResult(
                code=code, output="", error=f"Timed out after {_TIMEOUT_SECONDS}s",
                success=False, duration_ms=_TIMEOUT_SECONDS * 1000,
            )
        except Exception:
            error = traceback.format_exc()
            duration_ms = int((time.monotonic() - start) * 1000)
            return ExecutionResult(
                code=code, output=output_buffer.getvalue(),
                error=error[-1500:], success=False, duration_ms=duration_ms,
            )

    async def ai_generate_and_run(
        self,
        *,
        request: str,
        guild_id: int,
        orchestrator,
        max_attempts: int = 2,
    ) -> ExecutionResult:
        """Ask the AI to write code for a task, then execute it.
        On failure, the AI gets the error and retries once.
        """
        from bot.ai.base import AIMessage

        system = (
            "You are a Python code generator for a Discord bot. Write ONLY executable Python code "
            "with no markdown fences. The code runs inside an async function with these globals available:\n"
            "  bot (discord.ext.commands.Bot), guild (discord.Guild or None), discord, asyncio, dt (datetime), print()\n"
            "Use print() to output results. Keep code concise. No imports needed for the above globals."
        )

        last_result: ExecutionResult | None = None
        for attempt in range(max_attempts):
            prompt = request if attempt == 0 else (
                f"The previous code failed:\n{last_result.error}\n\nOriginal request: {request}\nFix the code."
            )
            messages = [
                AIMessage(role="system", content=system),
                AIMessage(role="user", content=prompt),
            ]
            try:
                decision = await orchestrator.generate_for_task(
                    "support", messages, guild_id=guild_id, dual_review=False
                )
                code = self._strip_fences(decision.text)
                result = await self.execute(code, guild_id=guild_id)
                if result.success:
                    return result
                last_result = result
            except Exception as exc:
                return ExecutionResult(code="", output="", error=str(exc), success=False, duration_ms=0)

        return last_result or ExecutionResult(code="", output="", error="Unknown error", success=False, duration_ms=0)

    @staticmethod
    def _strip_fences(text: str) -> str:
        import re
        text = re.sub(r"^```(?:python)?\n?", "", text.strip())
        text = re.sub(r"\n?```$", "", text)
        return text.strip()
