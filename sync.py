"""
sync.py — Sincroniza playlists de YouTube Music a Spotify.
Usa YouTube Data API v3 oficial + requests directos a Spotify API.
"""

import json
import os
import sys
import time
import logging
import requests

import spotipy
from spotipy.oauth2 import SpotifyOAuth
from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ─── Autenticación YouTube ───────────────────────────────────────────────────

def get_youtube():
    auth_file = "ytmusic_auth.json"

    raw = os.environ.get("YTMUSIC_AUTH")
    if raw:
        with open(auth_file, "w", encoding="utf-8") as f:
            f.write(raw)

    if not os.path.exists(auth_file):
        log.error("No se encontró ytmusic_auth.json. Corre setup_ytmusic_web.py primero.")
        sys.exit(1)

    with open(auth_file) as f:
        token_data = json.load(f)

    creds = Credentials(
        token=token_data["access_token"],
        refresh_token=token_data["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=token_data["client_id"],
        client_secret=token_data["client_secret"],
        scopes=["https://www.googleapis.com/auth/youtube"],
    )

    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token_data["access_token"] = creds.token
        with open(auth_file, "w") as f:
            json.dump(token_data, f, indent=2)

    return build("youtube", "v3", credentials=creds)


# ─── Autenticación Spotify ───────────────────────────────────────────────────

def get_spotify():
    cache_path = ".spotify_cache"

    raw = os.environ.get("SPOTIFY_CACHE_TOKEN")
    if raw:
        with open(cache_path, "w", encoding="utf-8") as f:
            f.write(raw)

    auth_manager = SpotifyOAuth(
        client_id=os.environ["SPOTIFY_CLIENT_ID"],
        client_secret=os.environ["SPOTIFY_CLIENT_SECRET"],
        redirect_uri=os.environ.get("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback"),
        scope="playlist-modify-public playlist-modify-private playlist-read-private",
        cache_path=cache_path,
        open_browser=False,
    )
    return spotipy.Spotify(auth_manager=auth_manager)


# ─── Helpers Spotify (requests directos, sin spotipy para evitar params extra) ──

def sp_headers(sp):
    sp.auth_manager.validate_token(sp.auth_manager.get_cached_token())
    token = sp.auth_manager.get_cached_token()["access_token"]
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

def sp_get(sp, url, params=None):
    r = requests.get(url, headers=sp_headers(sp), params=params or {})
    r.raise_for_status()
    return r.json()


def sp_post(sp, url, payload):
    r = requests.post(url, headers=sp_headers(sp), json=payload)
    r.raise_for_status()
    return r.json()


def sp_put(sp, url, payload):
    r = requests.put(url, headers=sp_headers(sp), json=payload)
    r.raise_for_status()


def sp_delete(sp, url, payload):
    r = requests.delete(url, headers=sp_headers(sp), json=payload)
    r.raise_for_status()


# ─── Helpers YouTube ─────────────────────────────────────────────────────────

def get_youtube_tracks(youtube, playlist_id: str) -> list[dict]:
    tracks = []
    request = youtube.playlistItems().list(
        part="snippet",
        playlistId=playlist_id,
        maxResults=50,
    )
    while request:
        response = request.execute()
        for item in response.get("items", []):
            snippet = item["snippet"]
            title  = snippet.get("title", "")
            artist = snippet.get("videoOwnerChannelTitle", "").replace(" - Topic", "")
            if title and title != "Private video" and title != "Deleted video":
                tracks.append({"title": title, "artist": artist})
        request = youtube.playlistItems().list_next(request, response)
    return tracks


# ─── Helpers Spotify ─────────────────────────────────────────────────────────

def get_or_create_spotify_playlist(sp, name: str) -> str:
    offset = 0
    while True:
        result = sp_get(sp, "https://api.spotify.com/v1/me/playlists", {"limit": 50, "offset": offset})
        for pl in result["items"]:
            if pl["name"] == name:
                log.info(f"  Playlist encontrada: '{name}' → {pl['id']}")
                return pl["id"]
        if result["next"] is None:
            break
        offset += 50

        
    # Crear con POST /me/playlists (endpoint correcto feb 2026)
    pl = sp_post(sp, "https://api.spotify.com/v1/me/playlists", {
        "name": name,
        "public": False,
        "description": "Sincronizada desde YouTube Music",
    })
    log.info(f"  Playlist creada en Spotify: '{name}'")
    return pl["id"]


def get_spotify_tracks(sp, playlist_id: str) -> list[dict]:
    """Usa GET /playlists/{id}/items (endpoint correcto feb 2026)."""
    tracks = []
    offset = 0
    while True:
        result = sp_get(sp, f"https://api.spotify.com/v1/playlists/{playlist_id}/items",
                        {"limit": 100, "offset": offset})

        for item in result.get("items", []):
            t = item.get("item")

            if not t or not t.get("id"):
                continue
            artists = ", ".join(a["name"] for a in t.get("artists", []))
            tracks.append({"id": t["id"], "name": t["name"], "artist": artists})
        if result.get("next") is None:
            break
        offset += 100
    return tracks


def search_spotify_track(sp, title: str, artist: str) -> str | None:
    artist_clean = artist.replace(" - Topic", "").strip()
    query = f"track:{title} artist:{artist_clean}" if artist_clean else f"track:{title}"
    try:
        result = sp_get(sp, "https://api.spotify.com/v1/search",
                        {"q": query, "type": "track", "limit": 1})
        items = result["tracks"]["items"]
        if items:
            return items[0]["id"]
        # Segundo intento más simple
        result = sp_get(sp, "https://api.spotify.com/v1/search",
                        {"q": f"{title} {artist_clean}", "type": "track", "limit": 1})
        items = result["tracks"]["items"]
        return items[0]["id"] if items else None
    except Exception as e:
        log.warning(f"  Error buscando '{title}': {e}")
        return None


def add_tracks_to_playlist(sp, playlist_id: str, track_ids: list[str], position: int = None):
    """Agrega tracks en una posicion especifica."""
    base_url = f"https://api.spotify.com/v1/playlists/{playlist_id}/items"
    for i in range(0, len(track_ids), 100):
        chunk = [f"spotify:track:{tid}" for tid in track_ids[i:i+100]]
        payload = {"uris": chunk}
        if position is not None:
            payload["position"] = position + i
        sp_post(sp, base_url, payload)
        time.sleep(0.3)


def remove_tracks_from_playlist(sp, playlist_id: str, track_ids: list[str]):
    """Elimina tracks especificos de la playlist."""
    base_url = f"https://api.spotify.com/v1/playlists/{playlist_id}/items"
    for i in range(0, len(track_ids), 100):
        chunk = [{"uri": f"spotify:track:{tid}"} for tid in track_ids[i:i+100]]
        sp_delete(sp, base_url, {"tracks": chunk})
        time.sleep(0.3)


def reorder_playlist(sp, playlist_id: str, track_ids: list[str]):
    """Reemplaza el orden completo de la playlist."""
    base_url = f"https://api.spotify.com/v1/playlists/{playlist_id}/items"
    sp_put(sp, base_url, {"uris": []})
    for i in range(0, len(track_ids), 100):
        chunk = [f"spotify:track:{tid}" for tid in track_ids[i:i+100]]
        sp_post(sp, base_url, {"uris": chunk})
        time.sleep(0.3)


# ─── Sincronización ──────────────────────────────────────────────────────────

def sync_playlist(youtube, sp, ytm_id: str, spotify_name: str):
    log.info(f"▶ Sincronizando: '{spotify_name}'")

    # 1. Obtener tracks de YouTube Music (fuente de verdad)
    ytm_tracks = get_youtube_tracks(youtube, ytm_id)
    log.info(f"  YouTube Music: {len(ytm_tracks)} canciones")

    # 2. Obtener/crear playlist en Spotify
    sp_playlist_id = get_or_create_spotify_playlist(sp, spotify_name)
    sp_tracks = get_spotify_tracks(sp, sp_playlist_id)
    log.info(f"  Spotify actual: {len(sp_tracks)} canciones")

    # 3. Construir mapa de tracks ya conocidos en Spotify (nombre+artista -> id)
    sp_track_map = {(t["name"].lower(), t["artist"].lower()): t["id"] for t in sp_tracks}
    sp_ids_current = [t["id"] for t in sp_tracks]

    # 4. Resolver IDs de Spotify para cada track de YTM
    not_found = []
    new_track_ids = []  # Lista final en el orden de YTM

    for track in ytm_tracks:
        key = (track["title"].lower(), track["artist"].lower())
        if key in sp_track_map:
            new_track_ids.append(sp_track_map[key])
        else:
            spotify_id = search_spotify_track(sp, track["title"], track["artist"])
            if spotify_id:
                new_track_ids.append(spotify_id)
                sp_track_map[key] = spotify_id  # cachear para no buscar dos veces
            else:
                not_found.append(f"{track['artist']} — {track['title']}")
                log.warning(f"  ✗ No encontrada: {track['artist']} — {track['title']}")
            time.sleep(0.1)

    # 5. Comparar con estado actual
    if new_track_ids == sp_ids_current:
        log.info(f"  ✅ Sin cambios.")
        return

    # 6. Calcular diferencias
    new_set = set(new_track_ids)
    old_set = set(sp_ids_current)

    to_add    = [tid for tid in new_track_ids if tid not in old_set]
    to_remove = [tid for tid in sp_ids_current if tid not in new_set]
    order_changed = new_track_ids != [t for t in sp_ids_current if t in new_set]

    log.info(f"  + Agregar: {len(to_add)} | - Eliminar: {len(to_remove)} | ↕ Reordenar: {order_changed}")

    # 7. Aplicar cambios
    if to_remove:
        remove_tracks_from_playlist(sp, sp_playlist_id, to_remove)
        for tid in to_remove:
            log.info(f"  - Eliminada: {tid}")

    if to_add or order_changed:
        # Si hay canciones nuevas o el orden cambió, reordenamos todo
        reorder_playlist(sp, sp_playlist_id, new_track_ids)
        for tid in to_add:
            log.info(f"  + Agregada: {tid}")

    log.info(f"  ✅ Sincronización completa: {len(new_track_ids)} canciones")

    if not_found:
        log.warning(f"  ⚠ No encontradas en Spotify ({len(not_found)}):")
        for name in not_found:
            log.warning(f"    - {name}")


def main():
    if not os.path.exists("config.json"):
        log.error("No se encontró config.json. Corre setup_playlists.py primero.")
        sys.exit(1)

    with open("config.json", encoding="utf-8") as f:
        config = json.load(f)

    playlists = config.get("playlists", [])
    if not playlists:
        log.error("config.json no tiene playlists configuradas.")
        sys.exit(1)

    log.info(f"Iniciando sincronización de {len(playlists)} playlist(s)...")
    youtube = get_youtube()
    sp      = get_spotify()

    for pl in playlists:
        try:
            sync_playlist(youtube, sp, pl["youtube_music_id"], pl["spotify_name"])
        except Exception as e:
            log.error(f"Error sincronizando '{pl.get('spotify_name')}': {e}")

    log.info("✅ Sincronización completa.")


if __name__ == "__main__":
    main()