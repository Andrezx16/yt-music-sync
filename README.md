# 🎵 YouTube Music → Spotify Sync

Sincroniza automáticamente tus playlists de YouTube Music a Spotify una vez al día usando GitHub Actions (gratis).

---

## Archivos del proyecto

```
ytmusic-sync/
├── sync.py                        # Script principal de sincronización
├── config.json                    # Playlists a sincronizar
├── setup_ytmusic.py               # Genera ytmusic_auth.json (correr UNA vez)
├── setup_spotify.py               # Genera .spotify_cache (correr UNA vez)
├── setup_playlists.py             # Lista playlists de YTM y genera config.json
├── requirements.txt
├── .gitignore
└── .github/
    └── workflows/
        └── sync.yml               # GitHub Actions (corre a las 8AM Colombia)
```

---

## Configuración inicial (solo una vez)

### 1. Prerrequisitos

- Python 3.11+
- Cuenta de GitHub
- Cuenta de Google (YouTube Music)
- Cuenta de Spotify

Instala las dependencias:

```bash
pip install -r requirements.txt
```

---

### 2. Credenciales de YouTube Music (Google Cloud)

#### 2.1 Crear proyecto en Google Cloud

1. Ve a [console.cloud.google.com](https://console.cloud.google.com)
2. Crea un proyecto nuevo (ej: `ytmusic-sync`)

#### 2.2 Habilitar YouTube Data API v3

1. Ve a **APIs y servicios → Biblioteca**
2. Busca `YouTube Data API v3` → **Habilitar**

#### 2.3 Pantalla de consentimiento OAuth

1. Ve a **APIs y servicios → Pantalla de consentimiento de OAuth**
2. Tipo: **Externo** → Crear
3. Rellena nombre de app y correo → Guardar y continuar en todos los pasos

#### 2.4 Crear credencial OAuth

1. Ve a **APIs y servicios → Credenciales**
2. **+ Crear credenciales → ID de cliente OAuth**
3. Tipo: **Televisores y dispositivos de entrada limitada**
4. Copia el **Client ID** y **Client Secret**

#### 2.5 Generar ytmusic_auth.json

Edita `setup_ytmusic.py` con tus credenciales y ejecuta:

```bash
python setup_ytmusic_web.py
```

- Se mostrará un código en la terminal
- Ve a [google.com/device](https://google.com/device) en el navegador
- Ingresa el código y autoriza con tu cuenta de Google
- Se genera `ytmusic_auth.json`

---

### 3. Credenciales de Spotify

#### 3.1 Crear app en Spotify Developer

1. Ve a [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard)
2. **Create app**
3. Nombre: `ytmusic-sync`
4. Redirect URI: `http://127.0.0.1:8888/callback` (exactamente así)
5. Copia el **Client ID** y **Client Secret**

#### 3.2 Generar .spotify_cache

Edita `setup_spotify.py` con tus credenciales y ejecuta:

```bash
python setup_spotify.py
```

- Se abre el navegador para autorizar
- Después de autorizar, copia la URL de redirección completa y pégala en la terminal
- Se genera `.spotify_cache`

---

### 4. Elegir playlists a sincronizar

Edita `setup_playlists.py` con tus credenciales de Google Cloud y ejecuta:

```bash
python setup_playlists.py
```

- Lista todas tus playlists de YouTube Music
- Elige cuáles sincronizar
- Genera `config.json` automáticamente

También puedes editar `config.json` manualmente:

```json
{
  "playlists": [
    {
      "youtube_music_id": "PLxxxxxxxxxx",
      "spotify_name": "Mi Playlist en Spotify"
    }
  ]
}
```

---

### 5. Subir a GitHub

1. Crea un repositorio **privado** en GitHub (importante: privado)
2. Sube todos los archivos **excepto** `ytmusic_auth.json` y `.spotify_cache` (están en `.gitignore`)

```bash
git init
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/TU_USUARIO/ytmusic-sync.git
git push -u origin main
```

---

### 6. Configurar Secrets en GitHub

Ve a tu repo → **Settings → Secrets and variables → Actions → New repository secret**

Agrega estos secrets:

| Secret | Valor |
|--------|-------|
| `YTMUSIC_AUTH` | Contenido completo de `ytmusic_auth.json` |
| `YTMUSIC_CLIENT_ID` | Tu Client ID de Google Cloud |
| `YTMUSIC_CLIENT_SECRET` | Tu Client Secret de Google Cloud |
| `SPOTIFY_CLIENT_ID` | Tu Client ID de Spotify |
| `SPOTIFY_CLIENT_SECRET` | Tu Client Secret de Spotify |
| `SPOTIFY_REDIRECT_URI` | `http://127.0.0.1:8888/callback` |
| `SPOTIFY_CACHE_TOKEN` | Contenido completo de `.spotify_cache` |

---

### 7. Probar manualmente

1. Ve a tu repo en GitHub → pestaña **Actions**
2. Selecciona el workflow **"Sync YouTube Music → Spotify"**
3. Clic en **"Run workflow"**
4. Revisa los logs para confirmar que todo funciona

---

## Ejecución automática

El sync corre automáticamente **cada día a las 8:00 AM hora Colombia (UTC-5)**.

También puedes activarlo manualmente desde la **app de GitHub en el celular**:
- Abre el repo → Actions → Sync YouTube Music → Spotify → Run workflow

---

## Comportamiento del sync

- ✅ Agrega canciones nuevas que aparezcan en YouTube Music
- ✅ Elimina canciones que ya no estén en YouTube Music
- ✅ Mantiene el orden de las canciones
- ✅ Crea la playlist en Spotify si no existe
- ⚠️ Si una canción no se encuentra en Spotify, se omite y se registra en los logs

---

## Renovar tokens

**Spotify:** El refresh token no expira. Si algún día falla, vuelve a correr `setup_spotify.py` y actualiza el secret `SPOTIFY_CACHE_TOKEN`.

**YouTube Music:** El refresh token de Google tampoco expira en condiciones normales. Si falla (ej: revocaste el acceso), vuelve a correr `setup_ytmusic.py` y actualiza el secret `YTMUSIC_AUTH`.
