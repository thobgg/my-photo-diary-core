#!/bin/bash
# ═══════════════════════════════════════════════
# My Photo Diary — Docker Entrypoint
# ═══════════════════════════════════════════════

echo "════════════════════════════════════════════"
echo "  My Photo Diary v2"
echo "  $(date '+%Y-%m-%d %H:%M:%S')"
echo "════════════════════════════════════════════"
echo "🐍 Python: $(python3 --version 2>&1)"
echo "📦 Pillow: $(python3 -c 'from PIL import Image; print(Image.__version__)' 2>/dev/null || echo 'nicht installiert')"
echo "🎬 ffmpeg: $(ffmpeg -version 2>/dev/null | head -1 || echo 'nicht installiert')"
echo "🌐 Port:   ${MPD_PORT:-8089}"
echo "════════════════════════════════════════════"

# __pycache__ löschen (Volume-Mount kann stale .pyc enthalten)
find /app/backend -name "*.pyc" -delete 2>/dev/null
find /app/backend -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null
echo "🧹 __pycache__ bereinigt"

echo "🚀 Starte uvicorn..."
exec python3 -m uvicorn main:app \
    --host 0.0.0.0 \
    --port "${MPD_PORT:-8089}" \
    --proxy-headers \
    --log-level info
