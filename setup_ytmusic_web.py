"""
setup_ytmusic_web.py — Genera ytmusic_auth.json usando OAuth web de Google.
Login normal con tu cuenta de Google, igual que TuneMyMusic.
"""

import json
import os
from dotenv import load_dotenv
from google_auth_oauthlib.flow import Flow

load_dotenv()

CLIENT_ID     = os.environ.get("YTMUSIC_CLIENT_ID_WEB")
CLIENT_SECRET = os.environ.get("YTMUSIC_CLIENT_SECRET_WEB")

if not CLIENT_ID or not CLIENT_SECRET:
    print("❌ Faltan credenciales. Agrega al .env:")
    print("   YTMUSIC_CLIENT_ID_WEB=...")
    print("   YTMUSIC_CLIENT_SECRET_WEB=...")
    raise SystemExit(1)

REDIRECT_URI = "http://127.0.0.1:8080/callback"
SCOPES = ["https://www.googleapis.com/auth/youtube"]

client_config = {
    "web": {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": [REDIRECT_URI],
    }
}

flow = Flow.from_client_config(
    client_config,
    scopes=SCOPES,
    redirect_uri=REDIRECT_URI,
)

auth_url, _ = flow.authorization_url(
    access_type="offline",
    prompt="consent",
)

print("🔐 Abre este enlace en tu navegador y autoriza con tu cuenta de Google:")
print(f"\n{auth_url}\n")

# Servidor local para capturar el callback
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

code_holder = {}

class CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        if "code" in params:
            code_holder["code"] = params["code"][0]
            self.send_response(200)
            self.send_header("Content-type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"""
                <html><body style='font-family:sans-serif;text-align:center;padding:50px'>
                <h2>&#x2705; Autorizado correctamente</h2>
                <p>Puedes cerrar esta ventana y volver a la terminal.</p>
                </body></html>
            """)
        else:
            self.send_response(400)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # Silenciar logs del servidor

server = HTTPServer(("127.0.0.1", 8080), CallbackHandler)

print("⏳ Esperando autorización en el navegador...")
webbrowser.open(auth_url)

# Esperar hasta recibir el código
server.handle_request()

if "code" not in code_holder:
    print("❌ No se recibió el código de autorización.")
    raise SystemExit(1)

# Intercambiar código por token
flow.fetch_token(code=code_holder["code"])
creds = flow.credentials

# Guardar en formato compatible con ytmusicapi
token_data = {
    "scope": " ".join(SCOPES),
    "token_type": "Bearer",
    "access_token": creds.token,
    "refresh_token": creds.refresh_token,
    "expires_at": creds.expiry.timestamp() if creds.expiry else 0,
    "expires_in": 3600,
    "client_id": CLIENT_ID,
    "client_secret": CLIENT_SECRET,
}

with open("ytmusic_auth.json", "w") as f:
    json.dump(token_data, f, indent=2)

print("\n✅ ytmusic_auth.json generado correctamente.")
print("   Guarda su contenido como secret YTMUSIC_AUTH en GitHub.")
