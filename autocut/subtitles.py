"""Write burned-subtitle files (ASS for rendering, SRT for platforms)."""

from __future__ import annotations

from pathlib import Path

from .plan import Cue

# Style presets. Colours are ASS &HAABBGGRR (alpha, blue, green, red).
STYLES: dict[str, dict] = {
    # white bold, black outline — the "classic CapCut" look
    "classic": dict(primary="&H00FFFFFF", outline_col="&H00000000", back="&H80000000", border_style=1, outline=3.2, shadow=1.2),
    # white text over a soft black box
    "box": dict(primary="&H00FFFFFF", outline_col="&H00000000", back="&HA0000000", border_style=3, outline=1.5, shadow=0),
    # yellow bold with black outline (high contrast for sunlight vlogs)
    "yellow": dict(primary="&H0000E5FF", outline_col="&H00000000", back="&H80000000", border_style=1, outline=3.2, shadow=1.2),
}


def _ts(t: float) -> str:
    t = max(t, 0.0)
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h:d}:{m:02d}:{s:05.2f}"


def _escape(text: str) -> str:
    return text.replace("{", "(").replace("}", ")").replace("\n", "\\N")


def write_ass(
    cues: list[Cue],
    path: str | Path,
    width: int,
    height: int,
    font: str = "Inter",
    font_size: int = 64,
    margin_v: int = 420,
    margin_h: int = 80,
    style: str = "classic",
) -> str:
    st = STYLES.get(style, STYLES["classic"])
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{font_size},{st['primary']},&H000000FF,{st['outline_col']},{st['back']},-1,0,0,0,100,100,0.5,0,{st['border_style']},{st['outline']},{st['shadow']},2,{margin_h},{margin_h},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header]
    for c in cues:
        lines.append(f"Dialogue: 0,{_ts(c.start)},{_ts(c.end)},Default,,0,0,0,,{_escape(c.text)}\n")
    Path(path).write_text("".join(lines), encoding="utf-8")
    return str(path)


def write_srt(cues: list[Cue], path: str | Path) -> str:
    def ts(t: float) -> str:
        t = max(t, 0.0)
        ms = int(round((t - int(t)) * 1000))
        s = int(t)
        return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d},{ms:03d}"

    out = []
    for i, c in enumerate(cues, 1):
        out.append(f"{i}\n{ts(c.start)} --> {ts(c.end)}\n{c.text}\n\n")
    Path(path).write_text("".join(out), encoding="utf-8")
    return str(path)
