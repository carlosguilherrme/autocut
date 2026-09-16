"""Render an EditPlan with a single ffmpeg pass:
trim + speed per segment -> concat -> fit to target aspect -> grade (b&w) -> b-roll overlays
-> burn captions/titles -> fade out -> encode.
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
    grade: str = "none",
    color_pops: list[tuple[float, float]] | None = None,
    brolls: list[tuple[int, float, float]] | None = None,  # (ffmpeg input index, start, end) on the output timeline
    fade_out: float = 0.0,
) -> str:
    segs = plan.active_segments()
    if not segs:
        raise RuntimeError("plan has no enabled segments")
    sw, sh = info.display_width, info.display_height
    fps = info.fps if 1 <= info.fps <= 60 else 30.0
    out_dur = plan.out_duration()
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
    cur = "vf"

    # colour grade: black & white with optional brief colour windows
    if grade == "bw":
        enable = ""
        if color_pops:
            expr = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in color_pops)
            enable = f":enable='not({expr})'"
        lines.append(f"[{cur}]hue=s=0{enable},eq=contrast=1.06:brightness=-0.01[vg];")
        cur = "vg"

    # full-screen b-roll with a slow push-in, faded in/out, shown only in its window
    for k, (idx, start, end) in enumerate(brolls or []):
        dur = max(end - start, 0.3)
        frames = max(int(round(dur * fps)), 2)
        lines.append(
            f"[{idx}:v]scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},"
            f"zoompan=z='1+0.10*on/{frames}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={width}x{height}:fps={fps:.3f},"
            f"hue=s=0,eq=contrast=1.12:brightness=-0.08,vignette=angle=PI/4.5,"
            f"format=yuva420p,fade=t=in:st=0:d=0.25:alpha=1,fade=t=out:st={max(dur - 0.25, 0):.3f}:d=0.25:alpha=1,"
            f"setpts=PTS-STARTPTS+{start:.3f}/TB[b{k}];"
        )
        lines.append(f"[{cur}][b{k}]overlay=x=0:y=0:eof_action=pass:enable='between(t,{start:.3f},{end:.3f})'[vb{k}];")
        cur = f"vb{k}"

    if subs_name:
        sub = f"[{cur}]subtitles=filename={subs_name}"
        if fonts_name:
            sub += f":fontsdir={fonts_name}"
        lines.append(sub + "[vs];")
        cur = "vs"

    if fade_out > 0 and out_dur > fade_out * 2:
        lines.append(f"[{cur}]fade=t=out:st={out_dur - fade_out:.3f}:d={fade_out:.3f}[vo];")
    else:
        lines.append(f"[{cur}]null[vo];")

    if info.has_audio:
        chain = "loudnorm=I=-16:TP=-1.5:LRA=11" if normalize_audio else "anull"
        if fade_out > 0 and out_dur > fade_out * 2:
            chain += f",afade=t=out:st={out_dur - fade_out:.3f}:d={fade_out:.3f}"
        lines.append(f"[ac]{chain}[ao]")
    script = "\n".join(lines)
    return script.rstrip(";")


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
    grade: str = "none",
    color_pops: list[tuple[float, float]] | None = None,
    broll_images: list[tuple[str, float, float]] | None = None,  # (image path, start, end)
    fade_out: float = 0.0,
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
            shutil.copytree(FONTS_DIR, work / fonts_name, dirs_exist_ok=True)

    fps = info.fps if 1 <= info.fps <= 60 else 30.0
    extra_inputs: list[str] = []
    brolls: list[tuple[int, float, float]] = []
    for k, (img, start, end) in enumerate(broll_images or []):
        if not img or not os.path.exists(img):
            continue
        idx = 1 + len(brolls)
        extra_inputs += ["-loop", "1", "-framerate", f"{fps:.3f}", "-t", f"{end - start + 0.2:.3f}", "-i", os.path.abspath(img)]
        brolls.append((idx, start, end))

    script = build_filter_script(
        plan, info, width, height, fit, subs_name, fonts_name, normalize_audio,
        grade=grade, color_pops=color_pops, brolls=brolls, fade_out=fade_out,
    )
    (work / "filter.txt").write_text(script)

    args = [
        binary, "-y", "-hide_banner", "-loglevel", "error", "-nostats", "-progress", "pipe:1",
        "-i", os.path.abspath(info.path), *extra_inputs,
        "-filter_complex_script", "filter.txt",
        "-map", "[vo]",
    ]
    if info.has_audio:
        args += ["-map", "[ao]"]
    args += ffbin.video_encoder_args(binary, width, height, prefer_hw=prefer_hw)
    args += ["-pix_fmt", "yuv420p", "-r", info.fps_fraction if fps == info.fps else str(fps), "-fps_mode", "cfr"]
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
