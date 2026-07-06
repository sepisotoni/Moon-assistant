from __future__ import annotations

"""MusicCog — YouTube/URL audio playback with queue, skip, pause, volume, and now-playing.

Requirements (install before using):
    pip install yt-dlp
    pip install discord.py[voice] PyNaCl
    sudo apt-get install -y ffmpeg

Legal note: only play content you have the right to play. This cog streams audio
directly from URLs/YouTube — no files are stored on disk permanently.
"""

import asyncio
import logging
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Deque

import discord
from discord import app_commands
from discord.ext import commands

logger = logging.getLogger(__name__)

FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",  # no video
}

YDL_FORMAT_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",
    "extract_flat": False,
}


@dataclass
class Track:
    title: str
    url: str          # direct stream URL
    webpage_url: str  # original YouTube/source URL
    duration: int     # seconds
    requester: discord.Member

    @property
    def duration_str(self) -> str:
        m, s = divmod(self.duration, 60)
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


@dataclass
class GuildPlayer:
    queue: Deque[Track] = field(default_factory=deque)
    current: Track | None = None
    volume: float = 0.5
    loop: bool = False
    paused: bool = False


class MusicCog(commands.Cog, name="Music"):
    """Full-featured music player: play, queue, skip, pause, volume, loop, now-playing."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._players: dict[int, GuildPlayer] = defaultdict(GuildPlayer)

    def _player(self, guild_id: int) -> GuildPlayer:
        return self._players[guild_id]

    async def _ensure_voice(self, interaction: discord.Interaction) -> discord.VoiceClient | None:
        """Connect to the user's voice channel, or return the existing connection."""
        if not isinstance(interaction.user, discord.Member):
            return None
        voice_state = interaction.user.voice
        if voice_state is None or voice_state.channel is None:
            await interaction.followup.send("❌ You need to be in a voice channel first.", ephemeral=True)
            return None

        guild = interaction.guild
        vc: discord.VoiceClient | None = guild.voice_client  # type: ignore[union-attr]

        if vc is None:
            vc = await voice_state.channel.connect()
        elif vc.channel != voice_state.channel:
            await vc.move_to(voice_state.channel)

        return vc

    async def _fetch_track(self, query: str, requester: discord.Member) -> Track | None:
        """Resolve a search query or URL to a playable Track using yt-dlp."""
        try:
            import yt_dlp  # noqa: PLC0415
        except ImportError:
            return None

        loop = asyncio.get_event_loop()

        def _extract():
            with yt_dlp.YoutubeDL(YDL_FORMAT_OPTIONS) as ydl:
                # If not a URL, treat as a search.
                if not query.startswith("http"):
                    info = ydl.extract_info(f"ytsearch:{query}", download=False)
                    if "entries" in info:
                        info = info["entries"][0]
                else:
                    info = ydl.extract_info(query, download=False)
                return info

        try:
            info = await loop.run_in_executor(None, _extract)
            return Track(
                title=info.get("title", "Unknown"),
                url=info["url"],
                webpage_url=info.get("webpage_url", query),
                duration=info.get("duration", 0),
                requester=requester,
            )
        except Exception as exc:
            logger.warning("yt-dlp failed for query %r: %s", query, exc)
            return None

    def _play_next(self, guild: discord.Guild, vc: discord.VoiceClient) -> None:
        """Internal: play the next track in the queue."""
        player = self._player(guild.id)

        if player.loop and player.current is not None:
            next_track = player.current
        elif player.queue:
            next_track = player.queue.popleft()
        else:
            player.current = None
            asyncio.run_coroutine_threadsafe(self._idle_disconnect(vc), self.bot.loop)
            return

        player.current = next_track

        source = discord.FFmpegPCMAudio(next_track.url, **FFMPEG_OPTIONS)
        source = discord.PCMVolumeTransformer(source, volume=player.volume)

        def after(error):
            if error:
                logger.error("Player error: %s", error)
            self._play_next(guild, vc)

        vc.play(source, after=after)

    async def _idle_disconnect(self, vc: discord.VoiceClient, delay: int = 300) -> None:
        """Disconnect after `delay` seconds of silence."""
        await asyncio.sleep(delay)
        if vc.is_connected() and not vc.is_playing():
            await vc.disconnect()

    # ------------------------------------------------------------------
    # /play
    # ------------------------------------------------------------------
    @app_commands.command(name="play", description="Play a song from YouTube or a URL.")
    @app_commands.describe(query="Song name or YouTube URL")
    async def play(self, interaction: discord.Interaction, query: str) -> None:
        await interaction.response.defer(thinking=True)

        vc = await self._ensure_voice(interaction)
        if vc is None:
            return

        track = await self._fetch_track(query, interaction.user)  # type: ignore[arg-type]
        if track is None:
            await interaction.followup.send(
                "❌ Couldn't find that track. Make sure `yt-dlp` and `ffmpeg` are installed.",
                ephemeral=True,
            )
            return

        player = self._player(interaction.guild_id)  # type: ignore[arg-type]

        if vc.is_playing() or vc.is_paused():
            player.queue.append(track)
            embed = discord.Embed(
                title="➕ Added to queue",
                description=f"**[{track.title}]({track.webpage_url})**",
                color=discord.Color.blurple(),
            )
            embed.add_field(name="Duration", value=track.duration_str)
            embed.add_field(name="Position", value=f"#{len(player.queue)}")
            await interaction.followup.send(embed=embed)
        else:
            player.current = track
            source = discord.FFmpegPCMAudio(track.url, **FFMPEG_OPTIONS)
            source = discord.PCMVolumeTransformer(source, volume=player.volume)

            def after(error):
                if error:
                    logger.error("Player error: %s", error)
                self._play_next(interaction.guild, vc)  # type: ignore[arg-type]

            vc.play(source, after=after)

            embed = discord.Embed(
                title="🎵 Now playing",
                description=f"**[{track.title}]({track.webpage_url})**",
                color=discord.Color.green(),
            )
            embed.add_field(name="Duration", value=track.duration_str)
            embed.add_field(name="Requested by", value=track.requester.display_name)
            await interaction.followup.send(embed=embed)

    # ------------------------------------------------------------------
    # /skip
    # ------------------------------------------------------------------
    @app_commands.command(name="skip", description="Skip the current song.")
    async def skip(self, interaction: discord.Interaction) -> None:
        vc: discord.VoiceClient | None = interaction.guild.voice_client  # type: ignore[union-attr]
        if vc is None or not vc.is_playing():
            await interaction.response.send_message("Nothing is playing.", ephemeral=True)
            return
        vc.stop()  # triggers after() → _play_next()
        await interaction.response.send_message("⏭️ Skipped!")

    # ------------------------------------------------------------------
    # /pause / /resume
    # ------------------------------------------------------------------
    @app_commands.command(name="pause", description="Pause the current song.")
    async def pause(self, interaction: discord.Interaction) -> None:
        vc: discord.VoiceClient | None = interaction.guild.voice_client  # type: ignore[union-attr]
        if vc and vc.is_playing():
            vc.pause()
            self._player(interaction.guild_id).paused = True  # type: ignore[arg-type]
            await interaction.response.send_message("⏸️ Paused.")
        else:
            await interaction.response.send_message("Nothing to pause.", ephemeral=True)

    @app_commands.command(name="resume", description="Resume the paused song.")
    async def resume(self, interaction: discord.Interaction) -> None:
        vc: discord.VoiceClient | None = interaction.guild.voice_client  # type: ignore[union-attr]
        if vc and vc.is_paused():
            vc.resume()
            self._player(interaction.guild_id).paused = False  # type: ignore[arg-type]
            await interaction.response.send_message("▶️ Resumed.")
        else:
            await interaction.response.send_message("Nothing is paused.", ephemeral=True)

    # ------------------------------------------------------------------
    # /stop
    # ------------------------------------------------------------------
    @app_commands.command(name="stop", description="Stop music and clear the queue.")
    async def stop(self, interaction: discord.Interaction) -> None:
        guild_id = interaction.guild_id  # type: ignore[assignment]
        vc: discord.VoiceClient | None = interaction.guild.voice_client  # type: ignore[union-attr]
        player = self._player(guild_id)
        player.queue.clear()
        player.current = None
        if vc:
            vc.stop()
            await vc.disconnect()
        await interaction.response.send_message("⏹️ Stopped and queue cleared.")

    # ------------------------------------------------------------------
    # /queue
    # ------------------------------------------------------------------
    @app_commands.command(name="queue", description="Show the current music queue.")
    async def queue(self, interaction: discord.Interaction) -> None:
        player = self._player(interaction.guild_id)  # type: ignore[arg-type]

        if player.current is None and not player.queue:
            await interaction.response.send_message("The queue is empty.", ephemeral=True)
            return

        embed = discord.Embed(title="🎵 Music Queue", color=discord.Color.blurple())

        if player.current:
            embed.add_field(
                name="▶️ Now Playing",
                value=f"**[{player.current.title}]({player.current.webpage_url})** ({player.current.duration_str})",
                inline=False,
            )

        if player.queue:
            lines = []
            for i, track in enumerate(list(player.queue)[:10], 1):
                lines.append(f"`{i}.` [{track.title}]({track.webpage_url}) ({track.duration_str})")
            if len(player.queue) > 10:
                lines.append(f"...and {len(player.queue) - 10} more")
            embed.add_field(name="📋 Up Next", value="\n".join(lines), inline=False)

        loop_status = "🔁 Loop: ON" if player.loop else "🔁 Loop: OFF"
        embed.set_footer(text=f"{loop_status} · Volume: {int(player.volume * 100)}%")
        await interaction.response.send_message(embed=embed)

    # ------------------------------------------------------------------
    # /volume
    # ------------------------------------------------------------------
    @app_commands.command(name="volume", description="Set the volume (0–100).")
    @app_commands.describe(level="Volume level (0-100)")
    async def volume(self, interaction: discord.Interaction,
                     level: app_commands.Range[int, 0, 100]) -> None:
        player = self._player(interaction.guild_id)  # type: ignore[arg-type]
        player.volume = level / 100

        vc: discord.VoiceClient | None = interaction.guild.voice_client  # type: ignore[union-attr]
        if vc and vc.source and isinstance(vc.source, discord.PCMVolumeTransformer):
            vc.source.volume = player.volume

        await interaction.response.send_message(f"🔊 Volume set to **{level}%**.")

    # ------------------------------------------------------------------
    # /loop
    # ------------------------------------------------------------------
    @app_commands.command(name="loop", description="Toggle looping the current song.")
    async def loop(self, interaction: discord.Interaction) -> None:
        player = self._player(interaction.guild_id)  # type: ignore[arg-type]
        player.loop = not player.loop
        state = "🔁 enabled" if player.loop else "➡️ disabled"
        await interaction.response.send_message(f"Loop {state}.")

    # ------------------------------------------------------------------
    # /nowplaying
    # ------------------------------------------------------------------
    @app_commands.command(name="nowplaying", description="Show what's currently playing.")
    async def nowplaying(self, interaction: discord.Interaction) -> None:
        player = self._player(interaction.guild_id)  # type: ignore[arg-type]
        if player.current is None:
            await interaction.response.send_message("Nothing is playing right now.", ephemeral=True)
            return
        t = player.current
        embed = discord.Embed(
            title="🎵 Now Playing",
            description=f"**[{t.title}]({t.webpage_url})**",
            color=discord.Color.green(),
        )
        embed.add_field(name="Duration", value=t.duration_str)
        embed.add_field(name="Requested by", value=t.requester.display_name)
        embed.add_field(name="Volume", value=f"{int(player.volume * 100)}%")
        embed.add_field(name="Loop", value="ON" if player.loop else "OFF")
        await interaction.response.send_message(embed=embed)

    # ------------------------------------------------------------------
    # /remove — remove a specific track from queue
    # ------------------------------------------------------------------
    @app_commands.command(name="remove", description="Remove a track from the queue by position.")
    @app_commands.describe(position="Queue position (1 = next up)")
    async def remove(self, interaction: discord.Interaction,
                     position: app_commands.Range[int, 1, 100]) -> None:
        player = self._player(interaction.guild_id)  # type: ignore[arg-type]
        if not player.queue or position > len(player.queue):
            await interaction.response.send_message("Invalid position.", ephemeral=True)
            return
        queue_list = list(player.queue)
        removed = queue_list.pop(position - 1)
        player.queue = deque(queue_list)
        await interaction.response.send_message(f"🗑️ Removed **{removed.title}** from the queue.")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MusicCog(bot))
