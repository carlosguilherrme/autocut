"""End-to-end pipeline: video in -> edited video (+ plan.json, transcript.json, .srt) out."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Callable

from . import plan as planmod
from . import speech, subtitles, transcribe as tx
from .plan import EditPlan
from .presets import resolve_settings
from .probe import MediaInfo, probe
from .render import render

ProgressFn = Callable[[str, float], None]


def _scaled(progress: ProgressFn | None, lo: float, hi: float) -> ProgressFn | None:
    if progress is None:
        return None

    def fn(stage: str, frac: float) -> None:
        progress(stage, lo + (hi - lo) * max(0.0, min(frac, 1.0)))

    return fn


def make_plan(
    input_path: str,
    workdir: str,
    preset: str = "auto",
    overrides: dict | None = None,
    progress: ProgressFn | None = None,
    transcript: tx.Transcript | None = None,
) -> tuple[EditPlan, tx.Transcript, MediaInfo]:
    os.makedirs(workdir, exist_ok=True)
    if progress:
        progress("probing", 0.0)
    info = probe(input_path)
    settings = resolve_settings(preset, overrides, source_aspect=info.aspect)

    if transcript is None:
        transcript = tx.transcribe(
            input_path,
            workdir,
            backend=settings["transcribe_backend"],
            language=settings.get("language") or None,
            model=settings["whisper_model"],
            progress=_scaled(progress, 0.05, 0.75),
        )
        transcript.save(Path(workdir) / "transcript.json")

    if progress:
        progress("planning", 0.78)
    wav = Path(workdir) / "audio.wav"
    regions = speech.speech_regions(
        transcript.words,
        min_silence=float(settings["min_silence"]),
        audio_path=str(wav) if wav.exists() else None,
        use_vad=bool(settings["use_vad"]),
    )
    if not regions:  # no speech detected: fall back to energy-based silence detection
        audio = str(wav) if wav.exists() else tx.extract_audio(input_path, str(wav), "wav")
        regions = speech.regions_silencedetect(audio, info.duration, float(settings["min_silence"]))

    segments = planmod.build_segments(
        regions,
        info.duration,
        info.fps,
        pad_before=float(settings["pad_before"]),
        pad_after=float(settings["pad_after"]),
        min_segment=float(settings["min_segment"]),
    )
    planmod.apply_speed(segments, float(settings["speed"]), settings["speed_mode"], float(settings["long_threshold"]))
    if settings.get("punch_in"):
        planmod.apply_punch_in(segments)

    edit = EditPlan(source=os.path.abspath(input_path), source_duration=info.duration, fps=info.fps, segments=segments, settings=settings)
    if settings.get("subtitles"):
        edit.cues = planmod.build_cues(
            transcript.words, edit, transcript.segments,
            max_chars=int(settings["max_chars"]), uppercase=bool(settings.get("uppercase")),
        )
    edit.save(Path(workdir) / "plan.json")
    if progress:
        progress("planned", 0.8)
    return edit, transcript, info


def render_plan(
    edit: EditPlan,
    output_path: str,
    workdir: str,
    info: MediaInfo | None = None,
    transcript: tx.Transcript | None = None,
    progress: ProgressFn | None = None,
    prefer_hw: bool = True,
) -> str:
    """Render a (possibly user-edited) plan. Cues are rebuilt when a transcript is given,
    so edited speeds/segments keep subtitles in sync."""
    os.makedirs(workdir, exist_ok=True)
    s = edit.settings
    info = info or probe(edit.source)
    if transcript is None and (Path(workdir) / "transcript.json").exists():
        transcript = tx.Transcript.load(Path(workdir) / "transcript.json")
    if transcript is not None and s.get("subtitles"):
        edit.cues = planmod.build_cues(
            transcript.words, edit, transcript.segments,
            max_chars=int(s["max_chars"]), uppercase=bool(s.get("uppercase")),
        )
    edit.save(Path(workdir) / "plan.json")

    subs_path = None
    if s.get("subtitles") and edit.cues:
        subs_path = subtitles.write_ass(
            edit.cues,
            Path(workdir) / "subs.ass",
            s["width"], s["height"],
            font=s.get("font", "Inter"),
            font_size=int(s["font_size"]),
            margin_v=int(s["margin_v"]),
            margin_h=int(s["margin_h"]),
            style=s.get("subtitle_style", "classic"),
        )
        subtitles.write_srt(edit.cues, Path(output_path).with_suffix(".srt"))

    return render(
        edit, info, output_path, workdir,
        width=int(s["width"]), height=int(s["height"]), fit=s.get("fit", "blur"),
        subs_path=subs_path, normalize_audio=bool(s.get("normalize_audio", True)),
        progress=_scaled(progress, 0.8, 1.0), prefer_hw=prefer_hw,
    )


def run(
    input_path: str,
    output_path: str,
    preset: str = "auto",
    overrides: dict | None = None,
    workdir: str | None = None,
    progress: ProgressFn | None = None,
    keep_work: bool = True,
    prefer_hw: bool = True,
) -> EditPlan:
    tmp = workdir is None
    workdir = workdir or tempfile.mkdtemp(prefix="autocut_")
    try:
        edit, transcript, info = make_plan(input_path, workdir, preset, overrides, progress)
        render_plan(edit, output_path, workdir, info=info, transcript=transcript, progress=progress, prefer_hw=prefer_hw)
        return edit
    finally:
        if tmp and not keep_work:
            shutil.rmtree(workdir, ignore_errors=True)
