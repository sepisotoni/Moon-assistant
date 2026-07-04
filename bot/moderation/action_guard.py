from __future__ import annotations

"""Hard, code-level safety invariants for moderation actions.

These constants and checks are deliberately NOT sourced from the database or .env: they are the
last line of defense the spec asks for ("hardcode this safeguard"). Even if the constitution
table is misconfigured, the AI hallucinates, or a future contributor adds a new caller, these
checks make it structurally impossible to:

  - apply a timeout longer than ABSOLUTE_MAX_TIMEOUT_MINUTES
  - kick a member (no function in this module performs a kick, period)
  - ban a member (no function in this module performs a ban, period)
  - change a permission/role (no function in this module touches permissions, period)

bot/moderation/service.py (Phase 1) and bot/moderation/intelligence_service.py (Phase 2/3) both
route every timeout through `clamp_timeout_minutes` below rather than trusting their caller.
"""

# Absolute ceiling. This is intentionally a Python constant, not a Settings field, so it cannot
# be raised via .env, database config, or any runtime code path.
ABSOLUTE_MAX_TIMEOUT_MINUTES: int = 60


class ForbiddenActionError(RuntimeError):
    """Raised if any code path ever attempts an action this bot must never perform."""


def clamp_timeout_minutes(requested_minutes: int, configured_max: int) -> int:
    """Clamp a requested timeout to the lesser of the server config and the absolute ceiling.

    Never raises for valid positive input -- it clamps rather than rejects, because callers
    (including AI-recommended actions) should never be able to silently bypass this by asking
    for an out-of-range number; they always get a safe value back.
    """
    if requested_minutes <= 0:
        raise ValueError("Timeout must be a positive number of minutes.")
    effective_max = min(configured_max, ABSOLUTE_MAX_TIMEOUT_MINUTES)
    return min(requested_minutes, effective_max)


def assert_action_allowed(action_name: str) -> None:
    """Defense in depth: explicitly reject any action name resembling kick/ban/permission-change.

    Call this at the top of any new automated-action code path as a guard against an action type
    being added by mistake in the future (e.g. a copy-pasted ModerationActionType case).
    """
    forbidden_markers = ("kick", "ban", "permission", "role_grant", "role_revoke")
    normalized = action_name.lower()
    if any(marker in normalized for marker in forbidden_markers):
        raise ForbiddenActionError(
            f"Action '{action_name}' is permanently disabled for this bot (kicks, bans, and "
            "permission changes are never allowed, automated or otherwise)."
        )
