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
| `add_posts.py`        | Agrega posts sueltos a la planilla **por link**, vía `/api/v1/media/<id>/info/`. Escape hatch para cuando Instagram bloquea el listado. |
| `run_daily.sh`        | Wrapper de cron: carga `.env`, activa el venv y corre `daily_update.py`; loguea a `daily.log`. |
| `notify_fail.sh`      | Notificación de escritorio (`notify-send -u critical`) cuando un wrapper de cron falla. |
| `notify_blocked.sh`   | Notificación cuando Instagram bloquea el listado (código 2): ícono de advertencia, no de error — no hay nada que arreglar. |
| `notify_new.sh`       | Notificación de escritorio cuando un cron detecta posts nuevos (ícono info, misma urgencia). |
| `notify_ok.sh`        | Notificación de escritorio cuando un cron termina OK sin posts nuevos (ícono tilde, misma urgencia). |
| `test_notify.sh`      | Prueba manual de la notificación + diagnóstico (estado de "no molestar"). |
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
> del navegador. Como puede haber **varias cuentas de IG logueadas** (una por perfil),
> acepta un 2º argumento con el usuario **esperado** (p. ej. `daxcoletti`): verifica
> online el dueño de cada cookie (`/api/v1/users/<ds_user_id>/info/`, mismo family
> que el feed) y **solo usa la de esa cuenta**; las demás las ignora con aviso.
> `setup.sh` pasa ese usuario desde el 3er argumento o, si falta, desde
> `IG_LOGIN_USER` de `.env`. Sin usuario esperado: primera sesión encontrada (legacy).
> Para mapear perfil↔cuenta: mirá `~/.config/google-chrome/Local State`
> (`profile.info_cache`). Ojo: la cookie refleja la cuenta **activa** en instagram.com
> de ese perfil de Chrome — si está switcheada a otra cuenta (p. ej. `trans.ti`),
> hay que cambiar a la esperada en el navegador y reintentar.

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

### Resiliencia del listado (`ig_feed.py`)

Instagram viene rompiendo **una vía por vez**, así que listar posts dejó de ser un
endpoint fijo y pasó a ser una **cadena de fallback**. Toda la lógica de red vive en
`ig_feed.py` (los dos pipelines la comparten: un cambio se arregla en un solo lugar).

`fetch_new_items()` prueba en orden y se queda con la primera que responda:

| # | Endpoint | Alcance | Sobrevive al soft-block |
|---|----------|---------|--------------------------|
| 1 | `/api/v1/feed/user/<id>/` | todo el perfil, paginado | ✗ |
| 2 | `/api/v1/users/web_profile_info/` | los ~12 últimos, sin paginar | a veces (da 429 aparte) |

Los ~12 del fallback alcanzan de sobra para el incremental (el perfil publica ~1 post
por día, y el cron corre cada 3). Si **todos** los que trae son nuevos avisa por
stderr, porque puede haber más atrás que no llega a ver.

Aparte de la cadena:

- **`FeedUnavailable`** distingue "Instagram no me deja listar" de "la sesión venció"
  o "el perfil no existe" — que era justo la confusión de jul-2026.
- **El bloqueo no se reintenta.** `_looks_blocked()` lo detecta leyendo el *cuerpo*
  (Instagram lo manda con status 400, así que `raise_for_status()` lo tapaba), y
  aborta esa estrategia al instante: reintentar no cambia el resultado y **renueva el
  flag**. Lo que sí se reintenta, con backoff exponencial + jitter, es lo transitorio
  (429 y 5xx).
- **`iter_feed_items()` no tiene fallback** porque necesita el listado completo y el
  perfil web solo da 12. Pero `build_music_spreadsheet.py` es reanudable: si el scan
  se corta a la mitad, guarda lo procesado y sale con 2; al reintentar sigue desde
  `<PERFIL>_seen.txt`.
- **`fetch_item_by_shortcode()`** trae un post por `/api/v1/media/<id>/info/`, que
  **sigue respondiendo 200 con el feed bloqueado**. Es lo que usa `add_posts.py`.

Código de salida **2** = bloqueado, en los dos dailies y en el build de música. Los
wrappers lo notifican con `notify_blocked.sh` (ícono de advertencia) en vez de
`notify_fail.sh`: no hay nada que arreglar.

### Agregar posts a mano (`add_posts.py`)

Cuando el listado está bloqueado pero necesitás la planilla al día, `add_posts.py`
recibe los links que ves en el navegador y hace **lo mismo que el daily con un post
nuevo** (parseo del caption, IMDb, torrent, CSV + XLSX, subida a Drive); lo único que
cambia es de dónde salen los posts.

```bash
python add_posts.py https://www.instagram.com/p/ABC123/ DEF456
python add_posts.py --profile juan.amonda --no-drive <links...>
```

Acepta link de post, de reel o el shortcode pelado; saltea los que ya están en la
planilla y verifica que el post sea del perfil correcto antes de agregarlo.

### Cron y secretos

- `run_daily.sh` carga `.env`, activa el venv, corre `daily_update.py` y loguea todo a
  `daily.log` (con rotación a `.1` si supera 5 MB).
- `.env` (chmod 600, **gitignored**): `ANTHROPIC_API_KEY`, `IG_LOGIN_USER`, `PROFILE`,
  `DRIVE_DEST`.
- crontab del usuario (timezone America/Argentina/Buenos_Aires):
  `30 23 */3 * * /home/dax/dev/insta-movies/run_daily.sh`
  **Cada 3 días, no a diario:** el perfil no publica todos los días y espaciar las
  corridas baja la chance del soft-block de Instagram. Como la actualización es
  incremental, una corrida levanta todo lo publicado desde la anterior. (Ojo: `*/3`
  es día-del-mes, así que el salto 31→1 queda de 1 día; no afecta al resultado.)
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
      Para regenerar **solo la sesión** sin re-descargar posts:
      `./venv/bin/python import_cookies.py chrome <user>`.

> ⚠️ **Gotcha (sesión inválida ≠ "perfil no existe", jul-2026):** cuando Instagram
> invalida la sesión, `Profile.from_username()` falla con
> `ProfileNotExistsException: Profile X does not exist.` — el perfil existe; es la
> consulta autenticada la que devuelve 401/429. Señal inequívoca: los **dos** crons
> (películas y música, perfiles distintos) fallan el mismo día con ese error.
> Probando la sesión a mano se ve el `401 "Please wait a few minutes"` / `429`.
> Fix: regenerar la sesión (arriba). La sesión original duró ~6 meses; una
> regeneración de jul-2026 duró solo 5 días.

> ⚠️ **Gotcha (`feedback_required` ≠ sesión vencida, sep-2026):** desde el 2026-09-03
> los dos crons fallan con `400 Bad Request` en `/api/v1/feed/user/<id>/`. Acá la
> sesión está **sana**: el login carga, `Profile.from_username()` resuelve y
> `import_cookies.py` verifica la cookie OK — lo que falla es solo el endpoint del
> feed. El cuerpo de la respuesta lo aclara (hay que mirarlo, `raise_for_status()`
> lo tapa):
>
> ```json
> {"message":"feedback_required","spam":true,"feedback_title":"Try Again Later",
>  "feedback_message":"We limit how often you can do certain things on Instagram…"}
> ```
>
> Es un **soft-block por actividad automatizada**, a nivel cuenta+IP, no un bug del
> código ni algo que arregle regenerar la sesión. Por eso también falla `setup.sh`:
> el listado por GraphQL cae (deprecado, esperado) y el fallback `download_posts.py`
> pega contra el mismo endpoint marcado.
>
> **Comprobado (sep-2026):** entrar a instagram.com desde el navegador y reimportar
> las cookies **no lo destraba** — la sesión nueva verifica OK contra
> `/api/v1/users/<id>/info/` y el feed sigue dando `feedback_required`. Confirma que
> el flag es de la cuenta/IP y no del token.
>
> Qué hacer: **esperar** (se libera solo; cada reintento lo renueva) y mientras tanto
> usar `add_posts.py` para agregar por link los posts que falten. Los crons ya no
> hace falta pararlos a mano: corren cada 3 días y una corrida bloqueada sale con
> código 2 sin tocar la planilla. Diagnóstico rápido:
>
> ```bash
> ./venv/bin/python -c "import instaloader,ig_feed;L=ig_feed.build_loader('<user>');\
> p=instaloader.Profile.from_username(L.context,'<perfil>');\
> print(L.context._session.get(f'https://www.instagram.com/api/v1/feed/user/{p.userid}/',\
> headers={**ig_feed.HEADERS},params={'count':1}).text[:300])"
> ```

- **Notificación de falla (desktop):** si `daily_update.py` / `daily_music_update.py`
  terminan ≠0, los wrappers llaman a `notify_fail.sh` → `notify-send -u critical`
  (persiste hasta cerrarla). Como cron corre sin entorno gráfico, el script setea
  `DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$(id -u)/bus` y `DISPLAY=:0`.
  Prueba manual: `./test_notify.sh`.
- **Notificación de novedades (desktop):** los wrappers capturan la salida de la
  corrida (`tee` a un temporal) y, si hay líneas `>> nuevo …` (ambos dailies imprimen
  una por post detectado), llaman a `notify_new.sh` con el detalle (fecha + título).
  Misma urgencia `critical` para que persista hasta la mañana; ícono
  `dialog-information` para distinguirla de una falla. Aplica el mismo gotcha del
  "no molestar" de arriba.
- **Notificación de "sin novedades" (desktop):** si la corrida termina OK pero no hubo
  posts nuevos, los wrappers llaman a `notify_ok.sh` (ícono `emblem-default`, misma
  urgencia) con el resumen de la planilla. Así **toda** corrida de cron notifica algo:
  falla (`notify_fail.sh`), novedades (`notify_new.sh`) o sin novedades (`notify_ok.sh`);
  si a la mañana no hay ninguna notificación, el cron no corrió.
  > Gotcha: el modo **"no molestar"** de MATE (y del indicador ayatana) suprime los
  > popups en silencio — el daemon acepta la notificación (asigna id) pero no la
  > muestra. `test_notify.sh` chequea `org.mate.NotificationDaemon do-not-disturb` y
  > `org.ayatana.indicator.notifications do-not-disturb` y avisa si están en `true`.

> Pendiente menor: 3 filas sin IMDb (post de colaboración + 2 de nicho) se reintentan
> cada día porque "vacío" = "sin resolver". Es barato (3 lookups); si molesta, marcar
> los misses para no reintentarlos.

## Pipeline de música (`@owencutts`)

Variante del pipeline pensada para perfiles que **hablan de canciones** (default:
`owencutts`, *"Old Music Friday"*). En vez de un título de película saca **canción +
artista**, arma `<PERFIL>_canciones.csv/.xlsx` y mantiene una **playlist de Spotify**
acumulativa. Reutiliza toda la infra de Instagram (sesión/cookies, el workaround del
GraphQL, cron + Drive); lo nuevo es el **núcleo de identificación** y **Spotify**.

| Archivo | Rol |
|---------|-----|
| `ig_feed.py`               | **Capa de red compartida por los dos pipelines**: lista posts con una cadena de fallback de endpoints, trae posts sueltos por shortcode, y extrae caption/fecha/shortcode + **URLs de thumbnail y video** de cada item. |
| `identify_songs.py`        | Identifica `canción + artista` por post **en capas**, parando apenas una funciona. |
| `transcribe.py`            | Capa 3: baja el mp4 del Reel y lo transcribe vía APIs cloud (Groq→Deepgram→Gladia). |
| `spotify_sync.py`          | Canoniza cada tema (búsqueda Spotify → título/artista/álbum/año/link) y mantiene la playlist. |
| `spotify_auth.py`          | Autorización OAuth inicial (una vez). |
| `build_music_spreadsheet.py` | Batch: recorre todo el perfil, identifica, canoniza, escribe la planilla y sincroniza la playlist. |
| `daily_music_update.py`    | Incremental (posts nuevos + backfill de Spotify) + playlist + Drive. |
| `run_daily_music.sh`       | Wrapper de cron (loguea a `daily_music.log`). |

Columnas: **A** Fecha · **B** Canción · **C** Artista · **D** Álbum · **E** Año ·
**F** Link (post) · **G** Spotify · **H** Caption.

### Identificación en capas (`identify_songs.py`)

Por cada post, en orden y cortando apenas una resuelve:
1. **Caption** → Claude (texto) extrae `{"cancion", "artista"}` o `NONE`.
2. **Imagen** → Claude Vision sobre el thumbnail del Reel (texto sobreimpreso).
3. **Audio** → se baja el mp4 (URL directa del feed, sin instaloader), se extrae el
   audio con `ffmpeg` (mp3 mono) y se transcribe; Claude lee la transcripción.

> ⚠️ **Gotcha clave:** la Claude API **no procesa audio**. Por eso la capa 3 transcribe
> primero y recién después Claude lee **texto**. La transcripción **reutiliza el enfoque
> y las API keys de `~dax/dev/meet-transcriptions`** (en el equipo `agente`): rota entre
> **Groq (`whisper-large-v3`) → Deepgram (`nova-3`) → Gladia → whisper local en `agente`**,
> usando el primero que responda y saltando al siguiente ante un `429`. Detalles:
> - **Groq admite varias keys** para más cuota: `GROQ_API_KEY` (acepta varias separadas
>   por coma) + `GROQ_API_KEY_2`, `_3`, … `_groq` las rota cuando una da 429.
> - **Fallback final sin cuota:** si las 3 APIs cloud se agotan, se transcribe en `agente`
>   con el **whisper CLI de OpenAI** (`~/.local/bin/whisper`, modelo `WHISPER_SSH_MODEL`,
>   default `large-v3-turbo`) pipeando el audio por SSH. Se configura con `WHISPER_SSH_HOST`
>   (default `agente`; vaciar para desactivar). Requiere acceso SSH sin password al equipo.
> - Keys/host en `.env`: `GROQ_API_KEY[_n]`, `DEEPGRAM_API_KEY`, `GLADIA_API_KEY`,
>   `WHISPER_SSH_HOST`, `WHISPER_SSH_MODEL`. Requiere `ffmpeg`. La capa 3 se salta con `--no-audio`.

> ⚠️ **Gotcha (rate-limit de Spotify):** la API de búsqueda de Spotify tiene un límite
> que, ante uso intenso (varias corridas full el mismo día), devuelve `429` con un
> `Retry-After` de **horas** (se vieron ~20 h). Por eso `get_spotify()` crea el cliente con
> `retries=0`: ante un 429 tira excepción al instante (la capturamos y degradamos: la
> canción se guarda sin datos de Spotify y el `daily` la rellena después) en vez de
> **dormir** el `Retry-After` y colgar el proceso. `build_music_spreadsheet.py` guarda
> **checkpoint cada 25** posts y es **reanudable** (saltea lo que ya está en
> `<PERFIL>_seen.txt`), así un corte no pierde trabajo.

- Modelo Claude: `claude-sonnet-4-6` (igual que `extract_movies.py`).

### Spotify (`spotify_sync.py` / `spotify_auth.py`)

Cumple **doble función**: canoniza el tema (reemplaza el rol de IMDb/torrent) y
alimenta la playlist. Usa `spotipy` con OAuth Authorization Code (hace falta para
modificar playlists).

- **Bootstrap (una vez):** crear una app en https://developer.spotify.com/dashboard,
  con redirect URI `http://127.0.0.1:8888/callback` (Spotify ya **no** acepta
  `localhost`). Poner `SPOTIPY_CLIENT_ID` / `SPOTIPY_CLIENT_SECRET` /
  `SPOTIPY_REDIRECT_URI` en `.env`, y correr `python spotify_auth.py` (abre el navegador).
- El token queda en **`.spotify_cache`** (gitignored); `spotipy` lo **refresca solo**, así
  el cron corre headless — mismo patrón que la sesión de Instagram y el OAuth de rclone.
- `SPOTIFY_PLAYLIST_NAME` (default `Old Music Friday — Owen Cutts`): playlist privada y
  acumulativa; `add_tracks` deduplica contra lo que ya tiene.

> ⚠️ **Gotcha (migración 11-feb-2026):** Spotify deprecó los endpoints viejos de
> playlists y `spotipy` (≤2.26.0) los sigue usando por debajo, así que sus helpers
> `user_playlist_create` y `playlist_add_items` devuelven **403** aunque la cuenta tenga
> Premium, esté en la allowlist y los scopes sean correctos (la lectura sí anda; lo roto
> son los POST). `spotify_sync.py` llama directo a los endpoints nuevos
> `POST /v1/me/playlists` y `POST /v1/playlists/{id}/items` vía `sp._post(...)`.
> La misma migración **renombró el campo del track al LEER** items de playlist: ahora
> viene bajo `item` (antes `track`), y el GET de `/tracks` sin `fields` da 403. Por eso
> `existing_uris()` pide `fields="items.item.uri,items.track.uri,next"` y acepta cualquiera.
> Para reconstruir una playlist sin duplicados: `PUT /v1/playlists/{id}/items` (reemplaza
> con ≤100) + `POST .../items` para el resto.

### Flujo

```bash
# 0. (una vez) sesión de IG por cookies del navegador + autorizar Spotify
source ./setup.sh owencutts --chrome
python spotify_auth.py            # con las SPOTIPY_* en el entorno

# 1. Planilla completa + playlist (--no-audio para probar más rápido/barato)
export ANTHROPIC_API_KEY=sk-ant-...
python build_music_spreadsheet.py owencutts --login <TU_USUARIO_IG>

# 2. Día a día: incremental + playlist + Drive (lo dispara el cron)
python daily_music_update.py owencutts
```

### Cron y secretos (música)

- `run_daily_music.sh` carga `.env`, activa el venv, corre `daily_music_update.py` y
  loguea a `daily_music.log`.
- `.env` suma a lo de películas: `SPOTIPY_CLIENT_ID`, `SPOTIPY_CLIENT_SECRET`,
  `SPOTIPY_REDIRECT_URI`, `SPOTIFY_PLAYLIST_NAME`, `MUSIC_PROFILE` (default `owencutts`).
- crontab (23:45 para no pisar el de películas de las 23:30):
  `45 23 */3 * * /home/dax/dev/insta-movies/run_daily_music.sh`

## Notas / posibles mejoras

- El emparejamiento caption↔imagen es por *stem* del archivo. instaloader nombra
  los posts con timestamp; en posts multi-imagen los archivos llevan sufijo
  (`_1`, `_2`, …) y solo el primero comparte stem con el `.txt`, así que las
  imágenes secundarias caen al camino de Vision.
- Las llamadas son secuenciales. Para perfiles grandes se podría paralelizar o
  usar la Batch API.
- Errores de API por post se reportan y no abortan la corrida.
