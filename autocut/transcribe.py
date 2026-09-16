"""Speech-to-text with word timestamps.

Backends:
  - "local": faster-whisper on CPU/GPU (no API key, needs the `faster-whisper` package).
  - "api":   any OpenAI-compatible /audio/transcriptions endpoint that returns
             verbose_json with word timestamps. Works with OpenAI (whisper-1) and
             Groq (whisper-large-v3-turbo). Configure with env vars:
               TRANSCRIBE_API_KEY, TRANSCRIBE_BASE_URL, TRANSCRIBE_MODEL
             (falls back to GROQ_API_KEY / OPENAI_API_KEY).
  - "auto":  local if faster-whisper is importable, otherwise api.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Callable

from . import ffbin

ProgressFn = Callable[[str, float], None]


@dataclass
class Word:
    start: float
    end: float
    text: str
    prob: float = 1.0


@dataclass
class Transcript:
    words: list[Word] = field(default_factory=list)
    segments: list[dict] = field(default_factory=list)  # {"start","end","text"}
    language: str = ""
    backend: str = ""

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    def to_dict(self) -> dict:
        return {
            "language": self.language,
            "backend": self.backend,
            "segments": self.segments,
            "words": [asdict(w) for w in self.words],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Transcript":
        return cls(
            words=[Word(**w) for w in d.get("words", [])],
            segments=d.get("segments", []),
            language=d.get("language", ""),
            backend=d.get("backend", ""),
        )

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=1))

    @classmethod
    def load(cls, path: str | Path) -> "Transcript":
        return cls.from_dict(json.loads(Path(path).read_text()))


# --------------------------------------------------------------------------- audio prep


def extract_audio(video: str, out_path: str, fmt: str = "wav") -> str:
    """Extract mono 16 kHz audio. wav for local whisper, mp3 for API uploads."""
    binary = ffbin.ffmpeg(require_subtitles=False)
    args = [binary, "-y", "-hide_banner", "-loglevel", "error", "-i", video, "-vn", "-ac", "1", "-ar", "16000"]
    if fmt == "wav":
        args += ["-c:a", "pcm_s16le"]
    elif fmt == "mp3":
        args += ["-c:a", "libmp3lame", "-b:a", "64k"]
    else:
        raise ValueError(fmt)
    args.append(out_path)
    proc = subprocess.run(args, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"audio extraction failed: {proc.stderr.strip()}")
    return out_path


# --------------------------------------------------------------------------- local backend


def _local_available() -> bool:
    try:
        import faster_whisper  # noqa: F401

        return True
    except Exception:
        return False


_MODEL_CACHE: dict[tuple, object] = {}
_MODEL_LOCK = __import__("threading").Lock()


def _load_model(model: str, device: str, compute_type: str):
    """Keep loaded whisper models in memory so a long-running server does not reload per job."""
    from faster_whisper import WhisperModel

    key = (model, device, compute_type)
    with _MODEL_LOCK:
        if key not in _MODEL_CACHE:
            _MODEL_CACHE[key] = WhisperModel(model, device=device, compute_type=compute_type)
        return _MODEL_CACHE[key]


def transcribe_local(
    audio_path: str,
    model: str = "large-v3-turbo",
    language: str | None = "pt",
    device: str = "auto",
    compute_type: str = "int8",
    progress: ProgressFn | None = None,
) -> Transcript:
    if progress:
        progress("loading whisper model", 0.0)
    wm = _load_model(model, device, compute_type)
    segments, info = wm.transcribe(
        audio_path,
        language=language,
        word_timestamps=True,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 250, "speech_pad_ms": 200},
        beam_size=5,
        condition_on_previous_text=False,
    )
    total = max(info.duration or 1.0, 1.0)
    tr = Transcript(language=info.language or (language or ""), backend=f"local:{model}")
    for seg in segments:
        text = seg.text.strip()
        if not text:
            continue
        tr.segments.append({"start": float(seg.start), "end": float(seg.end), "text": text})
        for w in seg.words or []:
            t = w.word.strip()
            if not t:
                continue
            tr.words.append(Word(float(w.start), float(w.end), t, float(w.probability)))
        if progress:
            progress("transcribing", min(float(seg.end) / total, 0.99))
    return tr


# --------------------------------------------------------------------------- api backend


def _api_config() -> tuple[str, str, str]:
    key = os.environ.get("TRANSCRIBE_API_KEY") or os.environ.get("GROQ_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("no transcription API key (set TRANSCRIBE_API_KEY, GROQ_API_KEY or OPENAI_API_KEY)")
    base = os.environ.get("TRANSCRIBE_BASE_URL")
    model = os.environ.get("TRANSCRIBE_MODEL")
    if not base:
        if os.environ.get("GROQ_API_KEY") and key == os.environ.get("GROQ_API_KEY"):
            base = "https://api.groq.com/openai/v1"
        else:
            base = "https://api.openai.com/v1"
    if not model:
        model = "whisper-large-v3-turbo" if "groq" in base else "whisper-1"
    return key, base.rstrip("/"), model


def _split_audio(audio_path: str, chunk_seconds: int, workdir: str) -> list[tuple[float, str]]:
    """Split a long audio file into chunks; returns [(offset_seconds, path)]."""
    binary = ffbin.ffmpeg(require_subtitles=False)
    pattern = os.path.join(workdir, "chunk_%03d.mp3")
    subprocess.run(
        [binary, "-y", "-hide_banner", "-loglevel", "error", "-i", audio_path, "-f", "segment",
         "-segment_time", str(chunk_seconds), "-c", "copy", pattern],
        check=True,
    )
    chunks = sorted(Path(workdir).glob("chunk_*.mp3"))
    return [(i * chunk_seconds, str(p)) for i, p in enumerate(chunks)]


def transcribe_api(
    audio_path: str,
    language: str | None = "pt",
    progress: ProgressFn | None = None,
    chunk_seconds: int = 600,
) -> Transcript:
    import requests

    key, base, model = _api_config()
    size_mb = os.path.getsize(audio_path) / 1e6
    workdir = str(Path(audio_path).parent)
    if size_mb > 20:
        parts = _split_audio(audio_path, chunk_seconds, workdir)
    else:
        parts = [(0.0, audio_path)]

    tr = Transcript(language=language or "", backend=f"api:{model}")
    for i, (offset, path) in enumerate(parts):
        if progress:
            progress("transcribing (api)", i / max(len(parts), 1))
        with open(path, "rb") as fh:
            data = {
                "model": model,
                "response_format": "verbose_json",
                "timestamp_granularities[]": ["word", "segment"],
            }
            if language:
                data["language"] = language
            resp = requests.post(
                f"{base}/audio/transcriptions",
                headers={"Authorization": f"Bearer {key}"},
                files={"file": (os.path.basename(path), fh, "audio/mpeg")},
                data=data,
                timeout=600,
            )
        if resp.status_code != 200:
            raise RuntimeError(f"transcription API error {resp.status_code}: {resp.text[:300]}")
        body = resp.json()
        tr.language = body.get("language") or tr.language
        for s in body.get("segments", []) or []:
            tr.segments.append({"start": offset + float(s["start"]), "end": offset + float(s["end"]), "text": s["text"].strip()})
        words = body.get("words", []) or []
        if not words:
            # Some models only return segments; synthesize evenly spaced words.
            for s in body.get("segments", []) or []:
                toks = s["text"].split()
                if not toks:
                    continue
                dur = (float(s["end"]) - float(s["start"])) / len(toks)
                for j, tok in enumerate(toks):
                    st = offset + float(s["start"]) + j * dur
                    tr.words.append(Word(st, st + dur, tok, 0.5))
        for w in words:
            t = str(w.get("word", "")).strip()
            if t:
                tr.words.append(Word(offset + float(w["start"]), offset + float(w["end"]), t))
    tr.words.sort(key=lambda w: w.start)
    return tr


# --------------------------------------------------------------------------- entry


def transcribe(
    video_or_audio: str,
    workdir: str,
    backend: str = "auto",
    language: str | None = "pt",
    model: str = "large-v3-turbo",
    progress: ProgressFn | None = None,
) -> Transcript:
    if backend == "auto":
        backend = "local" if _local_available() else "api"
    os.makedirs(workdir, exist_ok=True)
    if backend == "local":
        wav = extract_audio(video_or_audio, os.path.join(workdir, "audio.wav"), "wav")
        return transcribe_local(wav, model=model, language=language, progress=progress)
    if backend == "api":
        mp3 = extract_audio(video_or_audio, os.path.join(workdir, "audio.mp3"), "mp3")
        return transcribe_api(mp3, language=language, progress=progress)
    raise ValueError(f"unknown backend {backend!r}")
