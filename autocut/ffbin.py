"""Locate ffmpeg/ffprobe binaries and check capabilities.

Resolution order for ffmpeg:
  1. $FFMPEG_BIN
  2. ffmpeg on PATH
  3. static binary bundled by the `imageio-ffmpeg` pip package (in the wheel, has libass)
  4. `static-ffmpeg` pip package, only if AUTOCUT_ALLOW_STATIC_FFMPEG_DOWNLOAD=1 (downloads at runtime)

Only binaries that have the `subtitles` filter (libass) are accepted when
`require_subtitles=True`, because burning captions needs libass.
"""

from __future__ import annotations

import functools
import os
import platform
import shutil
import subprocess
from typing import Iterator


def _run(args: list[str]) -> str:
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return proc.stdout + proc.stderr


def _static_ffmpeg_pair() -> tuple[str, str] | None:
    try:
        from static_ffmpeg import run as sf_run  # type: ignore

        ffmpeg, ffprobe = sf_run.get_or_fetch_platform_executables_else_raise()
        return ffmpeg, ffprobe
    except Exception:
        return None


def _imageio_ffmpeg() -> str | None:
    try:
        import imageio_ffmpeg  # type: ignore

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def _ffmpeg_candidates() -> Iterator[str]:
    env = os.environ.get("FFMPEG_BIN")
    if env:
        yield env
    found = shutil.which("ffmpeg")
    if found:
        yield found
    iio = _imageio_ffmpeg()  # ships inside the wheel, no download at runtime
    if iio:
        yield iio
    if os.environ.get("AUTOCUT_ALLOW_STATIC_FFMPEG_DOWNLOAD"):
        pair = _static_ffmpeg_pair()  # downloads ~40MB on first use; opt-in
        if pair:
            yield pair[0]


def _ffprobe_candidates() -> Iterator[str]:
    env = os.environ.get("FFPROBE_BIN")
    if env:
        yield env
    found = shutil.which("ffprobe")
    if found:
        yield found
    if os.environ.get("AUTOCUT_ALLOW_STATIC_FFMPEG_DOWNLOAD"):
        pair = _static_ffmpeg_pair()
        if pair:
            yield pair[1]


@functools.lru_cache(maxsize=None)
def has_filter(binary: str, name: str) -> bool:
    out = _run([binary, "-hide_banner", "-filters"])
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[1] == name and "->" in parts[2]:
            return True
    return False


@functools.lru_cache(maxsize=None)
def has_encoder(binary: str, name: str) -> bool:
    out = _run([binary, "-hide_banner", "-encoders"])
    return any(len(p := line.split()) >= 2 and p[1] == name for line in out.splitlines())


@functools.lru_cache(maxsize=None)
def ffmpeg(require_subtitles: bool = True) -> str:
    rejected: list[str] = []
    for cand in _ffmpeg_candidates():
        if not cand or not os.path.exists(cand):
            continue
        if require_subtitles and not has_filter(cand, "subtitles"):
            rejected.append(f"{cand} (built without libass)")
            continue
        return cand
    hint = (
        "No usable ffmpeg found. Install one with libass: `brew install ffmpeg` (mac), "
        "`apt-get install ffmpeg` (debian) or `pip install static-ffmpeg`."
    )
    if rejected:
        hint += " Rejected: " + "; ".join(rejected)
    raise RuntimeError(hint)


@functools.lru_cache(maxsize=None)
def ffprobe() -> str | None:
    for cand in _ffprobe_candidates():
        if cand and os.path.exists(cand):
            return cand
    return None


def video_encoder_args(binary: str, width: int, height: int, prefer_hw: bool = True) -> list[str]:
    """Pick a sane H.264 encoder: VideoToolbox on macOS (fast), libx264 elsewhere."""
    pixels = width * height
    bitrate = "12M" if pixels >= 1920 * 1080 else "6M"
    if (
        prefer_hw
        and platform.system() == "Darwin"
        and not os.environ.get("AUTOCUT_SOFTWARE_ENCODE")
        and has_encoder(binary, "h264_videotoolbox")
    ):
        return ["-c:v", "h264_videotoolbox", "-b:v", bitrate, "-maxrate", bitrate, "-profile:v", "high"]
    if has_encoder(binary, "libx264"):
        return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-profile:v", "high"]
    if has_encoder(binary, "h264_videotoolbox"):
        return ["-c:v", "h264_videotoolbox", "-b:v", bitrate]
    return ["-c:v", "mpeg4", "-q:v", "3"]
