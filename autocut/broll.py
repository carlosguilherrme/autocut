"""Full-screen b-roll images for title cards.

Providers (env var → provider):
  OPENAI_API_KEY → "openai"  (gpt-image-1, generated to match the prompt)
  FAL_KEY        → "fal"     (flux/schnell, fast + cheap)
  PEXELS_API_KEY → "pexels"  (stock photo search by phrase, free key)
  broll_dir      → "local"   (folder with 1.jpg, 2.jpg … one per title, in order)
  otherwise      → "openverse" (Creative Commons photo search, no key, rate-limited)
  "none"         → title card drawn over the footage itself
Every failure degrades to None so a title still renders over the footage.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

_STYLE = ", black and white, high contrast, editorial newspaper collage, cinematic, dramatic lighting, no text, no letters"


def resolve_provider(requested: str, broll_dir: str | None) -> str:
    if requested and requested != "auto":
        return requested
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    if os.environ.get("FAL_KEY"):
        return "fal"
    if os.environ.get("PEXELS_API_KEY"):
        return "pexels"
    if broll_dir and Path(broll_dir).is_dir():
        return "local"
    return "openverse"


def _download(url: str, out: Path) -> str:
    import requests

    r = requests.get(url, timeout=120)
    r.raise_for_status()
    out.write_bytes(r.content)
    return str(out)


def _openai(prompt: str, portrait: bool, out: Path) -> str:
    import requests

    r = requests.post(
        "https://api.openai.com/v1/images/generations",
        headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
        json={"model": os.environ.get("BROLL_MODEL", "gpt-image-1"), "prompt": prompt + _STYLE,
              "size": "1024x1536" if portrait else "1536x1024", "n": 1, "quality": os.environ.get("BROLL_QUALITY", "medium")},
        timeout=180,
    )
    r.raise_for_status()
    data = r.json()["data"][0]
    if data.get("b64_json"):
        out.write_bytes(base64.b64decode(data["b64_json"]))
        return str(out)
    return _download(data["url"], out)


def _fal(prompt: str, portrait: bool, out: Path) -> str:
    import requests

    r = requests.post(
        f"https://fal.run/{os.environ.get('BROLL_MODEL', 'fal-ai/flux/schnell')}",
        headers={"Authorization": f"Key {os.environ['FAL_KEY']}"},
        json={"prompt": prompt + _STYLE, "image_size": "portrait_16_9" if portrait else "landscape_16_9", "num_images": 1},
        timeout=180,
    )
    r.raise_for_status()
    return _download(r.json()["images"][0]["url"], out)


def _pexels(phrase: str, portrait: bool, out: Path) -> str | None:
    import requests

    r = requests.get(
        "https://api.pexels.com/v1/search",
        headers={"Authorization": os.environ["PEXELS_API_KEY"]},
        params={"query": phrase, "orientation": "portrait" if portrait else "landscape", "per_page": 3},
        timeout=60,
    )
    r.raise_for_status()
    photos = r.json().get("photos") or []
    if not photos:
        return None
    return _download(photos[0]["src"]["large2x"], out)


def _openverse(query: str, portrait: bool, out: Path) -> str | None:
    """Creative Commons photos, no API key (anonymous quota is small: a few dozen requests/day)."""
    import requests

    words = [w for w in query.split() if w]
    queries = [" ".join(words[:n]) for n in (3, 2, 1) if len(words) >= n] or [query]
    headers = {"User-Agent": "autocut/0.1 (https://github.com/carlosguilherrme/autocut)"}
    seen: set[str] = set()
    for q in queries:  # a 3-word query often returns nothing; back off to fewer words
        if q in seen:
            continue
        seen.add(q)
        try:
            r = requests.get(
                "https://api.openverse.org/v1/images/",
                params={"q": q, "license_type": "commercial", "aspect_ratio": "tall" if portrait else "wide",
                        "category": "photograph", "size": "large", "page_size": 6, "mature": "false"},
                headers=headers, timeout=60,
            )
            r.raise_for_status()
            results = r.json().get("results") or []
        except Exception:
            results = []
        for res in results:
            url = res.get("url")
            if not url:
                continue
            try:
                img = requests.get(url, timeout=60, headers=headers)
                if img.status_code == 200 and len(img.content) > 30_000:
                    out.write_bytes(img.content)
                    return str(out)
            except Exception:
                continue
    return None


def _local(index: int, broll_dir: str) -> str | None:
    d = Path(broll_dir)
    for ext in ("jpg", "jpeg", "png", "webp"):
        p = d / f"{index}.{ext}"
        if p.exists():
            return str(p)
    files = sorted(p for p in d.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"))
    return str(files[index - 1]) if 0 < index <= len(files) else None


LAST_ERRORS: list[str] = []  # human-readable reasons for missing images (surfaced in plan.json)


def fetch_image(prompt: str, phrase: str, index: int, out_dir: str | Path, provider: str, portrait: bool = True, broll_dir: str | None = None) -> str | None:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"broll_{index}.png"
    if out.exists():
        return str(out)
    try:
        return _fetch(prompt, phrase, index, out, provider, portrait, broll_dir)
    except Exception as exc:  # noqa: BLE001
        LAST_ERRORS.append(f"{provider} #{index}: {str(exc)[:200]}")
        return None


def _fetch(prompt: str, phrase: str, index: int, out: Path, provider: str, portrait: bool, broll_dir: str | None) -> str | None:
    if True:
        if provider == "openai":
            return _openai(prompt or phrase, portrait, out)
        if provider == "fal":
            return _fal(prompt or phrase, portrait, out)
        if provider == "pexels":
            return _pexels(phrase, portrait, out.with_suffix(".jpg"))
        if provider == "openverse":
            return _openverse(phrase, portrait, out.with_suffix(".jpg"))
        if provider == "local" and broll_dir:
            return _local(index, broll_dir)
    return None
