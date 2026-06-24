# insta-movies

Descarga todos los posts públicos de un perfil de Instagram y extrae los títulos
de las películas recomendadas en cada post, usando la Claude API.

## Objetivo

Dado un perfil de Instagram dedicado a recomendar películas (default:
`juan.amonda`), producir una lista deduplicada y ordenada de los títulos
recomendados en `movies.txt`.

## Componentes

| Archivo             | Rol |
|---------------------|-----|
| `setup.sh`            | Crea `venv/`, instala dependencias, autentica y descarga los posts. |
| `import_cookies.py`   | Importa la sesión desde el navegador local (`browser_cookie3`). |
| `import_sessionid.py` | Construye la sesión desde una cookie `sessionid` (vía `IG_SESSIONID`). |
| `download_posts.py`   | Descarga los posts listándolos por `/api/v1/feed/user/` (fallback al GraphQL roto). |
| `extract_movies.py`   | Recorre las imágenes descargadas y extrae los títulos vía Claude. |
| `build_spreadsheet.py`| Arma una planilla (fecha, película, año, link, caption) con un renglón por post. |
| `find_torrents.py`    | Busca el mejor torrent de cada película (vía Claude + apibay) y lo agrega a la planilla. |
| `daily_update.py`     | Actualización incremental: detecta posts nuevos, completa IMDb/torrent y sube a Drive. |
| `run_daily.sh`        | Wrapper de cron: carga `.env`, activa el venv y corre `daily_update.py`; loguea a `daily.log`. |
| `requirements.txt`    | Dependencias: `instaloader`, `anthropic`, `browser_cookie3`, `openpyxl`, `requests`. |

Columnas de la planilla (`<PERFIL>_peliculas.csv` / `.xlsx`):
**A** Fecha · **B** Película · **C** Año · **D** Link (post) · **E** IMDb · **F** Torrent · **G** Caption.

## Flujo

```bash
# 1. Setup + descarga (dejá el venv activo con "source")
#    Instagram bloquea la descarga anónima (403). Recomendado: cookies del navegador.
source ./setup.sh juan.amonda --chrome

# 2. Extracción
export ANTHROPIC_API_KEY=sk-ant-...
python extract_movies.py juan.amonda

# 3. (Opcional) Planilla por post: fecha, película, año, link, caption
python build_spreadsheet.py juan.amonda --login <TU_USUARIO_IG>

# 4. (Opcional) Agregar el mejor torrent de cada película
python find_torrents.py juan.amonda
```

Para el día a día no hace falta correr los pasos sueltos: `daily_update.py` los hace
de forma incremental (ver abajo) y el cron lo dispara una vez por día.

`setup.sh <PERFIL> [AUTH]` corre:
`instaloader --no-videos --no-video-thumbnails --no-metadata-json --dirname-pattern="{profile}" [--login=USUARIO] <PERFIL>`
→ las imágenes quedan en `./<PERFIL>/`, con el caption de cada post en un `.txt`
de igual nombre base.

`[AUTH]` puede ser:
- `--chrome` / `--firefox` / `--chromium` / `--brave` / `--edge` / `--opera [USUARIO_IG]`:
  importa la sesión ya iniciada en ese navegador vía `import_cookies.py`
  (usa `browser_cookie3`). **Solo funciona si corrés esto EN la misma máquina
  donde el navegador está logueado en Instagram** — lee el perfil de Chrome local.
- `--sessionid <USUARIO_IG>` (con `IG_SESSIONID` en el entorno): construye la
  sesión desde tu cookie `sessionid` vía `import_sessionid.py`. Útil cuando el
  navegador logueado está en **otra** máquina. Opcionalmente `IG_DS_USER_ID` /
  `IG_CSRFTOKEN`.
- `<USUARIO_IG>`: login usuario/contraseña; instaloader pide la contraseña con
  `getpass`, que **requiere una terminal interactiva** (sin TTY lanza `EOFError`).
- vacío: descarga anónima → casi siempre 403.

> ⚠️ **Hallazgo clave (gotcha):** el modo `--chrome` lee las cookies del Chrome de
> la **máquina donde corre el script**. Si ese servidor no tiene Instagram logueado
> en su Chrome (caso típico al ejecutar en un VPS), `browser_cookie3` devuelve cero
> cookies de instagram.com y el login falla con 401, sin importar cuánto se espere.
> Soluciones: correr todo en la máquina con el navegador logueado, o usar
> `--sessionid` trayendo la cookie. Además, las IPs de datacenter/VPS suelen estar
> bloqueadas por Instagram, así que aun con cookie válida la descarga puede dar 401.

Notas del script:
- No usa `set -e` (al hacer `source`, un errexit cerraría tu shell); maneja los
  errores explícitamente y aborta sin imprimir el banner de "Listo" si algo falla.

### `import_cookies.py`

Lee las cookies de `instagram.com` del navegador con `browser_cookie3`, valida con
`Instaloader.test_login()` y guarda la sesión en `~/.config/instaloader/session-<user>`.
Imprime el usuario detectado en stdout (lo captura `setup.sh`). Falla si: no hay
sesión iniciada en el navegador, el llavero/keyring está bloqueado (Linux), o
Instagram está rate-limiteando (401 "Please wait a few minutes…").

> Gotcha observado: tras varios intentos seguidos, Instagram devuelve
> `401 "Please wait a few minutes before you try again."` (rate-limit). No es un
> problema de las cookies; hay que esperar unos minutos y reintentar.

> Gotcha (perfiles de Chrome): `browser_cookie3.chrome()` lee por defecto el perfil
> `Default`, pero muchas instalaciones usan perfiles con nombre (`Profile 6`, etc.)
> y puede que `Default` ni exista. `import_cookies.py` recorre **todos** los perfiles
> del navegador y elige el primero con cookie `sessionid`. Para mapear perfil↔cuenta:
> mirá `~/.config/google-chrome/Local State` (`profile.info_cache`).

> Gotcha (stdout): `Instaloader.save_session_to_file()` loguea "Saved session to ..."
> a **stdout**. Como `setup.sh` captura el stdout de `import_cookies.py` como nombre
> de usuario, ese log contaminaba `--login`. Por eso `import_cookies.py` lo redirige
> a stderr y deja en stdout **solo** el usuario.

### Descarga: `download_posts.py` (fallback al GraphQL roto)

> ⚠️ **Hallazgo clave (mediados 2026):** instaloader (incl. la última, 4.15.1) lista
> los posts de un perfil con un `doc_id`/`query_hash` de GraphQL que Instagram
> **deprecó**. El login, la resolución del perfil y la descarga de cada post andan,
> pero `Profile.get_posts()` (y cualquier acceso a metadata como `profile.mediacount`)
> revienta con `400 Bad Request - "fail" status, message "invalid request"` en
> `/graphql/query`. No se arregla actualizando instaloader.
>
> Workaround ([issue #2689](https://github.com/instaloader/instaloader/issues/2689)):
> listar los posts por el endpoint privado `/api/v1/feed/user/<userid>/` (con headers
> `X-IG-App-ID`/`X-ASBD-ID` + User-Agent de navegador), construir cada `Post` con
> `Post.from_iphone_struct` y delegar la bajada en `Instaloader.download_post`.
> Eso hace `download_posts.py`. **No accede a `profile.mediacount`** porque también
> dispara el GraphQL roto.
>
> `setup.sh` intenta primero la vía normal (instaloader) y, si falla teniendo sesión,
> cae automáticamente a `download_posts.py` — así se auto-cura si Instagram repara el
> endpoint.

> Gotcha (Reels): el perfil `juan.amonda` publica casi todo como **Reels (videos)**,
> no fotos. `download_posts.py` corre con `download_video_thumbnails=True` para dejar
> un `.jpg` (la portada del Reel) por post, así `extract_movies.py` —que itera sobre
> imágenes— tiene su imagen y el caption asociado. El título suele estar en el caption.

## Lógica de extracción (`extract_movies.py`)

Para cada imagen `.jpg`/`.png` de la carpeta del perfil:

1. **Caption primero**: si existe el `.txt` con el mismo nombre base y tiene
   contenido, se le pasa a Claude (texto) para extraer el título.
2. **Fallback a Vision**: si el caption está vacío o Claude devuelve el centinela
   `NONE`, se hace una llamada con la imagen (Claude Vision).

- Modelo: `claude-sonnet-4-6` (texto y visión), fijado en la constante `MODEL`.
  Nota: la guía de la Claude API recomienda `claude-opus-4-8` por defecto; acá se
  usa Sonnet 4.6 a pedido explícito.
- El prompt instruye responder SOLO con el título, o `NONE` si no hay película.
- Progreso línea a línea indicando el método usado: `caption` / `imagen` /
  `no identificado`.
- Salida final: `movies.txt` (títulos únicos, orden alfabético case-insensitive)
  + resumen con cantidad de películas únicas y posts no identificados.
- `ANTHROPIC_API_KEY` se lee del entorno; si falta, el script sale con error claro.

## Planilla (`build_spreadsheet.py`)

Lista los posts por `/api/v1/feed/user/` (igual que `download_posts.py`) y arma una
fila por post con: **Fecha** (`taken_at` → `YYYY-MM-DD`), **Película**, **Año**,
**Link** (`https://www.instagram.com/p/<code>/`) y **Caption**. Salida:
`<PERFIL>_peliculas.csv` (UTF-8 con BOM) y `<PERFIL>_peliculas.xlsx` (si hay `openpyxl`;
con links clicables y fila de encabezado fija).

- Título y año salen del caption por regex, no por la API (costo cero). El año es el
  primer número de 4 dígitos **dentro de un paréntesis** — cubre `(2024)`,
  `(2015, Director)`, `(Título original, 2015)`, `(1973, Fellini)`. El título es lo
  que va antes del primer `(`.
- No usa Claude: a diferencia de `extract_movies.py` (que normaliza el título), acá el
  nombre es el textual del caption. En la corrida de `juan.amonda`: 383 posts, solo 1
  sin año (un post de colaboración que no es una recomendación individual).

## Torrents (`find_torrents.py`)

> ⚠️ Uso dual: busca torrents de películas con copyright. Es para uso personal;
> verificá la legalidad en tu jurisdicción y preferí fuentes legales / dominio
> público cuando puedas.

Lee `<PERFIL>_peliculas.csv`, para cada película busca el mejor torrent y reescribe
la planilla con **columna E = `Torrent`** (un magnet link; `Caption` pasa a F).
Necesita `ANTHROPIC_API_KEY`.

Cómo elige el candidato:
1. **Resuelve el título original/inglés con Claude** (en lotes de 40), porque los
   títulos de la planilla están en español y los releases usan el título original o
   inglés (p. ej. `Amigos intocables` → *The Intouchables*, `La princesa Mononoke` →
   *Princess Mononoke*).
2. Busca en **apibay** (API de The Pirate Bay; YTS está bloqueado por DNS en algunos
   entornos) probando `inglés+año`, `original+año`, `español+año` y variantes
   recortadas del título.
3. Filtra a categorías de **películas** (excluye TV) y acepta un candidato solo si
   coincide el **año** o la mayoría de las **palabras del título** (evita falsos
   positivos). Entre los aceptados elige por **seeders** (con bonus por 1080p/4K y
   por año en el nombre). Arma el magnet con trackers públicos.

> Gotchas de matching (aprendidos en `juan.amonda`, 357/383 ≈ 93% de aciertos):
> - **Apóstrofos rompen apibay**: `Winter's Bone 2010` devuelve 0; hay que mandar
>   `Winters Bone 2010` (`clean_query` quita apóstrofos rectos y tipográficos).
> - **Subtítulos largos no matchean**: `Dr. Strangelove or: How I Learned…` falla; se
>   prueba también el título recortado antes del `:` (`queries_for`).
> - Lo que queda sin torrent son cortometrajes, films de festival, estrenos muy
>   recientes (aún sin release) o alguna resolución de título errónea de Claude.

## Automatización diaria (`daily_update.py` + cron + Google Drive)

El perfil publica ~1 película por día. `daily_update.py` mantiene la planilla al día
de forma **incremental**:

1. Recorre el feed (`/api/v1/feed/user/`, newest-first) y junta solo los posts cuyo
   shortcode no está ya en `<PERFIL>_peliculas.csv`. **Para de paginar apenas ve un
   post conocido** → en un día normal hace una sola request.
2. Por cada post nuevo: baja el thumbnail, parsea película/año, resuelve el título
   original/inglés (Claude), busca **IMDb** y el mejor **torrent**.
3. **Backfill**: completa la columna `IMDb` (y `Torrent`) en filas viejas que no la
   tengan. La 1ª corrida puebla IMDb en toda la planilla; después no re-busca
   torrents de los misses ya confirmados (eso es lo lento) — solo IMDb faltante.
4. Reescribe CSV + XLSX y los **sube a Google Drive con rclone** (`rclone copy`
   sobreescribe el mismo archivo in-place: mismo file ID, sin duplicar, link estable).

- **IMDb**: vía la suggestion API pública `v3.sg.media-imdb.com/suggestion/x/<q>.json`
  (sin key); filtra a títulos `tt…`, prefiere match por año y tipo película. Construye
  `https://www.imdb.com/title/tt…/`. Cobertura en `juan.amonda`: 380/383.
- **Google Drive**: remote rclone `drive:`; destino en `DRIVE_DEST` (default
  `drive:insta-movies`). El OAuth client viejo estaba borrado; se limpió el
  `client_id`/`client_secret` para usar el cliente por defecto de rclone y se
  reconectó con `rclone config reconnect drive:`.

### Cron y secretos

- `run_daily.sh` carga `.env`, activa el venv, corre `daily_update.py` y loguea todo a
  `daily.log` (con rotación a `.1` si supera 5 MB).
- `.env` (chmod 600, **gitignored**): `ANTHROPIC_API_KEY`, `IG_LOGIN_USER`, `PROFILE`,
  `DRIVE_DEST`.
- crontab del usuario (timezone America/Argentina/Buenos_Aires):
  `30 23 * * * /home/dax/dev/insta-movies/run_daily.sh`
- **Cookies vs sesión (importante):** Chrome es solo el *bootstrap* de una vez. En el
  `setup.sh --chrome`, `import_cookies.py` lee las cookies de Chrome
  (`~/.config/google-chrome/Profile 6/Cookies`) y guarda una **sesión independiente**
  en `~/.config/instaloader/session-<user>` (~472 bytes). El cron diario hace
  `load_session_from_file(<user>)` → usa **esa copia**, nunca Chrome
  (`daily_update.py` no importa `browser_cookie3`). Por eso:
    - corre headless: Chrome puede estar cerrado o deslogueado de Instagram;
    - esa sesión tiene vida propia: Instagram puede invalidarla con el tiempo. Cuando
      pase, el cron fallará con error de login en `daily.log` → regenerar con
      `source ./setup.sh <PERFIL> --chrome` (vuelve a leer Chrome y reescribe la sesión).

> Pendiente menor: 3 filas sin IMDb (post de colaboración + 2 de nicho) se reintentan
> cada día porque "vacío" = "sin resolver". Es barato (3 lookups); si molesta, marcar
> los misses para no reintentarlos.

## Notas / posibles mejoras

- El emparejamiento caption↔imagen es por *stem* del archivo. instaloader nombra
  los posts con timestamp; en posts multi-imagen los archivos llevan sufijo
  (`_1`, `_2`, …) y solo el primero comparte stem con el `.txt`, así que las
  imágenes secundarias caen al camino de Vision.
- Las llamadas son secuenciales. Para perfiles grandes se podría paralelizar o
  usar la Batch API.
- Errores de API por post se reportan y no abortan la corrida.
