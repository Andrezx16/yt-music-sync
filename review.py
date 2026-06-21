"""
review.py — Resolución manual de canciones pendientes de revisión.
Lee pending_review.json, muestra opciones de Spotify, y guarda decisiones directamente en Supabase.
Corre siempre de manera local, nunca en GitHub Actions.
"""

import json
import os
import requests
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

PENDING_REVIEW_FILE = "pending_review.json"

# ─── Inicialización de Supabase ──────────────────────────────────────────────
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

if SUPABASE_URL:
    SUPABASE_URL = SUPABASE_URL.strip().rstrip('/')

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def _cache_key(title: str, artist: str) -> str:
    return f"{title.lower()}::{artist.lower()}"

def parse_spotify_id(val: str) -> str | None:
    val = val.strip()
    if not val:
        return None
    # Si es una URL de Spotify, e.g., https://open.spotify.com/track/6Yk1zG1V6VVYeUedec2TqU?si=xxxx
    if "open.spotify.com/track/" in val:
        parts = val.split("open.spotify.com/track/")
        if len(parts) > 1:
            id_part = parts[1].split("?")[0].split("/")[0]
            if len(id_part) == 22:
                return id_part
    # Si es uri: spotify:track:6Yk1zG1V6VVYeUedec2TqU
    if val.startswith("spotify:track:"):
        id_part = val.split("spotify:track:")[1]
        if len(id_part) == 22:
            return id_part
    # Si es el ID directo
    if len(val) == 22:
        return val
    return None

def get_spotify() -> spotipy.Spotify:
    cache_path = ".spotify_cache"
    auth_manager = SpotifyOAuth(
        client_id=os.environ["SPOTIFY_CLIENT_ID"],
        client_secret=os.environ["SPOTIFY_CLIENT_SECRET"],
        redirect_uri=os.environ.get("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback"),
        scope="playlist-modify-public playlist-modify-private playlist-read-private",
        cache_path=cache_path,
        open_browser=False,
    )
    return spotipy.Spotify(auth_manager=auth_manager)

def sp_headers(sp: spotipy.Spotify) -> dict:
    sp.auth_manager.validate_token(sp.auth_manager.get_cached_token())
    token = sp.auth_manager.get_cached_token()["access_token"]
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

# Canales de YouTube que no son artistas reales en Spotify
_FAKE_ARTISTS = {
    "release", "the a1 plug", "snitm", "dripp4n", "kianyel", "briiel",
    "briielbtmf", "prod stone", "proodbyng", "jo.onethebeat", "anibaaal 808",
    "freestyle mania season", "los mas odiau", "urban music tv", "bymgxzz",
    "yovngfelo", "dvnne", "kvcti", "flirtdexity", "world star hip hop",
    "worldstarhiphop", "trap house latino", "miflow tv", "mundo music",
    "sbeᶻᶻ", "ap", "k10", "kylowtw", "pouliryc", "recxo",
}

def _normalize(s: str) -> str:
    import re, unicodedata
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"\(.*?\)", "", s)
    s = re.sub(r"\[.*?\]", "", s)
    s = re.sub(r"[^\w\s]", " ", s)
    return " ".join(s.lower().split())

def search_options(sp: spotipy.Spotify, title: str, artist: str) -> list[dict]:
    """Busca hasta 5 opciones en Spotify usando la misma cascada de estrategias que sync.py."""
    import re
    artist_clean = artist.replace(" - Topic", "").strip()
    artist_lower = artist_clean.lower()
    is_fake = artist_lower in _FAKE_ARTISTS
    headers = sp_headers(sp)

    # Extraer artista real si el título tiene formato "Artista - Título"
    title_real = title
    artist_real = artist_clean
    m = re.match(r"^(.+?)\s+[-–—]\s+(.+)$", title)
    if m and (is_fake or not artist_clean):
        artist_real = m.group(1).strip()
        title_real  = m.group(2).strip()

    def _search(q: str) -> list:
        r = requests.get(
            "https://api.spotify.com/v1/search",
            headers=headers,
            params={"q": q, "type": "track", "limit": 5},
        )
        return r.json().get("tracks", {}).get("items", [])

    seen_ids: set = set()
    items: list = []

    def _add(new_items: list) -> None:
        for t in new_items:
            if t["id"] not in seen_ids:
                seen_ids.add(t["id"])
                items.append(t)

    # Mismas 4 estrategias que sync.py, acumulando resultados únicos
    if artist_real and artist_real.lower() not in _FAKE_ARTISTS:
        _add(_search(f"track:{title_real} artist:{artist_real}"))
    _add(_search(f"track:{title_real}"))
    if artist_real and artist_real.lower() not in _FAKE_ARTISTS:
        _add(_search(f"{title_real} {artist_real}"))
    _add(_search(f"{title_real}"))

    return [
        {
            "id": t["id"],
            "name": t["name"],
            "artist": ", ".join(a["name"] for a in t["artists"]),
            "album": t["album"]["name"],
            "url": t["external_urls"].get("spotify", ""),
        }
        for t in items[:5]
    ]

def load_manual_matches() -> dict:
    try:
        res = supabase.table("mapeos_manuales").select("nombre_busqueda", "spotify_id").limit(5000).execute()
        return {fila["nombre_busqueda"]: fila["spotify_id"] for fila in res.data}
    except Exception as e:
        print(f"❌ Error al cargar mapeos manuales de Supabase: {e}")
        return {}

def load_search_cache() -> dict:
    try:
        res = supabase.table("canciones").select("nombre_busqueda", "spotify_id").limit(10000).execute()
        return {fila["nombre_busqueda"]: fila["spotify_id"] for fila in res.data}
    except Exception as e:
        print(f"❌ Error al cargar caché automático de Supabase: {e}")
        return {}

def save_manual_matches(matches: dict) -> None:
    filas = [{"nombre_busqueda": k, "spotify_id": v} for k, v in matches.items()]
    try:
        print("⏳ Subiendo decisiones a la nube de Supabase...")
        supabase.table("mapeos_manuales").upsert(filas).execute()
        print("✅ Guardado exitosamente en la base de datos remota.")
    except Exception as e:
        print(f"❌ Error al guardar en Supabase: {e}")

def review():
    if not os.path.exists(PENDING_REVIEW_FILE):
        print("✅ No hay canciones pendientes de revisión.")
        return

    with open(PENDING_REVIEW_FILE, encoding="utf-8") as f:
        pending = json.load(f)

    if not pending:
        print("✅ No hay canciones pendientes de revisión.")
        return

    manual_matches = load_manual_matches()
    search_cache = load_search_cache()
    sp = get_spotify()

    # Filtrar las que ya tienen decisión en Supabase
    to_review = [
        t for t in pending
        if _cache_key(t["title"], t["artist"]) not in manual_matches
    ]

    if not to_review:
        print("✅ Todas las pendientes ya tienen decisión guardada en Supabase.")
        return

    print(f"\n🎵 {len(to_review)} canción(es) pendientes de revisión\n")
    print("─" * 60)

    saved = 0
    new_decisions = {}

    def parse_id(raw: str) -> str:
        raw = raw.strip()
        if "spotify.com/track/" in raw:
            return raw.split("spotify.com/track/")[1].split("?")[0]
        return raw

    try:
        i = 0
        while i < len(to_review):
            track     = to_review[i]
            title     = track["title"]
            artist    = track["artist"]
            reason    = track.get("reason", "")
            key       = _cache_key(title, artist)
            cached_id = search_cache.get(key)

            reason_label = {
                "fuzzy_title_match": "⚠  Match por título (artista difiere)",
                "not_found":         "✗  No encontrada automáticamente",
                "duplicate_id":      "🔁 ID duplicado con otra canción en la playlist",
            }.get(reason, "")

            print(f"\n[{i+1}/{len(to_review)}] {artist} — {title}")
            if reason_label:
                print(f"  {reason_label}")
            if cached_id:
                print(f"  ID actual en caché: {cached_id}")
            else:
                print("  ID actual en caché: (ninguno / no encontrada)")
            print("─" * 60)

            should_exit = False
            go_back     = False

            while True:
                print("\n  Opciones:")
                if cached_id:
                    print("    [Enter] → Aceptar ID actual en caché")
                print("    b       → Buscar hasta 5 opciones en Spotify")
                print("    m       → Ingresar ID de Spotify manualmente")
                print("    s       → Skip (no sincronizar esta canción)")
                print("    n       → Dejar para después (siguiente track)")
                if i > 0:
                    print("    p       → Volver a la canción anterior")
                print("    q       → Guardar decisiones tomadas y salir")

                choice = input("\n  Tu elección: ").strip().lower()

                if choice == "q":
                    should_exit = True
                    break

                elif choice == "p" and i > 0:
                    # Retroceder: deshacer decisión anterior si la había
                    prev_key = _cache_key(to_review[i-1]["title"], to_review[i-1]["artist"])
                    if prev_key in new_decisions:
                        del new_decisions[prev_key]
                        saved -= 1
                        print(f"  ↩ Decisión anterior deshecha.")
                    go_back = True
                    break

                elif choice == "" and cached_id:
                    new_decisions[key] = cached_id
                    print(f"  ✅ Aceptado ID en caché: {cached_id}")
                    saved += 1
                    break

                elif choice == "b":
                    print("  Buscando opciones en Spotify...")
                    options = search_options(sp, title, artist)

                    if not options:
                        print("  ❌ Sin resultados en Spotify.")
                        continue

                    print("\n  Resultados encontrados:")
                    for j, opt in enumerate(options, 1):
                        print(f"    {j}. {opt['artist']} — {opt['name']}")
                        print(f"       Album: {opt['album']}")
                        print(f"       {opt['url']}")

                    print("\n  Opciones de búsqueda:")
                    for j in range(1, len(options) + 1):
                        print(f"    {j} → elegir opción {j}")
                    print("    m → ingresar ID de Spotify manualmente")
                    print("    s → skip (no sincronizar esta canción)")
                    print("    n → dejar para después (siguiente track)")
                    print("    q → guardar decisiones tomadas y salir")
                    print("    v → volver al menú principal")

                    while True:
                        sub = input("\n  Tu elección (de búsqueda): ").strip().lower()
                        if sub.isdigit() and 1 <= int(sub) <= len(options):
                            chosen = options[int(sub) - 1]
                            new_decisions[key] = chosen["id"]
                            print(f"  ✅ Seleccionado: {chosen['artist']} — {chosen['name']}")
                            saved += 1
                            break
                        elif sub == "m":
                            raw = input("  ID o Link de Spotify: ").strip()
                            new_decisions[key] = parse_id(raw)
                            print(f"  ✅ Seleccionado ID manual: {new_decisions[key]}")
                            saved += 1
                            break
                        elif sub == "s":
                            new_decisions[key] = "skip"
                            print("  ⏭ Skip seleccionado.")
                            saved += 1
                            break
                        elif sub == "n":
                            print("  ⏩ Dejado para después.")
                            break
                        elif sub == "q":
                            should_exit = True
                            break
                        elif sub == "v":
                            break
                        else:
                            print("  Opción inválida, intenta de nuevo.")
                    if key in new_decisions or should_exit:
                        break
                    continue

                elif choice == "m":
                    raw = input("  ID o Link de Spotify: ").strip()
                    new_decisions[key] = parse_id(raw)
                    print(f"  ✅ Seleccionado ID manual: {new_decisions[key]}")
                    saved += 1
                    break

                elif choice == "s":
                    new_decisions[key] = "skip"
                    print("  ⏭ Skip seleccionado.")
                    saved += 1
                    break

                elif choice == "n":
                    print("  ⏩ Dejado para después.")
                    break

                elif choice != "":
                    print("  Opción inválida, intenta de nuevo.")

            if should_exit:
                break
            elif go_back:
                i -= 1
            else:
                i += 1

    except KeyboardInterrupt:
        print("\n\n  👋 Saliendo y guardando progreso...")

    if saved > 0:
        save_manual_matches(new_decisions)
        print(f"\n📝 {saved} decisión(es) subidas a Supabase con éxito.")
    else:
        print("\n  Sin cambios guardados.")

    # Eliminar del pending_review.json las entradas que ya tienen decisión
    # (ya sea en new_decisions o en manual_matches cargados al inicio).
    all_resolved = set(manual_matches.keys()) | set(new_decisions.keys())
    remaining = [t for t in pending if _cache_key(t["title"], t["artist"]) not in all_resolved]
    with open(PENDING_REVIEW_FILE, "w", encoding="utf-8") as f:
        json.dump(remaining, f, ensure_ascii=False, indent=2)
    if remaining:
        print(f"  📋 {len(remaining)} canción(es) aún pendientes en {PENDING_REVIEW_FILE}.")
    else:
        print(f"  ✅ {PENDING_REVIEW_FILE} vaciado — todas las canciones resueltas.")


def clean(ytm_tracks_file: str = "ytm_tracks.json") -> None:
    """Elimina de mapeos_manuales las entradas cuya canción ya no está en YTM."""
    if not os.path.exists(ytm_tracks_file):
        print(f"❌ No se encontró {ytm_tracks_file}. Corre sync.py primero.")
        return

    with open(ytm_tracks_file, encoding="utf-8") as f:
        ytm_tracks = json.load(f)

    ytm_keys = {_cache_key(t["title"], t["artist"]) for t in ytm_tracks}
    manual_matches = load_manual_matches()

    orphans = [k for k in manual_matches if k not in ytm_keys]
    if not orphans:
        print("✅ No hay entradas huérfanas en mapeos_manuales.")
        return

    print(f"\n🧹 {len(orphans)} entrada(s) huérfana(s) en mapeos_manuales (de {len(manual_matches)} totales):\n")
    for k in orphans:
        print(f"  - {k} → {manual_matches[k]}")

    print(f"\n⚠  Esto eliminará {len(orphans)} entrada(s) de Supabase permanentemente.")
    confirm = input("¿Confirmar eliminación? (escribe 'si' para continuar): ").strip().lower()
    if confirm == "si":
        eliminadas = 0
        for k in orphans:
            try:
                supabase.table("mapeos_manuales").delete().eq("nombre_busqueda", k).execute()
                eliminadas += 1
            except Exception as e:
                print(f"  ❌ No se pudo eliminar {k}: {e}")
        print(f"✅ {eliminadas} entrada(s) eliminadas de mapeos_manuales.")
    else:
        print("  Cancelado — no se eliminó nada.")


def clean_cache(ytm_tracks_file: str = "ytm_tracks.json") -> None:
    """Elimina de canciones (caché automático) las entradas huérfanas."""
    if not os.path.exists(ytm_tracks_file):
        print(f"❌ No se encontró {ytm_tracks_file}. Corre sync.py primero.")
        return

    with open(ytm_tracks_file, encoding="utf-8") as f:
        ytm_tracks = json.load(f)

    if not ytm_tracks:
        print("❌ ytm_tracks.json está vacío — abortando para no borrar todo el caché.")
        return

    ytm_keys = {_cache_key(t["title"], t["artist"]) for t in ytm_tracks}
    cache = load_search_cache()

    orphans = [k for k in cache if k not in ytm_keys]
    if not orphans:
        print("✅ No hay entradas huérfanas en el caché (canciones).")
        return

    print(f"\n🧹 {len(orphans)} entrada(s) huérfana(s) en canciones (de {len(cache)} totales):\n")

    # Mostrar solo las primeras 30 para no inundar la pantalla
    for k in orphans[:30]:
        print(f"  - {k} → {cache[k] or 'null'}")
    if len(orphans) > 30:
        print(f"  ... y {len(orphans) - 30} más.")

    print(f"\n⚠  Esto eliminará {len(orphans)} entrada(s) del caché automático permanentemente.")
    print("   El caché se reconstruirá solo en los próximos runs de sync.py.")
    confirm = input("¿Confirmar eliminación? (escribe 'si' para continuar): ").strip().lower()
    if confirm == "si":
        eliminadas = 0
        for k in orphans:
            try:
                supabase.table("canciones").delete().eq("nombre_busqueda", k).execute()
                eliminadas += 1
            except Exception as e:
                print(f"  ❌ No se pudo eliminar {k}: {e}")
        print(f"✅ {eliminadas} entrada(s) eliminadas del caché.")
    else:
        print("  Cancelado — no se eliminó nada.")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--clean":
        clean()
    elif len(sys.argv) > 1 and sys.argv[1] == "--clean-cache":
        clean_cache()
    else:
        review()