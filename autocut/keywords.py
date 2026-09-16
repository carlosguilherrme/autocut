"""Pick the few phrases that deserve a big serif title card (and an image prompt for b-roll).

Backends, tried in this order when backend="auto":
  - "anthropic":  ANTHROPIC_API_KEY (Messages API, claude-haiku-4-5)
  - "openai":     OPENAI_API_KEY or GROQ_API_KEY (chat completions; Groq uses llama-3.3-70b)
  - "claude-cli": the local `claude` CLI logged in on this Mac (`claude -p`)
  - "heuristic":  longest content words, evenly spread — no network
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess

_STOP = set(
    "a o e os as um uma uns umas de do da dos das em no na nos nas por para pra com sem sob sobre que se não sim "
    "eu tu ele ela nós vós eles elas me te lhe nos vos meu minha seu sua nosso nossa isso isto aquilo este esta esse essa "
    "aquele aquela ao à aos às mas ou porque então também já ainda muito muita muitos muitas mais menos bem mal como "
    "quando onde qual quais quem cada tudo todo toda todos todas nada algo aqui ali lá hoje ontem amanhã agora depois antes "
    "ser estar ter fazer ir vir dar ver poder querer dever gente vai vou foi era são está estão tem têm hambúrguer".split()
)

_PROMPT = """Você é editor de Reels. Abaixo está a transcrição de uma fala.
Escolha até {n} expressões curtas (1 a 3 palavras, copiadas EXATAMENTE como aparecem no texto, na mesma ordem em que são ditas, bem espaçadas ao longo da fala) que merecem virar um título grande na tela — ideias-chave, substantivos fortes, contrastes.
Para cada uma escreva (a) um prompt de imagem em inglês para b-roll: preto e branco, estilo colagem editorial de jornal, cinematográfico, dramático, sem texto nem letras na imagem; e (b) "search": 2 ou 3 palavras em inglês, concretas e visuais, para buscar uma foto de banco de imagens (ex.: "burger fries", "city skyline night").
Responda SOMENTE um JSON array: [{{"phrase": "...", "image_prompt": "...", "search": "..."}}]

Transcrição:
{text}"""


def _extract_json(s: str) -> list[dict]:
    s = s.strip()
    m = re.search(r"\[.*\]", s, re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    out = []
    for d in data if isinstance(data, list) else []:
        if isinstance(d, dict) and d.get("phrase"):
            out.append({
                "phrase": str(d["phrase"]).strip(),
                "image_prompt": str(d.get("image_prompt", "")).strip(),
                "search": str(d.get("search", "")).strip(),
            })
    return out


def _via_anthropic(prompt: str) -> list[dict]:
    import requests

    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={"model": os.environ.get("KEYWORDS_MODEL", "claude-haiku-4-5-20251001"), "max_tokens": 800,
              "messages": [{"role": "user", "content": prompt}]},
        timeout=60,
    )
    r.raise_for_status()
    return _extract_json("".join(b.get("text", "") for b in r.json().get("content", [])))


def _via_openai(prompt: str) -> list[dict]:
    import requests

    if os.environ.get("OPENAI_API_KEY"):
        base, key, model = "https://api.openai.com/v1", os.environ["OPENAI_API_KEY"], os.environ.get("KEYWORDS_MODEL", "gpt-4o-mini")
    else:
        base, key, model = "https://api.groq.com/openai/v1", os.environ["GROQ_API_KEY"], os.environ.get("KEYWORDS_MODEL", "llama-3.3-70b-versatile")
    r = requests.post(
        f"{base}/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.3},
        timeout=60,
    )
    r.raise_for_status()
    return _extract_json(r.json()["choices"][0]["message"]["content"])


def _via_claude_cli(prompt: str) -> list[dict]:
    exe = shutil.which("claude")
    if not exe:
        raise RuntimeError("claude CLI not found")
    proc = subprocess.run(
        [exe, "-p", "--model", os.environ.get("KEYWORDS_MODEL", "haiku"), "--output-format", "json", prompt],
        capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip()[:300])
    try:
        body = json.loads(proc.stdout)
        text = body.get("result", "") if isinstance(body, dict) else ""
    except json.JSONDecodeError:
        text = proc.stdout
    return _extract_json(text)


def _heuristic(segments: list[dict], n: int) -> list[dict]:
    """Longest content word of well-spread phrases."""
    if not segments:
        return []
    picks: list[dict] = []
    step = max(len(segments) / n, 1.0)
    idx = 0.0
    while idx < len(segments) and len(picks) < n:
        seg = segments[int(idx)]
        cands = [w.strip(",.;:!?…\"'") for w in seg["text"].split()]
        cands = [w for w in cands if len(w) >= 7 and w.lower() not in _STOP]
        if cands:
            best = max(cands, key=len)
            picks.append({"phrase": best, "search": best,
                          "image_prompt": f"black and white editorial newspaper collage, cinematic, dramatic, concept of '{best}', no text"})
        idx += step
    return picks


def pick_keywords(text: str, segments: list[dict], n: int = 4, backend: str = "auto") -> tuple[list[dict], str]:
    """Returns (keywords, backend_used)."""
    prompt = _PROMPT.format(n=n, text=text[:12000])
    order = [backend] if backend != "auto" else ["anthropic", "openai", "claude-cli", "heuristic"]
    for b in order:
        try:
            if b == "anthropic" and os.environ.get("ANTHROPIC_API_KEY"):
                out = _via_anthropic(prompt)
            elif b == "openai" and (os.environ.get("OPENAI_API_KEY") or os.environ.get("GROQ_API_KEY")):
                out = _via_openai(prompt)
            elif b == "claude-cli":
                out = _via_claude_cli(prompt)
            elif b == "heuristic":
                out = _heuristic(segments, n)
            else:
                continue
            if out:
                return out[:n], b
        except Exception:
            if backend != "auto":
                raise
            continue
    return _heuristic(segments, n), "heuristic"
