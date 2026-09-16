"""End-to-end pipeline: video in -> edited video (+ plan.json, transcript.json, .srt) out."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Callable

from . import broll, keywords
from . import plan as planmod
from . import speech, subtitles, transcribe as tx
from .plan import Cue, EditPlan
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


# --------------------------------------------------------------------------- captions


def _caption_cues(transcript: tx.Transcript, edit: EditPlan, s: dict) -> list[Cue]:
    if not s.get("subtitles"):
        return []
    if s.get("caption_mode", "word") == "word":
        return planmod.build_word_cues(transcript.words, edit, lower=s.get("word_case", "lower") == "lower")
    return planmod.build_cues(
        transcript.words, edit, transcript.segments,
        max_chars=int(s["max_chars"]), uppercase=bool(s.get("uppercase")),
    )


def _title_cues(transcript: tx.Transcript, edit: EditPlan, s: dict, workdir: str, progress: ProgressFn | None) -> list[Cue]:
    if not s.get("titles"):
        return []
    if progress:
        progress("picking titles", 0.0)
    kws, used = keywords.pick_keywords(
        transcript.text, transcript.segments, n=int(s.get("titles_max", 4)), backend=s.get("keyword_backend", "auto")
    )
    s["keyword_backend_used"] = used
    s["keywords_picked"] = kws
    titles = planmod.place_titles(
        kws, transcript.words, edit,
        duration=float(s.get("title_duration", 3.0)),
        line_chars=int(s.get("title_line_chars", 13)),
        lower=s.get("word_case", "lower") == "lower",
    )
    provider = broll.resolve_provider(s.get("broll", "auto"), s.get("broll_dir"))
    s["broll_provider_used"] = provider
    portrait = int(s["height"]) > int(s["width"])
    for i, t in enumerate(titles, 1):
        if progress:
            progress("fetching b-roll", i / max(len(titles), 1))
        if provider != "none":
            t.image = broll.fetch_image(t.prompt or "", t.search or t.text.replace("\n", " "), i, Path(workdir) / "broll", provider, portrait, s.get("broll_dir"))
    if broll.LAST_ERRORS:
        s["broll_errors"] = broll.LAST_ERRORS[-10:]
    return titles


def _color_pops(edit: EditPlan, s: dict) -> list[tuple[float, float]]:
    """Brief colour windows: the opening beat and the moment before each title card."""
    if s.get("grade") != "bw" or not s.get("color_pops"):
        return []
    pops = [(0.0, min(1.0, edit.out_duration()))]
    for c in edit.cues:
        if c.style == "Title" and not c.image:
            pops.append((max(c.start - 0.7, 0.0), c.start))
    return pops


# --------------------------------------------------------------------------- plan


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
            progress=_scaled(progress, 0.05, 0.65),
        )
        transcript.save(Path(workdir) / "transcript.json")

    if progress:
        progress("planning", 0.66)
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
    edit.cues = _caption_cues(transcript, edit, settings)
    edit.cues += _title_cues(transcript, edit, settings, workdir, _scaled(progress, 0.68, 0.78))
    edit.save(Path(workdir) / "plan.json")
    if progress:
        progress("planned", 0.8)
    return edit, transcript, info


# --------------------------------------------------------------------------- render


def render_plan(
    edit: EditPlan,
    output_path: str,
    workdir: str,
    info: MediaInfo | None = None,
    transcript: tx.Transcript | None = None,
    progress: ProgressFn | None = None,
    prefer_hw: bool = True,
) -> str:
    """Render a (possibly user-edited) plan. Captions are rebuilt from the transcript and
    title cards follow their spoken anchor, so edited speeds/segments stay in sync."""
    os.makedirs(workdir, exist_ok=True)
    s = edit.settings
    info = info or probe(edit.source)
    if transcript is None and (Path(workdir) / "transcript.json").exists():
        transcript = tx.Transcript.load(Path(workdir) / "transcript.json")
    titles = [c for c in edit.cues if c.style == "Title"]
    if transcript is not None:
        edit.cues = _caption_cues(transcript, edit, s)
    else:
        edit.cues = [c for c in edit.cues if c.style != "Title"]
    edit.cues += planmod.remap_titles(titles, edit, duration=float(s.get("title_duration", 3.0)))
    edit.save(Path(workdir) / "plan.json")

    subs_path = None
    if edit.cues:
        subs_path = subtitles.write_ass(edit.cues, Path(workdir) / "subs.ass", int(s["width"]), int(s["height"]), s)
        subtitles.write_srt(edit.cues, Path(output_path).with_suffix(".srt"))

    broll_images = [(c.image, c.start, c.end) for c in edit.cues if c.style == "Title" and c.image]
    return render(
        edit, info, output_path, workdir,
        width=int(s["width"]), height=int(s["height"]), fit=s.get("fit", "blur"),
        subs_path=subs_path, normalize_audio=bool(s.get("normalize_audio", True)),
        progress=_scaled(progress, 0.8, 1.0), prefer_hw=prefer_hw,
        grade=s.get("grade", "none"), color_pops=_color_pops(edit, s),
        broll_images=broll_images, fade_out=float(s.get("fade_out", 0.0) or 0.0),
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
