#!/usr/bin/env python3
"""
extract_movies.py — extrae títulos de películas recomendadas en los posts de un
perfil de Instagram ya descargado con instaloader.

Estrategia por post:
  1. Si hay caption (archivo .txt con el mismo nombre base que la imagen), se le
     pasa a Claude (texto) para extraer el título de la película.
  2. Si el caption está vacío o Claude no encuentra un título, se hace fallback a
     Claude Vision sobre la propia imagen.

Al terminar escribe movies.txt con la lista deduplicada y ordenada de títulos.

Uso:
    export ANTHROPIC_API_KEY=sk-ant-...
    python extract_movies.py [CARPETA_PERFIL]   # default: juan.amonda
"""

import base64
import os
import sys
from pathlib import Path

import anthropic

MODEL = "claude-sonnet-4-6"
DEFAULT_PROFILE = "juan.amonda"
IMAGE_EXTS = (".jpg", ".jpeg", ".png")
NONE_SENTINEL = "NONE"

MEDIA_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}

SYSTEM_PROMPT = (
    "Sos un asistente que identifica títulos de películas recomendadas en posts "
    "de Instagram. Tu tarea es extraer el título de la película que se recomienda. "
    "Respondé ÚNICAMENTE con el título exacto de la película, sin comillas, sin "
    "año, sin texto adicional, sin explicaciones. Si no hay ninguna película "
    f"recomendada o no podés identificarla con seguridad, respondé exactamente: {NONE_SENTINEL}"
)


def die(msg: str, code: int = 1) -> None:
    print(f"Error: {msg}", file=sys.stderr)
    sys.exit(code)


def get_client() -> anthropic.Anthropic:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        die(
            "la variable de entorno ANTHROPIC_API_KEY no está seteada.\n"
            "Seteala con:  export ANTHROPIC_API_KEY=sk-ant-..."
        )
    return anthropic.Anthropic()


def find_images(folder: Path) -> list[Path]:
    """Devuelve las imágenes del perfil, ordenadas por nombre."""
    return sorted(
        p for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in IMAGE_EXTS
    )


def read_caption(image: Path) -> str:
    """Lee el caption (.txt con el mismo nombre base) si existe."""
    txt = image.with_suffix(".txt")
    if txt.exists():
        return txt.read_text(encoding="utf-8", errors="replace").strip()
    return ""


def _parse(text: str) -> str | None:
    """Normaliza la respuesta de Claude: None si es el centinela / vacío."""
    title = text.strip().strip('"').strip()
    if not title or title.upper() == NONE_SENTINEL:
        return None
    return title


def title_from_caption(client: anthropic.Anthropic, caption: str) -> str | None:
    resp = client.messages.create(
        model=MODEL,
        max_tokens=200,
        system=SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": (
                "Texto del post de Instagram:\n\n"
                f"{caption}\n\n"
                "¿Qué película se recomienda?"
            ),
        }],
    )
    text = next((b.text for b in resp.content if b.type == "text"), "")
    return _parse(text)


def title_from_image(client: anthropic.Anthropic, image: Path) -> str | None:
    data = base64.standard_b64encode(image.read_bytes()).decode("utf-8")
    media_type = MEDIA_TYPES.get(image.suffix.lower(), "image/jpeg")
    resp = client.messages.create(
        model=MODEL,
        max_tokens=200,
        system=SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": data,
                    },
                },
                {"type": "text", "text": "¿Qué película se recomienda en esta imagen?"},
            ],
        }],
    )
    text = next((b.text for b in resp.content if b.type == "text"), "")
    return _parse(text)


def main() -> None:
    profile = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PROFILE
    folder = Path(profile)
    if not folder.is_dir():
        die(
            f"no existe la carpeta '{folder}'.\n"
            "¿Descargaste el perfil?  ./setup.sh <PERFIL>"
        )

    images = find_images(folder)
    if not images:
        die(f"no se encontraron imágenes (.jpg/.png) en '{folder}'.")

    client = get_client()

    print(f"Procesando {len(images)} imágenes de '{folder}' con {MODEL}\n")

    movies: set[str] = set()
    unidentified = 0

    for i, image in enumerate(images, 1):
        prefix = f"[{i}/{len(images)}] {image.name}"
        title = None
        method = ""

        caption = read_caption(image)
        if caption:
            try:
                title = title_from_caption(client, caption)
            except anthropic.APIError as e:
                print(f"{prefix}: error en caption ({e!s}); pruebo con la imagen")
            if title:
                method = "caption"

        if not title:
            try:
                title = title_from_image(client, image)
                if title:
                    method = "imagen"
            except anthropic.APIError as e:
                print(f"{prefix}: error en imagen ({e!s})")

        if title:
            movies.add(title)
            print(f"{prefix}: {title}  ({method})")
        else:
            unidentified += 1
            print(f"{prefix}: no identificado")

    out = Path("movies.txt")
    ordered = sorted(movies, key=str.casefold)
    out.write_text("\n".join(ordered) + ("\n" if ordered else ""), encoding="utf-8")

    print("\n" + "=" * 50)
    print(f"Películas únicas encontradas : {len(ordered)}")
    print(f"Posts no identificados       : {unidentified}")
    print(f"Lista guardada en            : {out.resolve()}")


if __name__ == "__main__":
    main()
