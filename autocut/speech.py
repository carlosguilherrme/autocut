"""Find speech regions (and therefore silences) in a recording.

Primary signal: word timestamps from the transcript (gaps between words).
Optional refinement: Silero VAD (shipped with faster-whisper) unioned with the
word regions so short interjections the transcriber skipped are not cut away.
Fallback with no transcript: ffmpeg silencedetect with an adaptive threshold.
"""

from __future__ import annotations

import re
import subprocess

from . import ffbin
from .transcribe import Word

Region = tuple[float, float]


def merge_regions(regions: list[Region], max_gap: float) -> list[Region]:
    """Sort and merge regions whose gap is <= max_gap."""
    if not regions:
        return []
    regions = sorted((float(s), float(e)) for s, e in regions if e > s)
    out: list[Region] = [regions[0]]
    for s, e in regions[1:]:
        ps, pe = out[-1]
        if s - pe <= max_gap:
            out[-1] = (ps, max(pe, e))
        else:
            out.append((s, e))
    return out


def regions_from_words(words: list[Word], max_gap: float) -> list[Region]:
    return merge_regions([(w.start, w.end) for w in words], max_gap)


def regions_silero(audio_path: str, min_silence_ms: int = 300, threshold: float = 0.5, pad_ms: int = 120) -> list[Region]:
    from faster_whisper.audio import decode_audio
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    audio = decode_audio(audio_path, sampling_rate=16000)
    opts = VadOptions(threshold=threshold, min_silence_duration_ms=min_silence_ms, speech_pad_ms=pad_ms)
    stamps = get_speech_timestamps(audio, opts)
    return [(t["start"] / 16000.0, t["end"] / 16000.0) for t in stamps]


def speech_regions(
    words: list[Word],
    min_silence: float,
    audio_path: str | None = None,
    use_vad: bool = True,
) -> list[Region]:
    """Regions of continuous speech; any gap longer than `min_silence` separates regions."""
    regions = regions_from_words(words, min_silence)
    if use_vad and audio_path:
        try:
            vad = regions_silero(audio_path, min_silence_ms=int(min_silence * 1000))
            regions = merge_regions(regions + vad, min_silence)
        except Exception:
            pass  # VAD is a refinement only
    return regions


# --------------------------------------------------------------------------- no-transcript fallback

_SIL_RE = re.compile(r"silence_(start|end): ([0-9.]+)")


def mean_volume_db(audio_path: str) -> float:
    out = subprocess.run(
        [ffbin.ffmpeg(require_subtitles=False), "-hide_banner", "-i", audio_path, "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True,
        text=True,
    ).stderr
    m = re.search(r"mean_volume: (-?[0-9.]+) dB", out)
    return float(m.group(1)) if m else -25.0


def regions_silencedetect(audio_path: str, duration: float, min_silence: float, noise_db: float | None = None) -> list[Region]:
    if noise_db is None:
        noise_db = mean_volume_db(audio_path) - 10.0
    out = subprocess.run(
        [ffbin.ffmpeg(require_subtitles=False), "-hide_banner", "-i", audio_path,
         "-af", f"silencedetect=noise={noise_db}dB:d={min_silence}", "-f", "null", "-"],
        capture_output=True,
        text=True,
    ).stderr
    silences: list[Region] = []
    cur_start: float | None = None
    for kind, val in _SIL_RE.findall(out):
        if kind == "start":
            cur_start = float(val)
        elif cur_start is not None:
            silences.append((cur_start, float(val)))
            cur_start = None
    if cur_start is not None:
        silences.append((cur_start, duration))
    # complement
    regions: list[Region] = []
    pos = 0.0
    for s, e in silences:
        if s > pos:
            regions.append((pos, s))
        pos = e
    if duration > pos:
        regions.append((pos, duration))
    return regions
