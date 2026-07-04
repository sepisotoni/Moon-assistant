from __future__ import annotations

import discord

from bot.config import get_settings

settings = get_settings()


def has_owner_or_founder_role(member: discord.Member) -> bool:
    """True if the member has one of the configured Owner/Founder roles, or is a server admin."""
    role_names = {role.name.strip().lower() for role in member.roles}
    return bool(role_names & settings.owner_role_name_set) or member.guild_permissions.administrator


def get_member_visible_channel_ids(guild: discord.Guild, member: discord.Member) -> set[int]:
    """All text channel / thread IDs in this guild that the member can actually view.

    Used to scope /search results so a member can never see content from a
    channel they don't have access to.
    """
    visible: set[int] = set()

    for channel in guild.text_channels:
        try:
            if channel.permissions_for(member).view_channel:
                visible.add(channel.id)
        except Exception:
            continue

    for thread in guild.threads:
        try:
            if thread.permissions_for(member).view_channel:
                visible.add(thread.id)
        except Exception:
            continue

    return visible
