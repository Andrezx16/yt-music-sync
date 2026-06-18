"""
sync.py — Sincroniza playlists de YouTube Music a Spotify.
Usa YouTube Data API v3 oficial + requests directos a Spotify API.
Ahora integrado 100% con Supabase para el caché y mapeos manuales.
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
from supabase import create_client, Client

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ─── Inicialización de Supabase ──────────────────────────────────────────────
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

if SUPABASE_URL:
    SUPABASE_URL = SUPABASE_URL.strip().rstrip('/')

if not SUPABASE_URL or not SUPABASE_KEY:
    log.error("Faltan las credenciales de SUPABASE_URL o SUPABASE_KEY en el archivo .env.")
    sys.exit(1)

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ─── Cache de búsquedas Spotify (En la Nube) ─────────────────────────────────
_search_cache: dict = {}

def load_search_cache() -> None:
    global _search_cache
    try:
        res = supabase.table("canciones").select("nombre_busqueda", "spotify_id").limit(10000).execute()
        _search_cache = {fila["nombre_busqueda"]: fila["spotify_id"] for fila in res.data}
        log.info(f"Cache cargado desde Supabase: {len(_search_cache)} entradas.")
    except Exception as e:
        log.error(f"Error al cargar el caché desde Supabase: {e}")
        _search_cache = {}


def clean_search_cache(all_ytm_keys: set[str]) -> None:
    """Elimina del caché en la nube entradas cuya canción ya no está en YTM."""
    removed_keys = [k for k in list(_search_cache) if k not in all_ytm_keys]
    if not removed_keys:
        return
        
    log.info(f"Limpiando caché en la nube: {len(removed_keys)} entradas huérfanas...")
    for k in removed_keys:
        try:
            supabase.table("canciones").delete().eq("nombre_busqueda", k).execute()
            del _search_cache[k]
        except Exception as e:
            log.warning(f"No se pudo eliminar {k} de Supabase: {e}")

def _cache_key(title: str, artist: str) -> str:
    return f"{title.lower()}::{artist.lower()}"

def get_cached_track(title: str, artist: str) -> tuple[bool, str | None]:
    key = _cache_key(title, artist)
    if key in _search_cache:
        return True, _search_cache[key]
    return False, None

def set_cached_track(title: str, artist: str, spotify_id: str | None) -> None:
    key = _cache_key(title, artist)
    _search_cache[key] = spotify_id
    try:
        supabase.table("canciones").upsert({"nombre_busqueda": key, "spotify_id": spotify_id}).execute()
    except Exception as e:
        log.warning(f"Error al persistir nueva búsqueda en Supabase: {e}")

# ─── Manual matches (En la Nube) ─────────────────────────────────────────────
_manual_matches: dict = {}

def load_manual_matches() -> None:
    global _manual_matches
    try:
        res = supabase.table("mapeos_manuales").select("nombre_busqueda", "spotify_id").limit(5000).execute()
        _manual_matches = {fila["nombre_busqueda"]: fila["spotify_id"] for fila in res.data}
        log.info(f"Manual matches cargados desde Supabase: {len(_manual_matches)} entradas.")
    except Exception as e:
        log.error(f"Error al cargar mapeos manuales desde Supabase: {e}")
        _manual_matches = {}

def get_manual_match(title: str, artist: str) -> tuple[bool, str | None]:
    key = _cache_key(title, artist)
    if key in _manual_matches:
        val = _manual_matches[key]
        return True, (None if val == "skip" else val)
    return False, None

PENDING_REVIEW_FILE = "pending_review.json"

def save_pending_review(pending: list[dict]) -> None:
    if not pending:
        if os.path.exists(PENDING_REVIEW_FILE):
            os.remove(PENDING_REVIEW_FILE)
        return
    with open(PENDING_REVIEW_FILE, "w", encoding="utf-8") as f:
        json.dump(pending, f, ensure_ascii=False, indent=2)
    log.warning(f"  ⚠ {len(pending)} canción(es) pendientes de revisión → corre review.py")

# ─── Métricas ────────────────────────────────────────────────────────────────
metrics = {
    "spotify_requests": 0,
    "cache_hits":       0,
    "spotify_searches": 0,
    "tracks_added":     0,
    "tracks_removed":   0,
    "rate_limit_waits": 0,
    "manual_matches":   0,
    "skipped":          0,
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
    try:
        token = auth_manager.get_cached_token()
        if token and auth_manager.is_token_expired(token):
            auth_manager.refresh_access_token(token["refresh_token"])
    except Exception as e:
        if "invalid_grant" in str(e).lower():
            log.error("❌ El refresh token de Spotify expiró (>6 meses sin uso).")
            log.error("   Para solucionarlo:")
            log.error("   1. Corre setup_spotify.py localmente.")
            log.error("   2. Copia el contenido de .spotify_cache.")
            log.error("   3. Actualiza el secret SPOTIFY_CACHE en GitHub Actions.")
            sys.exit(1)
        raise
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
    in_cache, cached_id = get_cached_track(title, artist)
    if in_cache:
        metrics["cache_hits"] += 1
        return cached_id

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

            result = sp_get(sp, "https://api.spotify.com/v1/search",
                            {"q": query_loose, "type": "track", "limit": 1})
            items = result["tracks"]["items"]
            spotify_id = items[0]["id"] if items else None
            set_cached_track(title, artist, spotify_id) 
            return spotify_id

        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 429:
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
    if not current_tracks:
        return
    base_url = f"https://api.spotify.com/v1/playlists/{playlist_id}/items"
    all_items = [{"uri": f"spotify:track:{t['id']}"} for t in current_tracks]
    for i in range(0, len(all_items), 100):
        sp_delete(sp, base_url, {"items": all_items[i:i+100]})

def remove_tracks(sp, playlist_id: str, track_ids: list[str]):
    if not track_ids:
        return
    base_url = f"https://api.spotify.com/v1/playlists/{playlist_id}/items"
    all_items = [{"uri": f"spotify:track:{tid}"} for tid in track_ids]
    for i in range(0, len(all_items), 100):
        sp_delete(sp, base_url, {"items": all_items[i:i+100]})
    metrics["tracks_removed"] += len(track_ids)

def add_tracks(sp, playlist_id: str, track_ids: list[str]):
    if not track_ids:
        return
    base_url = f"https://api.spotify.com/v1/playlists/{playlist_id}/items"
    for i in range(0, len(track_ids), 100):
        chunk = [f"spotify:track:{tid}" for tid in track_ids[i:i+100]]
        sp_post(sp, base_url, {"uris": chunk})
    metrics["tracks_added"] += len(track_ids)

# ─── Sincronización ──────────────────────────────────────────────────────────
def sync_playlist(youtube, sp, ytm_id: str, spotify_name: str, searches_done: list):
    log.info(f"▶ Sincronizando: '{spotify_name}'")

    ytm_tracks = get_youtube_tracks(youtube, ytm_id)
    log.info(f"  YouTube Music: {len(ytm_tracks)} canciones")

    sp_playlist_id = get_or_create_spotify_playlist(sp, spotify_name)
    sp_tracks = get_spotify_tracks(sp, sp_playlist_id)
    log.info(f"  Spotify actual: {len(sp_tracks)} canciones")

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

    ADD_LIMIT      = 100
    SEARCH_LIMIT   = 100
    search_skipped = 0
    not_found     = []
    pending       = []
    new_track_ids = []

    for track in ytm_tracks:
        title  = track["title"].lower()
        artist = track["artist"].lower()

        if (title, artist) in sp_map_title_artist:
            new_track_ids.append(sp_map_title_artist[(title, artist)])
            continue
        if title in sp_map_title_only:
            new_track_ids.append(sp_map_title_only[title])
            continue

        in_manual, manual_id = get_manual_match(track["title"], track["artist"])
        if in_manual:
            if manual_id is None:
                metrics["skipped"] += 1
            else:
                metrics["manual_matches"] += 1
                new_track_ids.append(manual_id)
            continue

        in_cache, cached_id = get_cached_track(track["title"], track["artist"])
        if in_cache:
            metrics["cache_hits"] += 1
            if cached_id:
                new_track_ids.append(cached_id)
                pending.append({"title": track["title"], "artist": track["artist"]})
                log.info(f"  📦 Cache (revisar): {track['artist']} — {track['title']}")
            else:
                not_found.append(f"{track['artist']} — {track['title']}")
                pending.append({"title": track["title"], "artist": track["artist"]})
                log.warning(f"  ✗ No encontrada: {track['artist']} — {track['title']}")
            continue

        if searches_done[0] >= SEARCH_LIMIT:
            search_skipped += 1
            continue

        try:
            spotify_id = search_spotify_track(sp, track["title"], track["artist"])
            searches_done[0] += 1
        except SpotifyRateLimitError as e:
            log.warning(f"  ⏸ {e}")
            log.warning("  Las canciones pendientes se procesarán en el próximo run.")
            log.warning("  ⚠ Sync interrumpido — se agregarán las encontradas pero no se eliminará nada.")
            to_add_partial = [tid for tid in new_track_ids if tid not in old_set]
            to_add_now     = to_add_partial[:ADD_LIMIT]
            if to_add_now:
                if len(to_add_partial) > ADD_LIMIT:
                    log.info(f"  + Agregando {len(to_add_now)} de {len(to_add_partial)} canciones (límite por run).")
                else:
                    log.info(f"  + Agregando {len(to_add_now)} canciones encontradas antes del corte.")
                add_tracks(sp, sp_playlist_id, to_add_now)
            faltan = len(ytm_tracks) - (len(sp_ids_current) + len(to_add_now))
            if faltan > 0:
                log.info(f"  ⏳ ~{faltan} canciones pendientes para el próximo run (Spotify: {len(sp_ids_current) + len(to_add_now)} / YTM: {len(ytm_tracks)})")
            ytm_keys = {_cache_key(t["title"], t["artist"]) for t in ytm_tracks}
            return pending, ytm_keys, ytm_tracks, True  
        if spotify_id:
            new_track_ids.append(spotify_id)
            sp_map_title_artist[(title, artist)] = spotify_id
            sp_map_title_only[title] = spotify_id
            log.info(f"  ✓ Nueva: {track['artist']} — {track['title']}")
        else:
            not_found.append(f"{track['artist']} — {track['title']}")
            pending.append({"title": track["title"], "artist": track["artist"]})
            log.warning(f"  ✗ No encontrada: {track['artist']} — {track['title']}")

    if new_track_ids == sp_ids_current:
        if search_skipped:
            log.info(f"  ⏳ {search_skipped} canción(es) pendientes para el próximo run (límite de búsquedas alcanzado).")
        log.info(f"  ✅ Sin cambios.")
        ytm_keys = {_cache_key(t["title"], t["artist"]) for t in ytm_tracks}
        return pending, ytm_keys, ytm_tracks, False  
        
    new_set = set(new_track_ids)
    to_add    = [tid for tid in new_track_ids if tid not in old_set]
    to_remove = [tid for tid in sp_ids_current if tid not in new_set]

    common_in_new = [tid for tid in new_track_ids if tid in old_set]
    common_in_old = [tid for tid in sp_ids_current if tid in new_set]
    order_changed = common_in_new != common_in_old

    log.info(f"  + Agregar: {len(to_add)} | - Eliminar: {len(to_remove)} | ↕ Reordenar: {order_changed}")

    if order_changed:
        log.info("  ↕ Orden cambiado — vaciando y reinsertando.")
        clear_playlist(sp, sp_playlist_id, sp_tracks)
        metrics["tracks_removed"] += len(sp_tracks)
        add_tracks(sp, sp_playlist_id, new_track_ids)
    else:
        if to_remove:
            log.info(f"  - Eliminando {len(to_remove)} canciones.")
            remove_tracks(sp, sp_playlist_id, to_remove)
        if to_add:
            to_add_now     = to_add[:ADD_LIMIT]
            to_add_pending = to_add[ADD_LIMIT:]
            if to_add_pending:
                log.info(f"  + Agregando {len(to_add_now)} de {len(to_add)} canciones (límite por run).")
                log.info(f"  ⏳ {len(to_add_pending)} canciones pendientes para el próximo run.")
            else:
                log.info(f"  + Agregando {len(to_add_now)} canciones al final.")
            add_tracks(sp, sp_playlist_id, to_add_now)

    for tid in to_add:
        log.info(f"  + Agregada: {tid}")
    for tid in to_remove:
        log.info(f"  - Eliminada: {tid}")

    if search_skipped:
        log.info(f"  ⏳ {search_skipped} canción(es) pendientes para el próximo run (límite de búsquedas alcanzado).")
    log.info(f"  ✅ Sincronización completa: {len(new_track_ids)} canciones")

    if not_found:
        log.warning(f"  ⚠ No encontradas en Spotify ({len(not_found)}):")
        for name in not_found:
            log.warning(f"    - {name}")

    ytm_keys = {_cache_key(t["title"], t["artist"]) for t in ytm_tracks}
    return pending, ytm_keys, ytm_tracks, False  

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
    load_manual_matches()

    log.info(f"Iniciando sincronización de {len(playlists)} playlist(s)...")
    youtube = get_youtube()
    sp      = get_spotify()

    all_pending    = []
    all_ytm_keys   = set()
    all_ytm_tracks = []
    any_aborted    = False
    searches_done  = [0]  # contador compartido entre playlists
    for pl in playlists:
        try:
            pending, ytm_keys, ytm_tracks, aborted = sync_playlist(youtube, sp, pl["youtube_music_id"], pl["spotify_name"], searches_done)
            all_pending.extend(pending)
            all_ytm_keys.update(ytm_keys)
            all_ytm_tracks.extend(ytm_tracks)
            if aborted:
                any_aborted = True
        except Exception as e:
            log.error(f"Error sincronizando '{pl.get('spotify_name')}': {e}")

    if any_aborted:
        log.warning("  ⚠ Al menos un sync fue interrumpido — cache no se limpiará para evitar pérdida de datos.")
    else:
        clean_search_cache(all_ytm_keys)
    save_pending_review(all_pending)

    with open("ytm_tracks.json", "w", encoding="utf-8") as f:
        json.dump(all_ytm_tracks, f, ensure_ascii=False, indent=2)

    elapsed = time.time() - start_time
    log.info("✅ Sincronización completa.")
    log.info("─── Métricas ─────────────────────────────────")
    log.info(f"  Spotify requests:   {metrics['spotify_requests']}")
    log.info(f"  Search cache hits:  {metrics['cache_hits']}")
    log.info(f"  Spotify searches:   {metrics['spotify_searches']}")
    log.info(f"  Manual matches:     {metrics['manual_matches']}")
    log.info(f"  Skipped:            {metrics['skipped']}")
    log.info(f"  Tracks added:       {metrics['tracks_added']}")
    log.info(f"  Tracks removed:     {metrics['tracks_removed']}")
    log.info(f"  Rate limit waits:   {metrics['rate_limit_waits']}")
    log.info(f"  Tiempo total:       {elapsed:.1f}s")
    log.info("──────────────────────────────────────────────")

if __name__ == "__main__":
    main()