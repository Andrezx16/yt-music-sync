"""
setup_playlists.py — Lista tus playlists de YouTube Music y genera config.json.
Usa YouTube Data API v3 oficial.
"""

import json
import os
from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

load_dotenv()

AUTH_FILE = "ytmusic_auth.json"

if not os.path.exists(AUTH_FILE):
    print(f"❌ No se encontró {AUTH_FILE}. Corre setup_ytmusic_web.py primero.")
    raise SystemExit(1)

with open(AUTH_FILE) as f:
    token_data = json.load(f)

creds = Credentials(
    token=token_data["access_token"],
    refresh_token=token_data["refresh_token"],
    token_uri="https://oauth2.googleapis.com/token",
    client_id=token_data["client_id"],
    client_secret=token_data["client_secret"],
    scopes=["https://www.googleapis.com/auth/youtube"],
)

print("📋 Conectando a YouTube Music...\n")

youtube = build("youtube", "v3", credentials=creds)

# Obtener todas las playlists del usuario
playlists = []
request = youtube.playlists().list(part="snippet", mine=True, maxResults=50)

while request:
    response = request.execute()
    for item in response.get("items", []):
        playlists.append({
            "id": item["id"],
            "title": item["snippet"]["title"],
        })
    request = youtube.playlists().list_next(request, response)

if not playlists:
    print("⚠ No se encontraron playlists en tu cuenta de YouTube.")
    raise SystemExit(1)

print(f"{'#':<4} {'Nombre':<45} {'ID'}")
print("─" * 80)
for i, pl in enumerate(playlists):
    print(f"{i:<4} {pl['title'][:44]:<45} {pl['id']}")

print("\n¿Cuáles quieres sincronizar?")
print("Escribe los números separados por comas (ej: 0,2,5) o 'all' para todas:\n")

choice = input("> ").strip()

if choice.lower() == "all":
    selected = playlists
else:
    indices = [int(x.strip()) for x in choice.split(",")]
    selected = [playlists[i] for i in indices]

config = {"playlists": []}
print()
for pl in selected:
    default_name = pl["title"]
    print(f"Nombre en Spotify para '{pl['title']}' (Enter para usar el mismo):")
    spotify_name = input(f"  [{default_name}] > ").strip() or default_name
    config["playlists"].append({
        "youtube_music_id": pl["id"],
        "spotify_name": spotify_name,
    })

with open("config.json", "w", encoding="utf-8") as f:
    json.dump(config, f, ensure_ascii=False, indent=2)

print(f"\n✅ config.json generado con {len(config['playlists'])} playlist(s):")
for pl in config["playlists"]:
    print(f"   • {pl['spotify_name']}  ←  {pl['youtube_music_id']}")
