"""autocut — automatic vlog/reels editor: silence cuts, speed ramps, burned subtitles."""

__version__ = "0.1.0"

from .pipeline import run, make_plan, render_plan  # noqa: F401
from .plan import EditPlan, Segment, Cue  # noqa: F401
from .presets import PRESETS, resolve_settings  # noqa: F401
