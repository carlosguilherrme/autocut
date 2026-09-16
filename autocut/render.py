"""Render an EditPlan with a single ffmpeg pass:
trim + speed per segment -> concat -> fit to target aspect -> burn subtitles -> encode.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable

from . import ffbin
from .plan import EditPlan
from .probe import MediaInfo

ProgressFn = Callable[[str, float], None]
FONTS_DIR = Path(__file__).parent / "fonts"


def _fmt(x: float) -> str:
    return f"{x:.4f}"


def _fit_chain(src_w: int, src_h: int, w: int, h: int, mode: str) -> str:
    """Filter chain that takes [vc] and produces [vf] at exactly w x h."""
    src_aspect = src_w / src_h
    dst_aspect = w / h
    same = abs(src_aspect - dst_aspect) / dst_aspect < 0.02
    if same or mode == "crop":
        return (
            f"[vc]scale={w}:{h}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h},setsar=1[vf]"
        )
    if mode == "pad":
        return (
            f"[vc]scale={w}:{h}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1[vf]"
        )
    # "blur": full-frame blurred copy behind the letterboxed original
    return (
        f"[vc]split=2[bg][fg];"
        f"[bg]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
        f"gblur=sigma=36:steps=2,eq=brightness=-0.08:saturation=1.1[bgb];"
        f"[fg]scale={w}:{h}:force_original_aspect_ratio=decrease:force_divisible_by=2[fgs];"
        f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2:format=auto,setsar=1[vf]"
    )


def build_filter_script(
    plan: EditPlan,
    info: MediaInfo,
    width: int,
    height: int,
    fit: str,
    subs_name: str | None,
    fonts_name: str | None,
    normalize_audio: bool,
) -> str:
    segs = plan.active_segments()
    if not segs:
        raise RuntimeError("plan has no enabled segments")
    sw, sh = info.display_width, info.display_height
    lines: list[str] = []
    for i, s in enumerate(segs):
        v = f"[0:v]trim=start={_fmt(s.src_start)}:end={_fmt(s.src_end)},setpts=(PTS-STARTPTS)/{s.speed:.4f}"
        if s.zoom > 1.001:
            z = s.zoom
            v += f",crop=iw/{z:.4f}:ih/{z:.4f},scale={sw}:{sh}"
        lines.append(v + f"[v{i}];")
        if info.has_audio:
            a = f"[0:a]atrim=start={_fmt(s.src_start)}:end={_fmt(s.src_end)},asetpts=PTS-STARTPTS"
            if abs(s.speed - 1.0) > 1e-3:
                a += f",atempo={s.speed:.4f}"
            lines.append(a + f"[a{i}];")
    if info.has_audio:
        inputs = "".join(f"[v{i}][a{i}]" for i in range(len(segs)))
        lines.append(f"{inputs}concat=n={len(segs)}:v=1:a=1[vc][ac];")
    else:
        inputs = "".join(f"[v{i}]" for i in range(len(segs)))
        lines.append(f"{inputs}concat=n={len(segs)}:v=1:a=0[vc];")

    lines.append(_fit_chain(sw, sh, width, height, fit) + ";")
    if subs_name:
        sub = f"[vf]subtitles=filename={subs_name}"
        if fonts_name:
            sub += f":fontsdir={fonts_name}"
        lines.append(sub + "[vo];")
    else:
        lines.append("[vf]null[vo];")
    if info.has_audio:
        if normalize_audio:
            lines.append("[ac]loudnorm=I=-16:TP=-1.5:LRA=11[ao]")
        else:
            lines.append("[ac]anull[ao]")
    script = "\n".join(lines)
    return script.rstrip(";") if script.endswith(";") else script


def render(
    plan: EditPlan,
    info: MediaInfo,
    output: str,
    workdir: str,
    width: int,
    height: int,
    fit: str = "blur",
    subs_path: str | None = None,
    normalize_audio: bool = True,
    progress: ProgressFn | None = None,
    prefer_hw: bool = True,
) -> str:
    binary = ffbin.ffmpeg(require_subtitles=bool(subs_path))
    work = Path(workdir)
    work.mkdir(parents=True, exist_ok=True)

    # The subtitles filter is picky about path escaping; run ffmpeg inside the
    # work dir and reference files by plain relative names.
    subs_name = None
    fonts_name = None
    if subs_path:
        subs_name = "subs.ass"
        if Path(subs_path).resolve() != (work / subs_name).resolve():
            shutil.copyfile(subs_path, work / subs_name)
        if FONTS_DIR.is_dir() and any(FONTS_DIR.iterdir()):
            fonts_name = "fonts"
            if not (work / fonts_name).exists():
                shutil.copytree(FONTS_DIR, work / fonts_name)

    script = build_filter_script(plan, info, width, height, fit, subs_name, fonts_name, normalize_audio)
    (work / "filter.txt").write_text(script)

    fps_out = info.fps if 1 <= info.fps <= 60 else 30
    args = [
        binary, "-y", "-hide_banner", "-loglevel", "error", "-nostats", "-progress", "pipe:1",
        "-i", os.path.abspath(info.path),
        "-filter_complex_script", "filter.txt",
        "-map", "[vo]",
    ]
    if info.has_audio:
        args += ["-map", "[ao]"]
    args += ffbin.video_encoder_args(binary, width, height, prefer_hw=prefer_hw)
    args += ["-pix_fmt", "yuv420p", "-r", info.fps_fraction if fps_out == info.fps else str(fps_out), "-fps_mode", "cfr"]
    if info.has_audio:
        args += ["-c:a", "aac", "-b:a", "160k", "-ar", "48000"]
    args += ["-movflags", "+faststart", os.path.abspath(output)]

    total_us = max(plan.out_duration(), 0.1) * 1_000_000
    proc = subprocess.Popen(args, cwd=str(work), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert proc.stdout is not None
    for line in proc.stdout:
        if progress and line.startswith("out_time_us="):
            try:
                progress("rendering", min(int(line.split("=", 1)[1]) / total_us, 0.999))
            except ValueError:
                pass
    _, err = proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg render failed: {err.strip()[-2000:]}")
    if progress:
        progress("rendering", 1.0)
    return output
