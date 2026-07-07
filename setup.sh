#!/usr/bin/env bash
# =============================================================================
# Moon-Assistant Setup Script
# Run this once in a fresh Codespace: bash setup.sh
# =============================================================================

set -e  # exit on any error

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║     Moon-Assistant — Setup Script        ║"
echo "╚══════════════════════════════════════════╝"
echo ""

# -----------------------------------------------------------------------------
# 1. Python dependencies
# -----------------------------------------------------------------------------
echo "📦 Installing Python dependencies..."
pip install -q -r requirements.txt
pip install -q yt-dlp PyNaCl "discord.py[voice]"
echo "✅ Python dependencies installed."

# -----------------------------------------------------------------------------
# 2. FFmpeg (static build — apt-get is blocked in Codespaces)
# -----------------------------------------------------------------------------
echo ""
echo "🎵 Installing FFmpeg (static build)..."
if [ -f "$HOME/.local/bin/ffmpeg" ]; then
    echo "   Already installed — skipping."
else
    cd /tmp
    curl -sL https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz | tar -xJ
    mkdir -p "$HOME/.local/bin"
    cp ffmpeg-master-latest-linux64-gpl/bin/ffmpeg "$HOME/.local/bin/"
    cp ffmpeg-master-latest-linux64-gpl/bin/ffprobe "$HOME/.local/bin/"
    rm -rf /tmp/ffmpeg-master-latest-linux64-gpl
    cd - > /dev/null
    echo "✅ FFmpeg installed to ~/.local/bin/"
fi

# Add ~/.local/bin to PATH permanently
if ! grep -q 'HOME/.local/bin' "$HOME/.bashrc" 2>/dev/null; then
    echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.bashrc"
fi
export PATH="$HOME/.local/bin:$PATH"

# -----------------------------------------------------------------------------
# 3. .env file
# -----------------------------------------------------------------------------
echo ""
echo "⚙️  Setting up .env file..."
if [ -f ".env" ]; then
    echo "   .env already exists — skipping. Edit it manually if needed."
else
    cp .env.example .env
    echo "✅ Created .env from .env.example"
    echo ""
    echo "   ⚠️  YOU MUST EDIT .env BEFORE STARTING THE BOT:"
    echo "   nano .env"
    echo ""
    echo "   Required values:"
    echo "     DISCORD_TOKEN    — from discord.com/developers/applications"
    echo "     DATABASE_URL     — your Postgres connection string"
    echo "     OPENROUTER_API_KEY — from openrouter.ai"
    echo "     GEMINI_API_KEY   — from aistudio.google.com (starts with AIzaSy)"
fi

# -----------------------------------------------------------------------------
# 4. Postgres via Docker
# -----------------------------------------------------------------------------
echo ""
echo "🐘 Starting Postgres (Docker)..."
if command -v docker &>/dev/null; then
    if docker compose ps 2>/dev/null | grep -q "db"; then
        echo "   Already running — skipping."
    else
        docker compose up -d db
        echo -n "   Waiting for Postgres to be ready"
        for i in $(seq 1 20); do
            if docker compose exec -T db pg_isready -U bot_user -d discord_ai_bot &>/dev/null 2>&1; then
                echo " ✅"
                break
            fi
            echo -n "."
            sleep 2
        done
    fi
else
    echo "   ⚠️  Docker not found. Start Postgres manually and set DATABASE_URL in .env"
fi

# -----------------------------------------------------------------------------
# 5. Database migrations
# -----------------------------------------------------------------------------
echo ""
echo "🗄️  Running database migrations..."
if python -m alembic upgrade head 2>&1; then
    echo "✅ Migrations complete."
else
    echo "   Migration failed — attempting clean reset (drops and recreates DB)..."
    docker compose exec -T db psql -U bot_user -c "DROP DATABASE IF EXISTS discord_ai_bot;" 2>/dev/null || true
    docker compose exec -T db psql -U bot_user -c "CREATE DATABASE discord_ai_bot;" 2>/dev/null || true
    if python -m alembic upgrade head 2>&1; then
        echo "✅ Migrations complete after clean reset."
    else
        echo "⚠️  Migrations still failing — check DATABASE_URL in .env and try manually:"
        echo "    python -m alembic upgrade head"
    fi
fi

# -----------------------------------------------------------------------------
# 6. Verify setup
# -----------------------------------------------------------------------------
echo ""
echo "🔍 Verifying setup..."
python3 -c "
import importlib.util, sys
missing = []
for pkg in ['discord', 'sqlalchemy', 'alembic', 'asyncpg', 'pydantic_settings', 'httpx', 'yt_dlp', 'nacl']:
    if importlib.util.find_spec(pkg) is None:
        missing.append(pkg)
if missing:
    print('  ❌ Missing packages:', ', '.join(missing))
    sys.exit(1)
else:
    print('  ✅ All packages present')
"

FFMPEG_OK="❌ not found — music commands won't work"
[ -f "$HOME/.local/bin/ffmpeg" ] && FFMPEG_OK="✅ $HOME/.local/bin/ffmpeg"
echo "  FFmpeg: $FFMPEG_OK"

ENV_OK="❌ .env file missing"
[ -f ".env" ] && ENV_OK="✅ .env exists"
echo "  .env: $ENV_OK"

# -----------------------------------------------------------------------------
# Done
# -----------------------------------------------------------------------------
echo ""
echo "╔══════════════════════════════════════════╗"
echo "║           Setup Complete! 🎉              ║"
echo "╚══════════════════════════════════════════╝"
echo ""
echo "Next steps:"
echo "  1. Edit .env with your tokens:   nano .env"
echo "  2. Start the bot:                python -m bot"
echo ""
echo "Useful commands:"
echo "  python -m bot                    — start the bot"
echo "  python -m alembic upgrade head  — run DB migrations"
echo "  docker compose up -d db         — start Postgres"
echo "  docker compose logs -f db       — view Postgres logs"
echo ""
