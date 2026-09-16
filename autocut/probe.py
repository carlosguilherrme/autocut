"""Read basic media info (duration, size, fps, rotation, audio presence)."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, asdict
from fractions import Fraction

from . import ffbin


@dataclass
class MediaInfo:
    path: str
    duration: float
    width: int  # coded width
    height: int  # coded height
    display_width: int  # after rotation metadata is applied (what ffmpeg filters see)
    display_height: int
    fps: float
    fps_fraction: str  # e.g. "30000/1001"
    rotation: int
    has_audio: bool
    video_codec: str = ""

    @property
    def aspect(self) -> float:
        return self.display_width / self.display_height

    def to_dict(self) -> dict:
        return asdict(self)


def _parse_fps(rate: str) -> tuple[float, str]:
    try:
        frac = Fraction(rate)
        if frac <= 0:
            raise ValueError
        return float(frac), f"{frac.numerator}/{frac.denominator}"
    except (ValueError, ZeroDivisionError):
        return 30.0, "30/1"


def _rotation_of(stream: dict) -> int:
    rot = 0
    for sd in stream.get("side_data_list", []) or []:
        if "rotation" in sd:
            try:
                rot = int(round(float(sd["rotation"])))
            except (TypeError, ValueError):
                pass
    tags = stream.get("tags", {}) or {}
    if not rot and "rotate" in tags:
        try:
            rot = int(tags["rotate"])
        except (TypeError, ValueError):
            pass
    return rot % 360


def probe(path: str) -> MediaInfo:
    fp = ffbin.ffprobe()
    if fp:
        return _probe_with_ffprobe(fp, path)
    return _probe_with_ffmpeg(path)


def _probe_with_ffprobe(fp: str, path: str) -> MediaInfo:
    proc = subprocess.run(
        [fp, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {proc.stderr.strip()}")
    data = json.loads(proc.stdout)
    video = next((s for s in data["streams"] if s.get("codec_type") == "video"), None)
    if video is None:
        raise RuntimeError("input has no video stream")
    has_audio = any(s.get("codec_type") == "audio" for s in data["streams"])
    fps, fps_frac = _parse_fps(video.get("avg_frame_rate") or video.get("r_frame_rate") or "30/1")
    if fps > 120 or fps < 1:
        fps, fps_frac = _parse_fps(video.get("r_frame_rate") or "30/1")
    duration = float(data.get("format", {}).get("duration") or video.get("duration") or 0)
    w, h = int(video["width"]), int(video["height"])
    rot = _rotation_of(video)
    dw, dh = (h, w) if rot in (90, 270) else (w, h)
    return MediaInfo(
        path=path,
        duration=duration,
        width=w,
        height=h,
        display_width=dw,
        display_height=dh,
        fps=fps,
        fps_fraction=fps_frac,
        rotation=rot,
        has_audio=has_audio,
        video_codec=video.get("codec_name", ""),
    )


def _probe_with_ffmpeg(path: str) -> MediaInfo:
    """Fallback when no ffprobe is available (e.g. imageio-ffmpeg only)."""
    out = subprocess.run(
        [ffbin.ffmpeg(require_subtitles=False), "-hide_banner", "-i", path], capture_output=True, text=True
    ).stderr
    m = re.search(r"Duration: (\d+):(\d+):(\d+\.?\d*)", out)
    duration = 0.0
    if m:
        duration = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    v = re.search(r"Video: (\w+).*?, (\d{2,5})x(\d{2,5})", out)
    if not v:
        raise RuntimeError("could not parse video stream info")
    w, h = int(v.group(2)), int(v.group(3))
    f = re.search(r"(\d+\.?\d*) fps", out)
    fps = float(f.group(1)) if f else 30.0
    fps_frac = f"{Fraction(fps).limit_denominator(1001).numerator}/{Fraction(fps).limit_denominator(1001).denominator}"
    r = re.search(r"rotation of (-?\d+\.?\d*)", out) or re.search(r"rotate\s*:\s*(-?\d+)", out)
    rot = int(round(float(r.group(1)))) % 360 if r else 0
    dw, dh = (h, w) if rot in (90, 270) else (w, h)
    return MediaInfo(
        path=path,
        duration=duration,
        width=w,
        height=h,
        display_width=dw,
        display_height=dh,
        fps=fps,
        fps_fraction=fps_frac,
        rotation=rot,
        has_audio="Audio:" in out,
        video_codec=v.group(1),
    )
