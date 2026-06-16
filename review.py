"""
review.py — Resolución manual de canciones pendientes de revisión.
Lee pending_review.json, muestra opciones de Spotify, y guarda decisiones en manual_matches.json.
Corre siempre de manera local, nunca en GitHub Actions.
"""

import json
import os
import requests
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from dotenv import load_dotenv

load_dotenv()

PENDING_REVIEW_FILE = "pending_review.json"
MANUAL_MATCHES_FILE = "manual_matches.json"


def _cache_key(title: str, artist: str) -> str:
    return f"{title.lower()}::{artist.lower()}"


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
    if os.path.exists(MANUAL_MATCHES_FILE):
        with open(MANUAL_MATCHES_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_manual_matches(matches: dict) -> None:
    with open(MANUAL_MATCHES_FILE, "w", encoding="utf-8") as f:
        json.dump(matches, f, ensure_ascii=False, indent=2)
    print(f"\n✅ Guardado en {MANUAL_MATCHES_FILE}")


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
    sp = get_spotify()

    # Filtrar las que ya tienen decisión en manual_matches
    to_review = [
        t for t in pending
        if _cache_key(t["title"], t["artist"]) not in manual_matches
    ]

    if not to_review:
        print("✅ Todas las pendientes ya tienen decisión en manual_matches.json.")
        return

    print(f"\n🎵 {len(to_review)} canción(es) pendientes de revisión\n")
    print("─" * 60)

    saved = 0
    for i, track in enumerate(to_review, 1):
        title  = track["title"]
        artist = track["artist"]
        key    = _cache_key(title, artist)

        print(f"\n[{i}/{len(to_review)}] {artist} — {title}")
        print("─" * 60)

        # Buscar opciones en Spotify
        print("  Buscando opciones en Spotify...")
        options = search_options(sp, title, artist)

        if options:
            for j, opt in enumerate(options, 1):
                print(f"  {j}. {opt['artist']} — {opt['name']}")
                print(f"     Album: {opt['album']}")
                print(f"     {opt['url']}")
        else:
            print("  (sin resultados en Spotify)")

        print()
        print("  Opciones:")
        for j in range(1, len(options) + 1):
            print(f"    {j} → elegir opción {j}")
        print("    m → ingresar ID de Spotify manualmente")
        print("    s → skip (no sincronizar esta canción)")
        print("    n → dejar para después (siguiente run)")

        while True:
            choice = input("\n  Tu elección: ").strip().lower()

            if choice in ("1", "2", "3") and int(choice) <= len(options):
                chosen = options[int(choice) - 1]
                manual_matches[key] = chosen["id"]
                print(f"  ✅ Guardado: {chosen['artist']} — {chosen['name']}")
                saved += 1
                break

            elif choice == "m":
                spotify_id = input("  ID de Spotify: ").strip()
                if spotify_id:
                    manual_matches[key] = spotify_id
                    print(f"  ✅ Guardado ID manual: {spotify_id}")
                    saved += 1
                    break
                else:
                    print("  ID vacío, intenta de nuevo.")

            elif choice == "s":
                manual_matches[key] = "skip"
                print(f"  ⏭ Skip guardado.")
                saved += 1
                break

            elif choice == "n":
                print("  ⏩ Dejado para después.")
                break

            else:
                print("  Opción inválida, intenta de nuevo.")

    if saved > 0:
        save_manual_matches(manual_matches)
        print(f"\n📝 {saved} decisión(es) guardadas en {MANUAL_MATCHES_FILE}")
        print("   Commitea el archivo y en el próximo sync se aplicarán.")
    else:
        print("\n  Sin cambios guardados.")


def clean(ytm_tracks_file: str = "ytm_tracks.json") -> None:
    """Elimina de manual_matches.json las entradas cuya canción ya no está en YTM."""
    if not os.path.exists(ytm_tracks_file):
        print(f"❌ No se encontró {ytm_tracks_file}. Corre sync.py primero.")
        return

    with open(ytm_tracks_file, encoding="utf-8") as f:
        ytm_tracks = json.load(f)

    ytm_keys = {_cache_key(t["title"], t["artist"]) for t in ytm_tracks}
    manual_matches = load_manual_matches()

    orphans = {k: v for k, v in manual_matches.items() if k not in ytm_keys}
    if not orphans:
        print("✅ No hay entradas huérfanas en manual_matches.json.")
        return

    print(f"\n🧹 {len(orphans)} entrada(s) huérfana(s) encontradas:\n")
    for k, v in orphans.items():
        print(f"  - {k} → {v}")

    confirm = input("\n¿Eliminar estas entradas? (s/n): ").strip().lower()
    if confirm == "s":
        for k in orphans:
            del manual_matches[k]
        save_manual_matches(manual_matches)
        print(f"✅ {len(orphans)} entrada(s) eliminadas.")
    else:
        print("  Cancelado.")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--clean":
        clean()
    else:
        review()