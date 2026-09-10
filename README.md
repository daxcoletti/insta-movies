# insta-movies

Pipeline que recorre los posts públicos de un perfil de Instagram y extrae, con la
Claude API, **lo que ese perfil recomienda** — armando una planilla que se mantiene
sola con un cron diario.

Vienen dos variantes construidas sobre la misma infraestructura:

| Pipeline | Perfil de ejemplo | Qué produce |
|----------|-------------------|-------------|
| 🎬 **Películas** | `juan.amonda` | `<perfil>_peliculas.csv/.xlsx` — fecha, película, año, link al post, IMDb, torrent, caption |
| 🎵 **Música** | `owencutts` | `<perfil>_canciones.csv/.xlsx` — fecha, canción, artista, álbum, año, link, Spotify, caption + una **playlist de Spotify** acumulativa |

Ambas suben la planilla a Google Drive con `rclone` y avisan por notificación de
escritorio si hubo novedades, si no hubo, o si la corrida falló.

---

## Cómo funciona

1. **Sesión de Instagram** — se importa una vez desde las cookies del navegador
   (`import_cookies.py`) y queda guardada como sesión propia de instaloader, así el
   cron corre headless.
2. **Listado de posts** — por el endpoint privado `/api/v1/feed/user/`, porque
   instaloader lista la timeline con un `doc_id` de GraphQL que Instagram deprecó
   (ver *Limitaciones* abajo).
3. **Identificación** — en capas, cortando apenas una resuelve:
   caption → Claude (texto); si no, thumbnail → Claude Vision; y en el pipeline de
   música, si tampoco, se transcribe el audio del Reel y Claude lee la transcripción.
4. **Canonización** — IMDb (películas) o Spotify (música) para normalizar el dato.
5. **Incremental** — el cron diario solo mira los posts nuevos: para de paginar
   apenas ve uno ya conocido, así en un día normal hace una sola request.

## Uso rápido

```bash
# 0. (una vez) sesión de IG desde el navegador logueado en ESTA máquina
source ./setup.sh juan.amonda --chrome

cp .env.example .env && chmod 600 .env   # completá tus keys acá

# 1. Corrida completa
python build_spreadsheet.py juan.amonda --login <TU_USUARIO_IG>
python find_torrents.py juan.amonda

# 2. Día a día (lo dispara el cron)
python daily_update.py juan.amonda
```

Para el pipeline de música: `python spotify_auth.py` una vez, después
`build_music_spreadsheet.py` / `daily_music_update.py`.

## Requisitos

Python 3.12, `ffmpeg` (solo para la capa de audio) y las dependencias de
`requirements.txt`. Opcional: `rclone` configurado para la subida a Drive.

## Configuración

Todas las credenciales van en `.env` (gitignored). Copiá `.env.example` y completalo
— ahí está la lista completa con comentarios. Ninguna key se commitea.

## Limitaciones conocidas

- **GraphQL deprecado:** `Profile.get_posts()` de instaloader devuelve
  `400 "invalid request"`; no se arregla actualizando. De ahí el fallback al endpoint
  privado ([issue #2689](https://github.com/instaloader/instaloader/issues/2689)).
- **`feedback_required`:** con uso sostenido, Instagram marca la cuenta y el endpoint
  privado empieza a devolver `400 {"message":"feedback_required","spam":true}`. No es
  un bug del código: hay que espaciar las corridas y esperar a que se libere.
- **IPs de datacenter:** Instagram suele bloquear VPS, así que conviene correrlo desde
  una máquina residencial.
- **Sesiones:** la sesión guardada tiene vida propia y en algún momento Instagram la
  invalida; se regenera con `./venv/bin/python import_cookies.py chrome <usuario>`.

## Nota sobre `find_torrents.py`

Busca torrents de películas en una API pública. Es un uso dual: está pensado para uso
personal — verificá la legalidad en tu jurisdicción y preferí fuentes legales o de
dominio público cuando puedas. El resto del pipeline funciona sin ese paso.

## Documentación completa

[`CLAUDE.md`](CLAUDE.md) tiene el detalle de cada componente, el formato de las
planillas, la configuración del cron y un registro largo de los *gotchas* encontrados
(rate-limits, perfiles de Chrome, migración de la API de Spotify, matching de títulos).
