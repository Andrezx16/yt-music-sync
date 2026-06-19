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

def search_options(sp: spotipy.Spotify, title: str, artist: str) -> list[dict]:
    """Busca 3 opciones en Spotify para una canción."""
    artist_clean = artist.replace(" - Topic", "").strip()
    headers = sp_headers(sp)

    # Query estricta primero
    r = requests.get(
        "https://api.spotify.com/v1/search",
        headers=headers,
        params={"q": f"track:{title} artist:{artist_clean}", "type": "track", "limit": 3},
    )
    items = r.json().get("tracks", {}).get("items", [])

    # Si no hay suficientes, complementar con query libre
    if len(items) < 3:
        r2 = requests.get(
            "https://api.spotify.com/v1/search",
            headers=headers,
            params={"q": f"{title} {artist_clean}", "type": "track", "limit": 3},
        )
        extra = r2.json().get("tracks", {}).get("items", [])
        seen_ids = {t["id"] for t in items}
        for t in extra:
            if t["id"] not in seen_ids:
                items.append(t)
            if len(items) >= 3:
                break

    return [
        {
            "id": t["id"],
            "name": t["name"],
            "artist": ", ".join(a["name"] for a in t["artists"]),
            "album": t["album"]["name"],
            "url": t["external_urls"].get("spotify", ""),
        }
        for t in items[:3]
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
    
    try:
        for i, track in enumerate(to_review, 1):
            title  = track["title"]
            artist = track["artist"]
            key    = _cache_key(title, artist)
            cached_id = search_cache.get(key)

            print(f"\n[{i}/{len(to_review)}] {artist} — {title}")
            if cached_id:
                print(f"  ID actual en caché: {cached_id}")
            else:
                print("  ID actual en caché: (ninguno / no encontrada)")
            print("─" * 60)

            should_exit = False
            while True:
                print("\n  Opciones:")
                if cached_id:
                    print("    [Enter] → Aceptar ID actual en caché")
                print("    b       → Buscar 3 opciones en Spotify")
                print("    m       → Ingresar ID de Spotify manualmente")
                print("    s       → Skip (no sincronizar esta canción)")
                print("    n       → Dejar para después (siguiente track)")
                print("    q       → Guardar decisiones tomadas y salir")

                choice = input("\n  Tu elección: ").strip().lower()

                if choice == "q":
                    should_exit = True
                    break

                if choice == "" and cached_id:
                    new_decisions[key] = cached_id
                    print(f"  ✅ Aceptado ID en caché: {cached_id}")
                    saved += 1
                    break

                elif choice == "b":
                    print("  Buscando opciones en Spotify...")
                    options = search_options(sp, title, artist)

                    if options:
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
                        print("    v → volver al menú anterior")

                        sub_choice_valid = False
                        while not sub_choice_valid:
                            sub_choice = input("\n  Tu elección (de búsqueda): ").strip().lower()
                            if sub_choice in ("1", "2", "3") and int(sub_choice) <= len(options):
                                chosen = options[int(sub_choice) - 1]
                                new_decisions[key] = chosen["id"]
                                print(f"  ✅ Seleccionado: {chosen['artist']} — {chosen['name']}")
                                saved += 1
                                sub_choice_valid = True
                                break
                            elif sub_choice == "m":
                                choice = "m"  # pasar al prompt manual
                                sub_choice_valid = True
                            elif sub_choice == "s":
                                choice = "s"  # pasar a skip
                                sub_choice_valid = True
                            elif sub_choice == "n":
                                choice = "n"  # pasar a next
                                sub_choice_valid = True
                            elif sub_choice == "q":
                                choice = "q"
                                should_exit = True
                                sub_choice_valid = True
                            elif sub_choice == "v":
                                sub_choice_valid = True
                                continue
                            else:
                                print("  Opción inválida, intenta de nuevo.")
                        
                        if key in new_decisions:
                            break
                        if should_exit:
                            break
                    else:
                        print("  ❌ Sin resultados en Spotify.")
                        continue

                elif choice == "m":
                    raw_input = input("  ID o Link de Spotify: ").strip()
                    if raw_input:
                        # Si pegaste el link completo, extraemos el ID que está entre '/track/' y el '?'
                        if "spotify.com/track/" in raw_input:
                            try:
                                # Cortamos lo que está antes del ID y nos quedamos con el resto
                                spotify_id = raw_input.split("spotify.com/track/")[1]
                                # Si tiene parámetros de rastreo (?si=...), los eliminamos
                                spotify_id = spotify_id.split("?")[0]
                            except IndexError:
                                spotify_id = raw_input # Por si pasa algo raro, dejamos el input original
                    else:
                        spotify_id = raw_input # Si ya era un ID limpio, lo dejamos igual

                    new_decisions[key] = spotify_id
                    print(f"  ✅ Seleccionado ID manual: {spotify_id}")
                    saved += 1
                    break

                elif choice == "s":
                    new_decisions[key] = "skip"
                    print(f"  ⏭ Skip seleccionado.")
                    saved += 1
                    break

                elif choice == "n":
                    print("  ⏩ Dejado para después.")
                    break

                elif choice != "":
                    print("  Opción inválida, intenta de nuevo.")

            if should_exit:
                break
    except KeyboardInterrupt:
        print("\n\n  👋 Saliendo y guardando progreso...")

    if saved > 0:
        save_manual_matches(new_decisions)
        print(f"\n📝 {saved} decisión(es) subidas a Supabase con éxito.")
    else:
        print("\n  Sin cambios guardados.")


def clean(ytm_tracks_file: str = "ytm_tracks.json") -> None:
    """Elimina de Supabase las entradas cuya canción ya no está en YTM."""
    if not os.path.exists(ytm_tracks_file):
        print(f"❌ No se encontró {ytm_tracks_file}. Corre sync.py primero.")
        return

    with open(ytm_tracks_file, encoding="utf-8") as f:
        ytm_tracks = json.load(f)

    ytm_keys = {_cache_key(t["title"], t["artist"]) for t in ytm_tracks}
    manual_matches = load_manual_matches()

    orphans = [k for k in manual_matches if k not in ytm_keys]
    if not orphans:
        print("✅ No hay entradas huérfanas en Supabase.")
        return

    print(f"\n🧹 {len(orphans)} entrada(s) huérfana(s) encontradas en la nube:\n")
    for k in orphans:
        print(f"  - {k} → {manual_matches[k]}")

    confirm = input("\n¿Eliminar estas entradas de Supabase? (s/n): ").strip().lower()
    if confirm == "s":
        for k in orphans:
            try:
                supabase.table("mapeos_manuales").delete().eq("nombre_busqueda", k).execute()
                print(f"  🗑️ Eliminado: {k}")
            except Exception as e:
                print(f"  ❌ No se pudo eliminar {k}: {e}")
        print("✅ Proceso de limpieza finalizado.")
    else:
        print("  Cancelado.")

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--clean":
        clean()
    else:
        review()