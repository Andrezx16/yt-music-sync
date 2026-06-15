import os
from dotenv import load_dotenv
import spotipy
from spotipy.oauth2 import SpotifyOAuth

load_dotenv()

sp = spotipy.Spotify(
    auth_manager=SpotifyOAuth(
        client_id=os.getenv("SPOTIFY_CLIENT_ID"),
        client_secret=os.getenv("SPOTIFY_CLIENT_SECRET"),
        redirect_uri=os.getenv("SPOTIFY_REDIRECT_URI"),
        scope="playlist-modify-public playlist-modify-private playlist-read-private"
    )
)

print(sp.current_user())

pl = sp._post(
    "me/playlists",
    payload={
        "name": "perm_test"
    }
)

print("Playlist:", pl["id"])

print(
    sp.playlist_add_items(
        pl["id"],
        ["spotify:track:4cOdK2wGLETKBW3PvgPWqT"]
    )
)