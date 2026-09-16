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
    "caption_mode": "word",   # "word" (one big word at a time, like the reels) | "phrase" (1-2 lines bottom)
    "subtitle_style": "classic",  # phrase mode colour variant: classic | box | yellow
    "font": "Poppins",        # phrase font (bundled in autocut/fonts, OFL)
    "word_font": "Poppins Bold",
    "word_case": "lower",     # "lower" | "keep"
    "title_font": "DM Serif Display",
    "uppercase": False,
    "language": "pt",
    # look
    "grade": "bw",            # "bw" (black & white like the reels) | "none"
    "color_pops": True,       # brief colour flashes on the title moments (bw grade only)
    "fade_out": 0.6,          # seconds of fade to black at the end (0 = off)
    # title cards + b-roll
    "titles": True,           # serif title cards on key phrases
    "titles_max": 4,
    "title_duration": 3.0,
    "keyword_backend": "auto",  # anthropic | openai | claude-cli | heuristic | auto
    "broll": "auto",          # image provider for title cards: auto | openai | fal | pexels | local | none
    "broll_dir": None,        # local provider: folder with images named 1.jpg, 2.jpg ... (one per title)
    # audio
    "normalize_audio": True,
    # transcription
    "transcribe_backend": "auto",
    "whisper_model": "large-v3-turbo",
}

# per-aspect layout numbers (font size, bottom margin, chars per cue)
LAYOUT: dict[str, dict] = {
    "16:9": {"width": 1920, "height": 1080, "font_size": 58, "margin_v": 72, "margin_h": 120, "max_chars": 58,
             "word_size": 88, "word_y": 0.82, "title_size": 104, "title_y": 0.50, "title_line_chars": 22},
    "9:16": {"width": 1080, "height": 1920, "font_size": 84, "margin_v": 420, "margin_h": 60, "max_chars": 38,
             "word_size": 100, "word_y": 0.77, "title_size": 118, "title_y": 0.56, "title_line_chars": 13},
    "1:1": {"width": 1080, "height": 1080, "font_size": 60, "margin_v": 120, "margin_h": 70, "max_chars": 46,
             "word_size": 90, "word_y": 0.80, "title_size": 108, "title_y": 0.50, "title_line_chars": 16},
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
