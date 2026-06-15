"""
setup_spotify.py — Genera .spotify_cache mediante OAuth de Spotify.

Corre esto UNA VEZ en tu PC local.
"""

import os
import spotipy
from dotenv import load_dotenv
from spotipy.oauth2 import SpotifyOAuth

load_dotenv()

CLIENT_ID     = os.environ.get("SPOTIFY_CLIENT_ID")
CLIENT_SECRET = os.environ.get("SPOTIFY_CLIENT_SECRET")
REDIRECT_URI  = os.environ.get("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback")

if not CLIENT_ID or not CLIENT_SECRET:
    print("❌ Faltan credenciales. Crea un archivo .env con:")
    print("   SPOTIFY_CLIENT_ID=...")
    print("   SPOTIFY_CLIENT_SECRET=...")
    raise SystemExit(1)

print("🔐 Iniciando flujo OAuth de Spotify...")
print(f"   Redirect URI: {REDIRECT_URI}")
print("   Asegúrate de que esa URI esté registrada en tu Spotify Developer Dashboard.\n")

auth_manager = SpotifyOAuth(
    client_id=CLIENT_ID,
    client_secret=CLIENT_SECRET,
    redirect_uri=REDIRECT_URI,
    scope="playlist-modify-public playlist-modify-private playlist-read-private",
    cache_path=".spotify_cache",
    open_browser=True,
)

sp = spotipy.Spotify(auth_manager=auth_manager)
user = sp.me()

print(f"\n✅ Autenticado como: {user['display_name']} ({user['id']})")
print("   .spotify_cache generado correctamente.")
print("   Guarda su contenido como secret SPOTIFY_CACHE_TOKEN en GitHub.")
