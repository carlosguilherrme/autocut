"""Write burned-subtitle files (ASS for rendering, SRT for platforms).

Three ASS styles live in one file:
  Default — phrase captions, 1–2 lines at the bottom (classic / box / yellow variants)
  Word    — one big lowercase word at a time, centred around 77 % of the height
  Title   — serif title card with a thin underline, centred a bit above the middle
"""

from __future__ import annotations

from pathlib import Path

from .plan import Cue

# Phrase-style colour variants. Colours are ASS &HAABBGGRR (alpha, blue, green, red).
STYLES: dict[str, dict] = {
    "classic": dict(primary="&H00FFFFFF", outline_col="&H00000000", back="&H80000000", border_style=1, outline=3.2, shadow=1.2),
    "box": dict(primary="&H00FFFFFF", outline_col="&H00000000", back="&HA0000000", border_style=3, outline=1.5, shadow=0),
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


def write_ass(cues: list[Cue], path: str | Path, width: int, height: int, s: dict) -> str:
    """`s` is the resolved settings dict (see presets.py) — fonts, sizes, positions."""
    st = STYLES.get(s.get("subtitle_style", "classic"), STYLES["classic"])
    font = s.get("font", "Poppins")
    word_font = s.get("word_font", "Poppins Bold")
    title_font = s.get("title_font", "Playfair Display")
    word_size = int(s.get("word_size", 100))
    title_size = int(s.get("title_size", 120))
    cx = width / 2
    word_y = int(height * float(s.get("word_y", 0.77)))
    title_y = int(height * float(s.get("title_y", 0.56)))

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{int(s.get('font_size', 64))},{st['primary']},&H000000FF,{st['outline_col']},{st['back']},-1,0,0,0,100,100,0.5,0,{st['border_style']},{st['outline']},{st['shadow']},2,{int(s.get('margin_h', 70))},{int(s.get('margin_h', 70))},{int(s.get('margin_v', 420))},1
Style: Word,{word_font},{word_size},&H00FFFFFF,&H000000FF,&H00000000,&H70000000,0,0,0,0,100,100,0,0,1,0,3,5,0,0,0,1
Style: Title,{title_font},{title_size},&H00FFFFFF,&H000000FF,&H00000000,&H70000000,-1,0,0,0,100,100,0,0,1,0,3,5,0,0,0,1
Style: Line,{title_font},{title_size},&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header]
    for c in cues:
        if c.style == "Word":
            fx = f"{{\\pos({cx:.0f},{word_y})\\fscx86\\fscy86\\t(0,80,\\fscx100\\fscy100)}}"
            lines.append(f"Dialogue: 1,{_ts(c.start)},{_ts(c.end)},Word,,0,0,0,,{fx}{_escape(c.text)}\n")
        elif c.style == "Title":
            n_lines = c.text.count("\n") + 1
            longest = max(len(x) for x in c.text.split("\n"))
            line_w = int(min(longest * title_size * 0.42, width * 0.6))  # ~65 % of the estimated text width
            line_y = title_y + int(n_lines * title_size * 0.62) + 22
            fx = f"{{\\pos({cx:.0f},{title_y})\\fad(180,180)}}"
            lines.append(f"Dialogue: 2,{_ts(c.start)},{_ts(c.end)},Title,,0,0,0,,{fx}{_escape(c.text)}\n")
            # thin underline: a drawing whose box is centred on \pos (an5), non-negative coords
            draw = f"{{\\an5\\pos({cx:.0f},{line_y})\\fad(180,180)\\alpha&H30&\\p1}}m 0 0 l {line_w} 0 l {line_w} 3 l 0 3{{\\p0}}"
            lines.append(f"Dialogue: 2,{_ts(c.start)},{_ts(c.end)},Line,,0,0,0,,{draw}\n")
        else:
            lines.append(f"Dialogue: 0,{_ts(c.start)},{_ts(c.end)},Default,,0,0,0,,{_escape(c.text)}\n")
    Path(path).write_text("".join(lines), encoding="utf-8")
    return str(path)


def write_srt(cues: list[Cue], path: str | Path) -> str:
    """SRT of the spoken captions only (titles are decoration)."""

    def ts(t: float) -> str:
        t = max(t, 0.0)
        ms = int(round((t - int(t)) * 1000))
        sec = int(t)
        return f"{sec // 3600:02d}:{(sec % 3600) // 60:02d}:{sec % 60:02d},{ms:03d}"

    out = []
    n = 0
    for c in cues:
        if c.style == "Title":
            continue
        n += 1
        out.append(f"{n}\n{ts(c.start)} --> {ts(c.end)}\n{c.text}\n\n")
    Path(path).write_text("".join(out), encoding="utf-8")
    return str(path)
