"""Editing presets — "the way I like it". Override any key from the CLI/API."""

from __future__ import annotations

import copy

BASE: dict = {
    # cutting
    "min_silence": 0.45,      # pause longer than this (s) becomes a cut
    "pad_before": 0.10,       # breathing room kept before speech (s)
    "pad_after": 0.18,        # ... and after
    "min_segment": 0.30,      # drop isolated blips shorter than this (s)
    "use_vad": True,          # refine word gaps with Silero VAD when available
    # speed
    "speed": 1.2,
    "speed_mode": "long",     # "all" | "long" | "none"
    "long_threshold": 6.0,    # in "long" mode, segments >= this many source seconds get sped up
    # picture
    "aspect": "auto",         # "auto" | "16:9" | "9:16" | "1:1"
    "fit": "blur",            # when aspect differs from source: "blur" | "crop" | "pad"
    "punch_in": False,        # alternate subtle zoom between cuts
    # subtitles
    "subtitles": True,
    "subtitle_style": "classic",
    "font": "Poppins",  # bundled in autocut/fonts (OFL)
    "uppercase": False,
    "language": "pt",
    # audio
    "normalize_audio": True,
    # transcription
    "transcribe_backend": "auto",
    "whisper_model": "large-v3-turbo",
}

# per-aspect layout numbers (font size, bottom margin, chars per cue)
LAYOUT: dict[str, dict] = {
    "16:9": {"width": 1920, "height": 1080, "font_size": 58, "margin_v": 72, "margin_h": 120, "max_chars": 58},
    "9:16": {"width": 1080, "height": 1920, "font_size": 84, "margin_v": 420, "margin_h": 60, "max_chars": 38},
    "1:1": {"width": 1080, "height": 1080, "font_size": 60, "margin_v": 120, "margin_h": 70, "max_chars": 46},
}

PRESETS: dict[str, dict] = {
    "auto": {},
    "youtube": {"aspect": "16:9", "speed_mode": "long", "punch_in": False},
    "reels": {"aspect": "9:16", "speed_mode": "long", "punch_in": True},
    "reels-fast": {"aspect": "9:16", "speed_mode": "all", "punch_in": True},
    "youtube-fast": {"aspect": "16:9", "speed_mode": "all"},
}


def resolve_settings(preset: str = "auto", overrides: dict | None = None, source_aspect: float | None = None) -> dict:
    if preset not in PRESETS:
        raise ValueError(f"unknown preset {preset!r}; choose from {', '.join(PRESETS)}")
    s = copy.deepcopy(BASE)
    s.update(PRESETS[preset])
    for k, v in (overrides or {}).items():
        if v is not None:
            s[k] = v
    if s["aspect"] == "auto":
        if source_aspect is None:
            s["aspect"] = "16:9"
        elif source_aspect < 0.8:
            s["aspect"] = "9:16"
        elif source_aspect < 1.2:
            s["aspect"] = "1:1"
        else:
            s["aspect"] = "16:9"
    layout = LAYOUT[s["aspect"]]
    for k, v in layout.items():
        s.setdefault(k, v)
    s["preset"] = preset
    return s
