FROM python:3.11-slim

# System-Dependencies: Pillow (libjpeg), ffmpeg (Video-Thumbnails),
# tzdata (Zeitzonen).
#
# tzdata ist nicht kosmetisch: Ohne die Zonendaten löst ein TZ wie
# "Europe/Berlin" nicht auf, und die Uhr des Containers bleibt still auf
# UTC stehen — ohne Fehlermeldung. Genau das ist bis 09/2026 im
# Synology-Paket passiert: alle Zeitpläne liefen zwei Stunden zu spät
# ("Heute vor X Jahren" um 08:30 statt 06:30). Compose setzt TZ, das
# Paket bekommt sie über entry.sh aus der DSM-Einstellung.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        libjpeg62-turbo \
        zlib1g \
        ffmpeg \
        tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python-Dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App-Code
COPY backend/ ./backend/
COPY frontend/ ./frontend/
# Developer handbook — served admin-gated via /admin/handbook (settings.py)
COPY docs/handbook.html ./docs/handbook.html

# Kein mpd.env im Image — kommt via Volume/Mount

EXPOSE 8089

# Entrypoint: Cache-Cleanup + Startup-Banner + uvicorn
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# Starte aus backend/, damit Imports stimmen
WORKDIR /app/backend

ENTRYPOINT ["/app/entrypoint.sh"]
