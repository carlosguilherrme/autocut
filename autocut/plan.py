"""Edit plan: which source ranges to keep, at what speed, and the subtitle cues
expressed on the OUTPUT timeline.

The plan is plain JSON so a UI can show it, let the user toggle speeds or drop
segments, and send it back for rendering.
"""

from __future__ import annotations

import bisect
import json
import math
from dataclasses import dataclass, field, asdict
from pathlib import Path

from .transcribe import Word
from .speech import Region

_END_PUNCT = ".?!…"


@dataclass
class Segment:
    src_start: float
    src_end: float
    speed: float = 1.0
    zoom: float = 1.0
    enabled: bool = True
    label: str = ""

    @property
    def src_dur(self) -> float:
        return max(self.src_end - self.src_start, 0.0)

    @property
    def out_dur(self) -> float:
        return self.src_dur / self.speed


@dataclass
class Cue:
    start: float  # output timeline
    end: float
    text: str  # may contain "\n" for a manual line break
    style: str = "Default"  # "Default" (phrase) | "Word" (big single word) | "Title" (serif title card)
    image: str | None = None  # Title only: optional full-screen b-roll image path
    prompt: str | None = None  # Title only: image prompt used/suggested
    search: str | None = None  # Title only: short English search terms for stock-photo providers
    anchor: float | None = None  # Title only: source time the title is tied to (re-mapped on re-render)


@dataclass
class EditPlan:
    source: str
    source_duration: float
    fps: float
    segments: list[Segment] = field(default_factory=list)
    cues: list[Cue] = field(default_factory=list)
    settings: dict = field(default_factory=dict)

    # ---- timeline helpers
    def active_segments(self) -> list[Segment]:
        return [s for s in self.segments if s.enabled and s.src_dur > 0]

    def out_duration(self) -> float:
        return sum(s.out_dur for s in self.active_segments())

    def _out_starts(self) -> tuple[list[float], list[Segment]]:
        segs = self.active_segments()
        starts: list[float] = []
        acc = 0.0
        for s in segs:
            starts.append(acc)
            acc += s.out_dur
        return starts, segs

    def map_time(self, t: float) -> float | None:
        """Source time -> output time. None if `t` falls inside a removed range."""
        starts, segs = self._out_starts()
        if not segs:
            return None
        src_starts = [s.src_start for s in segs]
        i = bisect.bisect_right(src_starts, t) - 1
        if i < 0:
            return None
        seg = segs[i]
        if t > seg.src_end + 1e-6:
            return None
        return starts[i] + (t - seg.src_start) / seg.speed

    def map_time_clamped(self, t: float) -> float:
        """Like map_time but snaps times inside removed ranges to the nearest kept edge."""
        starts, segs = self._out_starts()
        if not segs:
            return 0.0
        src_starts = [s.src_start for s in segs]
        i = bisect.bisect_right(src_starts, t) - 1
        if i < 0:
            return 0.0
        seg = segs[i]
        if t <= seg.src_end:
            return starts[i] + (t - seg.src_start) / seg.speed
        return starts[i] + seg.out_dur  # end of this segment

    # ---- (de)serialisation
    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "source_duration": self.source_duration,
            "fps": self.fps,
            "settings": self.settings,
            "segments": [asdict(s) for s in self.segments],
            "cues": [asdict(c) for c in self.cues],
            "stats": self.stats(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "EditPlan":
        return cls(
            source=d["source"],
            source_duration=float(d["source_duration"]),
            fps=float(d.get("fps", 30)),
            segments=[Segment(**s) for s in d.get("segments", [])],
            cues=[Cue(**c) for c in d.get("cues", [])],
            settings=d.get("settings", {}),
        )

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=1))

    @classmethod
    def load(cls, path: str | Path) -> "EditPlan":
        return cls.from_dict(json.loads(Path(path).read_text()))

    def stats(self) -> dict:
        segs = self.active_segments()
        kept = sum(s.src_dur for s in segs)
        return {
            "segments": len(segs),
            "cuts": max(len(segs) - 1, 0),
            "kept_source_seconds": round(kept, 2),
            "removed_seconds": round(self.source_duration - kept, 2),
            "output_seconds": round(self.out_duration(), 2),
            "sped_up_segments": sum(1 for s in segs if s.speed > 1.001),
            "cues": len(self.cues),
        }


# --------------------------------------------------------------------------- building


def _snap(t: float, fps: float, mode: str = "round") -> float:
    """Snap to a frame boundary so audio/video segment lengths match exactly."""
    n = t * fps
    n = {"floor": math.floor, "ceil": math.ceil}.get(mode, round)(n)
    return max(n / fps, 0.0)


def build_segments(
    regions: list[Region],
    duration: float,
    fps: float,
    pad_before: float,
    pad_after: float,
    min_segment: float = 0.3,
    keep_head: bool = False,
    keep_tail: bool = False,
) -> list[Segment]:
    """Turn speech regions into kept source ranges with breathing room."""
    regions = [r for r in regions if r[1] - r[0] >= min_segment] or list(regions)
    if not regions:
        return [Segment(0.0, duration)]
    segs: list[Segment] = []
    for i, (s, e) in enumerate(regions):
        prev_end = regions[i - 1][1] if i else 0.0
        next_start = regions[i + 1][0] if i + 1 < len(regions) else duration
        gap_b = s - prev_end
        gap_a = next_start - e
        # share the gap between neighbours when it's too small for both pads
        pb = min(pad_before, gap_b * 0.5) if i else (gap_b if keep_head else min(pad_before, gap_b))
        pa = min(pad_after, gap_a * 0.5) if i + 1 < len(regions) else (gap_a if keep_tail else min(pad_after, gap_a))
        start = _snap(max(s - pb, 0.0), fps, "floor")
        end = _snap(min(e + pa, duration), fps, "ceil")
        if segs and start < segs[-1].src_end:
            start = segs[-1].src_end
        if end - start >= 1.0 / fps:
            segs.append(Segment(start, end))
    return segs or [Segment(0.0, duration)]


def apply_speed(segments: list[Segment], speed: float, mode: str, long_threshold: float) -> None:
    for s in segments:
        if mode == "all":
            s.speed = speed
        elif mode == "long":
            s.speed = speed if s.src_dur >= long_threshold else 1.0
        else:
            s.speed = 1.0


def apply_punch_in(segments: list[Segment], zooms: tuple[float, ...] = (1.0, 1.08, 1.0, 1.12), min_dur: float = 1.5) -> None:
    """Alternate subtle zoom levels between cuts (classic vlog jump-cut feel)."""
    if len(segments) < 2:
        return
    k = 0
    for s in segments:
        if s.src_dur < min_dur:
            s.zoom = 1.0
            continue
        s.zoom = zooms[k % len(zooms)]
        k += 1


# --------------------------------------------------------------------------- subtitles


def _balance_two_lines(text: str, line_chars: int) -> str:
    if len(text) <= line_chars:
        return text
    words = text.split()
    best, best_diff = None, 10**9
    for i in range(1, len(words)):
        a, b = " ".join(words[:i]), " ".join(words[i:])
        diff = abs(len(a) - len(b))
        if max(len(a), len(b)) <= line_chars + 4 and diff < best_diff:
            best, best_diff = (a, b), diff
    if best is None:
        mid = len(words) // 2
        best = (" ".join(words[:mid]), " ".join(words[mid:]))
    return best[0] + "\n" + best[1]


def _segment_index(plan: EditPlan, t: float) -> int:
    segs = plan.active_segments()
    src_starts = [s.src_start for s in segs]
    i = bisect.bisect_right(src_starts, t) - 1
    return i if i >= 0 and t <= segs[i].src_end + 1e-6 else -1


def _mid(w: Word) -> float:
    return (w.start + w.end) / 2


def _text_len(words: list[Word]) -> int:
    return len(" ".join(w.text for w in words))


def _by_transcript_segment(words: list[Word], segments: list[dict]) -> list[list[Word]]:
    """Whisper already splits speech into natural phrases; use them as the first cut."""
    if not segments:
        return [list(words)]
    starts = [float(s["start"]) for s in segments]
    buckets: list[list[Word]] = [[] for _ in segments]
    for w in words:
        i = max(bisect.bisect_right(starts, w.start + 0.02) - 1, 0)
        buckets[i].append(w)
    return [b for b in buckets if b]


def _hard_split(words: list[Word], plan: EditPlan, max_gap: float) -> list[list[Word]]:
    """Split at long pauses and wherever a cut was made between two words."""
    groups: list[list[Word]] = []
    cur: list[Word] = []
    for w in words:
        if cur:
            gap = w.start - cur[-1].end
            crosses_cut = _segment_index(plan, _mid(w)) != _segment_index(plan, _mid(cur[-1]))
            if gap > max_gap or crosses_cut:
                groups.append(cur)
                cur = []
        cur.append(w)
    if cur:
        groups.append(cur)
    return groups


def _soft_split(words: list[Word], max_chars: int, max_duration: float) -> list[list[Word]]:
    """Recursively split an over-long phrase at the most natural point:
    prefer pauses and punctuation, then balanced halves."""
    if len(words) <= 1 or (_text_len(words) <= max_chars and words[-1].end - words[0].start <= max_duration):
        return [words]
    best_i, best_score = 1, -1e9
    for i in range(1, len(words)):
        la, lb = _text_len(words[:i]), _text_len(words[i:])
        gap = max(words[i].start - words[i - 1].end, 0.0)
        punct = 1.0 if words[i - 1].text[-1:] in ",;:" + _END_PUNCT else 0.0
        balance = 1.0 - abs(la - lb) / max(la + lb, 1)
        score = 4.0 * min(gap, 0.8) / 0.8 + 3.0 * punct + 2.0 * balance
        if score > best_score:
            best_i, best_score = i, score
    return _soft_split(words[:best_i], max_chars, max_duration) + _soft_split(words[best_i:], max_chars, max_duration)


def _is_tiny(g: list[Word]) -> bool:
    return len(g) <= 2 or (g[-1].end - g[0].start) < 0.9


def _merge_tiny(groups: list[list[Word]], plan: EditPlan, max_chars: int, max_duration: float, join_gap: float = 0.35) -> list[list[Word]]:
    """Glue orphan fragments ("e olham", "bem comum") onto a neighbour, then re-split
    the union at its most natural point so both halves read as phrases."""
    out: list[list[Word]] = []
    for g in groups:
        if out:
            prev = out[-1]
            gap = g[0].start - prev[-1].end
            same_take = _segment_index(plan, _mid(g[0])) == _segment_index(plan, _mid(prev[-1]))
            prev_ends_sentence = prev[-1].text[-1:] in _END_PUNCT
            if same_take and gap <= join_gap and not prev_ends_sentence and (_is_tiny(g) or _is_tiny(prev)):
                merged = prev + g
                pieces = _soft_split(merged, max_chars, max_duration)
                if len(pieces) == 1 or all(not _is_tiny(p) for p in pieces):
                    out[-1:] = pieces
                    continue
        out.append(g)
    return out


def build_cues(
    words: list[Word],
    plan: EditPlan,
    segments: list[dict] | None = None,
    max_chars: int = 44,
    max_duration: float = 4.5,
    max_gap: float = 0.8,
    min_duration: float = 0.7,
    tail: float = 0.15,
    uppercase: bool = False,
) -> list[Cue]:
    """Group words into phrase cues (1-2 lines) on the output timeline.

    Order of decisions: transcript phrase -> pause/cut boundaries -> length/duration.
    """
    line_chars = max(math.ceil(max_chars / 2), 12)
    kept = [w for w in words if _segment_index(plan, _mid(w)) >= 0]  # drop words in removed ranges
    groups: list[list[Word]] = []
    for phrase in _by_transcript_segment(kept, segments or []):
        for chunk in _hard_split(phrase, plan, max_gap):
            groups.extend(_soft_split(chunk, max_chars, max_duration))
    groups = _merge_tiny(groups, plan, max_chars, max_duration)

    cues: list[Cue] = []
    for g in groups:
        start = plan.map_time_clamped(g[0].start)
        end = plan.map_time_clamped(g[-1].end) + tail
        text = " ".join(w.text for w in g).strip()
        if uppercase:
            text = text.upper()
        cues.append(Cue(start, end, _balance_two_lines(text, line_chars)))

    # enforce min duration and no overlaps
    for i, c in enumerate(cues):
        if c.end - c.start < min_duration:
            c.end = c.start + min_duration
        if i + 1 < len(cues):
            c.end = min(c.end, cues[i + 1].start - 0.04)
        if c.end <= c.start:
            c.end = c.start + 0.3
    total = plan.out_duration()
    for c in cues:
        c.end = min(c.end, total)
    return [c for c in cues if c.end > c.start]


# --------------------------------------------------------------------------- word-by-word captions

_PUNCT = ",.;:!?…\"'“”«»()[]"


def _clean_word(text: str, lower: bool) -> str:
    t = text.strip().strip(_PUNCT).strip()
    return t.lower() if lower else t


def build_word_cues(
    words: list[Word],
    plan: EditPlan,
    min_duration: float = 0.28,
    hold_gap: float = 0.5,
    tail: float = 0.25,
    lower: bool = True,
    merge_shorter_than: float = 0.12,
) -> list[Cue]:
    """One big word on screen at a time (CapCut "word by word" look).

    A word stays up until the next word starts (continuous feel) unless the
    pause is long; words shorter than `merge_shorter_than` seconds are glued
    to the next word so the screen does not flicker.
    """
    kept = [w for w in words if _segment_index(plan, _mid(w)) >= 0 and _clean_word(w.text, lower)]
    chunks: list[list[Word]] = []
    i = 0
    while i < len(kept):
        grp = [kept[i]]
        while (
            grp[-1].end - grp[0].start < merge_shorter_than
            and i + 1 < len(kept)
            and kept[i + 1].start - grp[-1].end < 0.2
            and _segment_index(plan, _mid(kept[i + 1])) == _segment_index(plan, _mid(grp[0]))
        ):
            i += 1
            grp.append(kept[i])
        chunks.append(grp)
        i += 1

    cues: list[Cue] = []
    for k, g in enumerate(chunks):
        start = plan.map_time_clamped(g[0].start)
        nxt = chunks[k + 1] if k + 1 < len(chunks) else None
        same_take = nxt is not None and _segment_index(plan, _mid(nxt[0])) == _segment_index(plan, _mid(g[-1]))
        if nxt is not None and same_take and nxt[0].start - g[-1].end <= hold_gap:
            end = plan.map_time_clamped(nxt[0].start) - 0.02
        else:
            end = plan.map_time_clamped(g[-1].end) + tail
        end = max(end, start + min_duration)
        text = " ".join(_clean_word(w.text, lower) for w in g).strip()
        if text:
            cues.append(Cue(start, end, text, style="Word"))
    for i, c in enumerate(cues):
        if i + 1 < len(cues):
            c.end = min(c.end, cues[i + 1].start - 0.02)
    total = plan.out_duration()
    for c in cues:
        c.end = min(c.end, total)
    return [c for c in cues if c.end > c.start]


# --------------------------------------------------------------------------- serif title cards


def _norm(t: str) -> str:
    return _clean_word(t, True)


def _find_phrase(words: list[Word], phrase: str, after: float = 0.0) -> tuple[float, float, bool] | None:
    """Locate `phrase` (1..4 words) in the word list.
    Returns (start, end, exact); exact=False means only the first word was found."""
    target = [_norm(t) for t in phrase.split() if _norm(t)]
    if not target:
        return None
    n = len(target)
    toks = [_norm(w.text) for w in words]
    for i in range(len(words) - n + 1):
        if words[i].start < after:
            continue
        if toks[i : i + n] == target:
            return words[i].start, words[i + n - 1].end, True
    for i, t in enumerate(toks):  # fallback: first word only
        if t == target[0] and words[i].start >= after:
            return words[i].start, words[i].end, False
    return None


def place_titles(
    keywords: list[dict],
    words: list[Word],
    plan: EditPlan,
    duration: float = 3.0,
    min_gap: float = 3.0,
    line_chars: int = 13,
    lower: bool = True,
) -> list[Cue]:
    """Turn picked keywords [{"phrase", "image_prompt"}] into Title cues on the output timeline.
    A title starts when the phrase is spoken and stays `duration` seconds (never past its take)."""
    found: list[tuple[float, str, dict]] = []
    for kw in keywords:
        phrase = str(kw.get("phrase", "")).strip()
        loc = _find_phrase(words, phrase)
        if loc and _segment_index(plan, loc[0]) >= 0:
            # the LLM sometimes "improves" the phrase; if it is not said verbatim, show only the word that was
            shown = phrase if loc[2] else phrase.split()[0]
            found.append((loc[0], shown, kw))
    found.sort(key=lambda f: f[0])

    titles: list[Cue] = []
    last_end = -1e9
    for src_start, phrase, kw in found:
        start = plan.map_time_clamped(src_start)
        if start - last_end < min_gap:
            continue
        end = min(start + duration, plan.out_duration())  # a card may span a cut; it is an overlay
        if end - start < 1.2:
            continue
        text = phrase.lower() if lower else phrase
        titles.append(Cue(start, end, _balance_two_lines(text, line_chars), style="Title",
                          prompt=kw.get("image_prompt"), search=kw.get("search") or phrase, anchor=src_start))
        last_end = end
    return titles


def remap_titles(titles: list[Cue], plan: EditPlan, duration: float = 3.0) -> list[Cue]:
    """After the user edits speeds/segments, move title cards with their spoken anchor."""
    out: list[Cue] = []
    for t in titles:
        if t.anchor is None:
            out.append(t)
            continue
        if _segment_index(plan, t.anchor) < 0:
            continue  # its take was disabled
        start = plan.map_time_clamped(t.anchor)
        end = min(start + duration, plan.out_duration())
        if end - start >= 1.2:
            out.append(Cue(start, end, t.text, style="Title", image=t.image, prompt=t.prompt, search=t.search, anchor=t.anchor))
    return out
