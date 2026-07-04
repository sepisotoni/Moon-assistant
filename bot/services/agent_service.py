from __future__ import annotations

"""AgentService — lets /ask execute moderation actions from natural language.

The AI parses the request into a list of structured actions, which are then
executed one by one. Only warn / timeout / delete_message / bulk_delete are
allowed. Kick, ban, and permission changes are permanently disabled.
"""

import json
import logging
import re
from dataclasses import dataclass, field

import discord

from bot.ai.base import AIMessage
from bot.ai.orchestrator import AIOrchestrator
from bot.config import get_settings
from bot.moderation.action_guard import assert_action_allowed, clamp_timeout_minutes
from bot.moderation.service import ModerationService

logger = logging.getLogger(__name__)
settings = get_settings()

_ACTION_KEYWORDS = re.compile(
    r"\b(delete|remove|purge|timeout|mute|warn|kick|ban|silence|shut\s*up|bulk)\b",
    re.IGNORECASE,
)

_AGENT_SYSTEM = """You are a Discord moderation assistant that turns natural language requests
into a list of moderation actions. You can ONLY use these action types:
  - warn          → warn a member
  - timeout       → timeout a member (max 60 minutes)
  - delete_message → delete a specific message by ID
  - bulk_delete   → delete last N messages from a member in this channel (max 100)

NEVER suggest kick or ban — those are permanently disabled and do not exist.
If asked to kick or ban, include a "kick_ban_refused" action explaining this.

Respond ONLY with a JSON array of actions, no other text. Each action:
  {"action": "<type>", "user_id": <int or null>, "reason": "<str>",
   "minutes": <int 1-60 or null>, "message_id": <int or null>, "count": <int or null>}

Examples:
  Request: "warn @Dave for spamming"
  Response: [{"action":"warn","user_id":123,"reason":"spamming","minutes":null,"message_id":null,"count":null}]

  Request: "delete that message and timeout him 30 mins"
  Response: [{"action":"delete_message","user_id":null,"reason":"requested by staff","minutes":null,"message_id":456,"count":null},
             {"action":"timeout","user_id":789,"reason":"requested by staff","minutes":30,"message_id":null,"count":null}]

  Request: "delete his last 20 messages"
  Response: [{"action":"bulk_delete","user_id":123,"reason":"staff request","minutes":null,"message_id":null,"count":20}]

  Request: "kick him"
  Response: [{"action":"kick_ban_refused","user_id":null,"reason":"Kick/ban are permanently disabled for this bot. Please do it manually in Discord.","minutes":null,"message_id":null,"count":null}]
"""


@dataclass
class ActionResult:
    action: str
    success: bool
    message: str


@dataclass
class AgentResult:
    response_text: str
    actions_taken: list[ActionResult] = field(default_factory=list)
    any_executed: bool = False


class AgentService:
    """Parses natural language moderation requests and executes allowed actions."""

    def __init__(self, orchestrator: AIOrchestrator) -> None:
        self._orchestrator = orchestrator
        self._moderation = ModerationService()

    def looks_like_action_request(self, text: str) -> bool:
        """Quick check — does this message seem to be asking for a moderation action?"""
        return bool(_ACTION_KEYWORDS.search(text))

    async def execute(
        self,
        *,
        guild: discord.Guild,
        channel: discord.TextChannel,
        requester: discord.Member,
        text: str,
        replied_message: discord.Message | None = None,
    ) -> AgentResult:
        # Resolve context mentions so the AI has IDs to work with.
        context_lines = [f"Request: {text}"]
        if replied_message:
            context_lines.append(
                f"Replied-to message: id={replied_message.id} "
                f"author_id={replied_message.author.id} "
                f"author={replied_message.author.display_name} "
                f"content={replied_message.content[:200]}"
            )
        # Extract mentioned members from the text itself.
        mentioned_ids = [m.id for m in guild.members
                         if m.mention in text or m.display_name.lower() in text.lower()]
        if mentioned_ids:
            context_lines.append(f"Mentioned user IDs found in text: {mentioned_ids}")

        messages = [
            AIMessage(role="system", content=_AGENT_SYSTEM),
            AIMessage(role="user", content="\n".join(context_lines)),
        ]

        try:
            decision = await self._orchestrator.generate_for_task(
                "moderation_review",
                messages,
                guild_id=guild.id,
                dual_review=False,
                requested_by=requester.id,
                input_summary=text,
            )
            actions = self._parse_actions(decision.text)
        except Exception as exc:
            logger.exception("AgentService: AI parse failed")
            return AgentResult(response_text=f"Sorry, I couldn't parse that request: {exc}")

        results: list[ActionResult] = []
        for action_data in actions:
            result = await self._execute_one(
                action_data, guild=guild, channel=channel,
                requester=requester, replied_message=replied_message,
            )
            results.append(result)

        executed = [r for r in results if r.success]
        refused  = [r for r in results if not r.success]

        lines = []
        for r in results:
            icon = "✅" if r.success else "⚠️"
            lines.append(f"{icon} {r.message}")

        return AgentResult(
            response_text="\n".join(lines) if lines else "No actions were taken.",
            actions_taken=results,
            any_executed=bool(executed),
        )

    def _parse_actions(self, text: str) -> list[dict]:
        cleaned = re.sub(r"```(?:json)?|```", "", text).strip()
        try:
            data = json.loads(cleaned)
            return data if isinstance(data, list) else [data]
        except json.JSONDecodeError:
            logger.warning("AgentService: could not parse action JSON: %r", text)
            return []

    async def _execute_one(
        self,
        data: dict,
        *,
        guild: discord.Guild,
        channel: discord.TextChannel,
        requester: discord.Member,
        replied_message: discord.Message | None,
    ) -> ActionResult:
        action = data.get("action", "")
        reason = data.get("reason") or "Requested via /ask"
        user_id = data.get("user_id")
        minutes = data.get("minutes")
        message_id = data.get("message_id")
        count = data.get("count") or 10

        # Hard safety gate — will raise for kick/ban/permission keywords.
        try:
            assert_action_allowed(action)
        except Exception:
            return ActionResult(
                action=action, success=False,
                message=f"**{action}** is permanently disabled. Please do it manually in Discord.",
            )

        if action == "kick_ban_refused":
            return ActionResult(
                action=action, success=False,
                message=reason,
            )

        # Resolve target member.
        target: discord.Member | None = None
        if user_id:
            target = guild.get_member(int(user_id))
        elif replied_message:
            target = guild.get_member(replied_message.author.id)

        if action == "warn":
            if not target:
                return ActionResult(action=action, success=False, message="warn: couldn't find the target member.")
            try:
                await self._moderation.warn(
                    guild=guild, member=target, moderator=requester, reason=reason
                )
                return ActionResult(action=action, success=True,
                                    message=f"Warned {target.mention} — {reason}")
            except Exception as e:
                return ActionResult(action=action, success=False, message=f"warn failed: {e}")

        elif action == "timeout":
            if not target:
                return ActionResult(action=action, success=False, message="timeout: couldn't find the target member.")
            try:
                safe_minutes = clamp_timeout_minutes(int(minutes or 10), settings.max_timeout_minutes)
                await self._moderation.timeout(
                    member=target, moderator=requester, minutes=safe_minutes, reason=reason
                )
                return ActionResult(action=action, success=True,
                                    message=f"Timed out {target.mention} for {safe_minutes} min — {reason}")
            except Exception as e:
                return ActionResult(action=action, success=False, message=f"timeout failed: {e}")

        elif action == "delete_message":
            msg_id = message_id or (replied_message.id if replied_message else None)
            if not msg_id:
                return ActionResult(action=action, success=False,
                                    message="delete_message: no message ID — reply to the message you want deleted.")
            try:
                msg = await channel.fetch_message(int(msg_id))
                await self._moderation.delete_message(message=msg, moderator=requester, reason=reason)
                return ActionResult(action=action, success=True, message=f"Deleted message `{msg_id}`.")
            except discord.NotFound:
                return ActionResult(action=action, success=False, message="delete_message: message not found.")
            except Exception as e:
                return ActionResult(action=action, success=False, message=f"delete_message failed: {e}")

        elif action == "bulk_delete":
            if not target and not user_id:
                return ActionResult(action=action, success=False,
                                    message="bulk_delete: specify a member to delete messages from.")
            safe_count = max(1, min(int(count), 100))
            try:
                target_id = int(user_id) if user_id else (replied_message.author.id if replied_message else None)
                deleted = await channel.purge(
                    limit=safe_count * 3,  # scan more to find target's messages
                    check=lambda m: m.author.id == target_id and not m.pinned,
                    bulk=True,
                )
                actual = len(deleted)
                name = target.display_name if target else f"<@{target_id}>"
                return ActionResult(action=action, success=True,
                                    message=f"Bulk deleted {actual} message(s) from {name}.")
            except Exception as e:
                return ActionResult(action=action, success=False, message=f"bulk_delete failed: {e}")

        return ActionResult(action=action, success=False, message=f"Unknown action: {action}")
