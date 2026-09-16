"""Command line: `python -m autocut input.mp4 -o out.mp4 --preset reels`"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import pipeline
from .plan import EditPlan
from .presets import PRESETS


def _progress_printer():
    last = {"stage": "", "t": 0.0}

    def fn(stage: str, frac: float) -> None:
        now = time.time()
        if stage != last["stage"] or now - last["t"] > 1.0 or frac >= 1.0:
            sys.stderr.write(f"\r  [{frac * 100:5.1f}%] {stage:<24}")
            sys.stderr.flush()
            last["stage"], last["t"] = stage, now

    return fn


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="autocut", description="Auto-edit vlogs: silence cuts, speed-ups, burned subtitles.")
    p.add_argument("input", help="source video")
    p.add_argument("-o", "--output", help="output mp4 (default: <input>_autocut.mp4)")
    p.add_argument("--preset", default="auto", choices=list(PRESETS))
    p.add_argument("--workdir", help="keep intermediates here (plan.json, transcript.json, subs.ass)")
    p.add_argument("--speed", type=float)
    p.add_argument("--speed-mode", choices=["all", "long", "none"])
    p.add_argument("--long-threshold", type=float)
    p.add_argument("--min-silence", type=float)
    p.add_argument("--aspect", choices=["auto", "16:9", "9:16", "1:1"])
    p.add_argument("--fit", choices=["blur", "crop", "pad"])
    p.add_argument("--style", dest="subtitle_style", choices=["classic", "box", "yellow"])
    p.add_argument("--uppercase", action="store_true", default=None)
    p.add_argument("--no-subs", action="store_true")
    p.add_argument("--punch-in", dest="punch_in", action="store_true", default=None)
    p.add_argument("--no-punch-in", dest="punch_in", action="store_false")
    p.add_argument("--no-normalize", action="store_true")
    p.add_argument("--language", help="ISO code, e.g. pt, en (default pt)")
    p.add_argument("--backend", dest="transcribe_backend", choices=["auto", "local", "api"])
    p.add_argument("--model", dest="whisper_model", help="faster-whisper model for local backend")
    p.add_argument("--plan-only", action="store_true", help="transcribe + plan, do not render")
    p.add_argument("--from-plan", help="render an existing (edited) plan.json")
    p.add_argument("--software", action="store_true", help="force libx264 instead of hardware encoder")
    a = p.parse_args(argv)

    out = a.output or str(Path(a.input).with_name(Path(a.input).stem + "_autocut.mp4"))
    workdir = a.workdir or str(Path(out).with_suffix("")) + "_work"
    overrides = {
        k: getattr(a, k)
        for k in ["speed", "speed_mode", "long_threshold", "min_silence", "aspect", "fit", "subtitle_style",
                  "uppercase", "punch_in", "language", "transcribe_backend", "whisper_model"]
        if getattr(a, k) is not None
    }
    if a.no_subs:
        overrides["subtitles"] = False
    if a.no_normalize:
        overrides["normalize_audio"] = False

    progress = _progress_printer()
    t0 = time.time()
    if a.from_plan:
        edit = EditPlan.load(a.from_plan)
        pipeline.render_plan(edit, out, workdir, progress=progress, prefer_hw=not a.software)
    elif a.plan_only:
        edit, _, _ = pipeline.make_plan(a.input, workdir, a.preset, overrides, progress)
    else:
        edit = pipeline.run(a.input, out, a.preset, overrides, workdir=workdir, progress=progress, prefer_hw=not a.software)
    sys.stderr.write("\n")
    print(json.dumps({"output": None if a.plan_only else out, "workdir": workdir, "seconds": round(time.time() - t0, 1), **edit.stats()}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
