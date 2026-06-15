"""
sync.py — Sincroniza playlists de YouTube Music a Spotify.
Usa YouTube Data API v3 oficial + requests directos a Spotify API.
"""

import json
import os
import sys
import time
import random
import logging
import requests


class SpotifyRateLimitError(Exception):
    """Spotify impuso un rate limit demasiado largo — abortar todas las búsquedas."""
    pass

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

# ─── Cache de búsquedas Spotify ──────────────────────────────────────────────
# Persiste entre ejecuciones. Guarda también resultados negativos (None).
# Formato: { "title::artist": "spotify_id" | null }

SEARCH_CACHE_FILE = "spotify_search_cache.json"
_search_cache: dict = {}  # cargado una vez en main(), actúa también como memoria temporal


def load_search_cache() -> None:
    global _search_cache
    if os.path.exists(SEARCH_CACHE_FILE):
        with open(SEARCH_CACHE_FILE, encoding="utf-8") as f:
            _search_cache = json.load(f)
        log.info(f"Cache cargado: {len(_search_cache)} entradas en {SEARCH_CACHE_FILE}")
    else:
        _search_cache = {}


def save_search_cache() -> None:
    with open(SEARCH_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(_search_cache, f, ensure_ascii=False, indent=2)


def _cache_key(title: str, artist: str) -> str:
    return f"{title.lower()}::{artist.lower()}"


def get_cached_track(title: str, artist: str) -> tuple[bool, str | None]:
    """Retorna (found_in_cache, spotify_id_or_None)."""
    key = _cache_key(title, artist)
    if key in _search_cache:
        return True, _search_cache[key]
    return False, None


def set_cached_track(title: str, artist: str, spotify_id: str | None) -> None:
    _search_cache[_cache_key(title, artist)] = spotify_id


# ─── Métricas ────────────────────────────────────────────────────────────────

metrics = {
    "spotify_requests": 0,
    "cache_hits":       0,
    "spotify_searches": 0,
    "tracks_added":     0,
    "tracks_removed":   0,
    "rate_limit_waits": 0,
}


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


# ─── Helpers Spotify ─────────────────────────────────────────────────────────

def sp_headers(sp):
    sp.auth_manager.validate_token(sp.auth_manager.get_cached_token())
    token = sp.auth_manager.get_cached_token()["access_token"]
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def sp_get(sp, url, params=None):
    metrics["spotify_requests"] += 1
    r = requests.get(url, headers=sp_headers(sp), params=params or {})
    r.raise_for_status()
    return r.json()


def sp_post(sp, url, payload):
    metrics["spotify_requests"] += 1
    r = requests.post(url, headers=sp_headers(sp), json=payload)
    r.raise_for_status()
    return r.json()


def sp_delete(sp, url, payload):
    metrics["spotify_requests"] += 1
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
                return pl["id"]
        if result["next"] is None:
            break
        offset += 50
    pl = sp_post(sp, "https://api.spotify.com/v1/me/playlists", {
        "name": name,
        "public": False,
        "description": "Sincronizada desde YouTube Music",
    })
    log.info(f"  Playlist creada en Spotify: '{name}'")
    return pl["id"]


def get_spotify_tracks(sp, playlist_id: str) -> list[dict]:
    tracks = []
    offset = 0
    while True:
        result = sp_get(sp, f"https://api.spotify.com/v1/playlists/{playlist_id}/items",
                        {"limit": 100, "offset": offset})
        for item in result.get("items", []):
            t = item.get("item") or item.get("track")
            if not t or not t.get("id"):
                continue
            artists = ", ".join(a["name"] for a in t.get("artists", []))
            tracks.append({"id": t["id"], "name": t["name"], "artist": artists})
        if result.get("next") is None:
            break
        offset += 100
    return tracks


def search_spotify_track(sp, title: str, artist: str) -> str | None:
    """
    Busca en Spotify con reintentos reactivos al 429.
    Lee el cache antes de hacer cualquier request.
    Guarda el resultado (positivo o negativo) en cache.
    """
    # ── 1. Consultar cache primero ──────────────────────────────────────────
    in_cache, cached_id = get_cached_track(title, artist)
    if in_cache:
        metrics["cache_hits"] += 1
        return cached_id

    # ── 2. Llamar a Spotify ─────────────────────────────────────────────────
    metrics["spotify_searches"] += 1
    artist_clean = artist.replace(" - Topic", "").strip()
    query_strict = f"track:{title} artist:{artist_clean}" if artist_clean else f"track:{title}"
    query_loose  = f"{title} {artist_clean}"

    for attempt in range(3):
        try:
            result = sp_get(sp, "https://api.spotify.com/v1/search",
                            {"q": query_strict, "type": "track", "limit": 1})
            items = result["tracks"]["items"]
            if items:
                spotify_id = items[0]["id"]
                set_cached_track(title, artist, spotify_id)
                return spotify_id

            # Fallback: query libre
            result = sp_get(sp, "https://api.spotify.com/v1/search",
                            {"q": query_loose, "type": "track", "limit": 1})
            items = result["tracks"]["items"]
            spotify_id = items[0]["id"] if items else None
            set_cached_track(title, artist, spotify_id)  # guarda también None
            return spotify_id

        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 429:
                # ── Rate limit reactivo ─────────────────────────────────────
                wait = int(e.response.headers.get("Retry-After", 5))
                if wait > 60:
                    raise SpotifyRateLimitError(
                        f"Spotify rate limit de {wait}s — abortando búsquedas"
                    )
                jitter = random.uniform(0.2, 1.0)
                total_wait = wait + jitter
                metrics["rate_limit_waits"] += 1
                log.warning(f"  Rate limit, esperando {total_wait:.1f}s...")
                time.sleep(total_wait)
                # Reintenta el mismo attempt (no incrementa)
                continue
            else:
                log.warning(f"  Error buscando '{title}': {e}")
                set_cached_track(title, artist, None)
                return None
        except Exception as e:
            log.warning(f"  Error buscando '{title}': {e}")
            set_cached_track(title, artist, None)
            return None

    log.warning(f"  No se pudo buscar '{title}' tras 3 intentos.")
    set_cached_track(title, artist, None)
    return None


def clear_playlist(sp, playlist_id: str, current_tracks: list[dict]):
    """Vacía la playlist eliminando todos los tracks actuales."""
    if not current_tracks:
        return
    base_url = f"https://api.spotify.com/v1/playlists/{playlist_id}/items"
    all_items = [{"uri": f"spotify:track:{t['id']}"} for t in current_tracks]
    for i in range(0, len(all_items), 100):
        sp_delete(sp, base_url, {"items": all_items[i:i+100]})


def remove_tracks(sp, playlist_id: str, track_ids: list[str]):
    """Elimina solo los tracks indicados (sin vaciar la playlist completa)."""
    if not track_ids:
        return
    base_url = f"https://api.spotify.com/v1/playlists/{playlist_id}/items"
    all_items = [{"uri": f"spotify:track:{tid}"} for tid in track_ids]
    for i in range(0, len(all_items), 100):
        sp_delete(sp, base_url, {"items": all_items[i:i+100]})
    metrics["tracks_removed"] += len(track_ids)


def add_tracks(sp, playlist_id: str, track_ids: list[str]):
    """Agrega tracks en orden (máximo 100 por request)."""
    if not track_ids:
        return
    base_url = f"https://api.spotify.com/v1/playlists/{playlist_id}/items"
    for i in range(0, len(track_ids), 100):
        chunk = [f"spotify:track:{tid}" for tid in track_ids[i:i+100]]
        sp_post(sp, base_url, {"uris": chunk})
    metrics["tracks_added"] += len(track_ids)


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

    # 3. Mapas de tracks actuales en Spotify
    sp_ids_current = [t["id"] for t in sp_tracks]
    old_set = set(sp_ids_current)

    sp_map_title_artist = {}
    sp_map_title_only   = {}
    for t in sp_tracks:
        title        = t["name"].lower()
        first_artist = t["artist"].split(",")[0].strip().lower()
        sp_map_title_artist[(title, first_artist)] = t["id"]
        if title not in sp_map_title_only:
            sp_map_title_only[title] = t["id"]

    # 4. Resolver IDs para cada track de YTM
    not_found    = []
    new_track_ids = []

    for track in ytm_tracks:
        title  = track["title"].lower()
        artist = track["artist"].lower()

        if (title, artist) in sp_map_title_artist:
            new_track_ids.append(sp_map_title_artist[(title, artist)])
        elif title in sp_map_title_only:
            new_track_ids.append(sp_map_title_only[title])
        else:
            try:
                spotify_id = search_spotify_track(sp, track["title"], track["artist"])
            except SpotifyRateLimitError as e:
                log.warning(f"  ⏸ {e}")
                log.warning("  Las canciones pendientes se procesarán en el próximo run.")
                not_found.append(f"{track['artist']} — {track['title']} (pendiente)")
                break
            if spotify_id:
                new_track_ids.append(spotify_id)
                sp_map_title_artist[(title, artist)] = spotify_id
                sp_map_title_only[title] = spotify_id
                log.info(f"  ✓ Nueva: {track['artist']} — {track['title']}")
            else:
                not_found.append(f"{track['artist']} — {track['title']}")
                log.warning(f"  ✗ No encontrada: {track['artist']} — {track['title']}")
            # ── Sin sleep fijo: el rate limit reactivo en search_spotify_track lo maneja

    # 5. Sin cambios
    if new_track_ids == sp_ids_current:
        log.info(f"  ✅ Sin cambios.")
        return

    # 6. Calcular diferencias
    new_set = set(new_track_ids)
    to_add    = [tid for tid in new_track_ids if tid not in old_set]
    to_remove = [tid for tid in sp_ids_current if tid not in new_set]

    # Comparar orden relativo de las canciones comunes en ambas listas.
    # Ignora las nuevas y las eliminadas — solo verifica si el orden cambió.
    common_in_new = [tid for tid in new_track_ids if tid in old_set]
    common_in_old = [tid for tid in sp_ids_current if tid in new_set]
    order_changed = common_in_new != common_in_old

    log.info(f"  + Agregar: {len(to_add)} | - Eliminar: {len(to_remove)} | ↕ Reordenar: {order_changed}")

    # 7. Aplicar cambios
    if order_changed:
        # Hay reorden: más simple y seguro vaciar y reinsertar todo en el orden correcto
        log.info("  ↕ Orden cambiado — vaciando y reinsertando.")
        clear_playlist(sp, sp_playlist_id, sp_tracks)
        metrics["tracks_removed"] += len(sp_tracks)
        add_tracks(sp, sp_playlist_id, new_track_ids)
    else:
        # Sin reorden: cirugía — solo eliminar las sobrantes y añadir las nuevas al final
        if to_remove:
            log.info(f"  - Eliminando {len(to_remove)} canciones.")
            remove_tracks(sp, sp_playlist_id, to_remove)
        if to_add:
            log.info(f"  + Agregando {len(to_add)} canciones al final.")
            add_tracks(sp, sp_playlist_id, to_add)

    for tid in to_add:
        log.info(f"  + Agregada: {tid}")
    for tid in to_remove:
        log.info(f"  - Eliminada: {tid}")

    log.info(f"  ✅ Sincronización completa: {len(new_track_ids)} canciones")

    if not_found:
        log.warning(f"  ⚠ No encontradas en Spotify ({len(not_found)}):")
        for name in not_found:
            log.warning(f"    - {name}")


def main():
    start_time = time.time()

    if not os.path.exists("config.json"):
        log.error("No se encontró config.json. Corre setup_playlists.py primero.")
        sys.exit(1)
    with open("config.json", encoding="utf-8") as f:
        config = json.load(f)
    playlists = config.get("playlists", [])
    if not playlists:
        log.error("config.json no tiene playlists configuradas.")
        sys.exit(1)

    load_search_cache()

    log.info(f"Iniciando sincronización de {len(playlists)} playlist(s)...")
    youtube = get_youtube()
    sp      = get_spotify()

    for pl in playlists:
        try:
            sync_playlist(youtube, sp, pl["youtube_music_id"], pl["spotify_name"])
        except Exception as e:
            log.error(f"Error sincronizando '{pl.get('spotify_name')}': {e}")

    save_search_cache()

    elapsed = time.time() - start_time
    log.info("✅ Sincronización completa.")
    log.info("─── Métricas ─────────────────────────────────")
    log.info(f"  Spotify requests:   {metrics['spotify_requests']}")
    log.info(f"  Search cache hits:  {metrics['cache_hits']}")
    log.info(f"  Spotify searches:   {metrics['spotify_searches']}")
    log.info(f"  Tracks added:       {metrics['tracks_added']}")
    log.info(f"  Tracks removed:     {metrics['tracks_removed']}")
    log.info(f"  Rate limit waits:   {metrics['rate_limit_waits']}")
    log.info(f"  Tiempo total:       {elapsed:.1f}s")
    log.info("──────────────────────────────────────────────")


if __name__ == "__main__":
    main()