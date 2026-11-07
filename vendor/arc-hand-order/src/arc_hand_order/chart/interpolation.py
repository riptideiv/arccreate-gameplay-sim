"""AFF arc geometry: curve interpolation, clamping and the zero-duration arc.

The curves are evaluated as ArcCreate (https://github.com/Arcthesia/ArcCreate) evaluates them.
"""

from __future__ import annotations

import math

from arc_hand_order.schemas.chart_ir import ArcEvent

SUPPORTED_INTERPOLATIONS = frozenset({"s", "b", "si", "so", "sisi", "siso", "sosi", "soso"})


def _weight(kind: str, t: float) -> float:
    if kind == "s":
        return t
    if kind == "b":
        # The cubic Bezier has both first controls at the start and both last
        # controls at the end, so its endpoint weight is smoothstep.
        return t * t * (3.0 - 2.0 * t)
    if kind == "si":
        return math.sin(math.pi * t / 2.0)
    return 1.0 - math.cos(math.pi * t / 2.0)


def arc_position(arc: ArcEvent, time_ms: float) -> tuple[float, float]:
    """Return AFF x/y at chart time; time clamps, AFF coordinates do not.

    ``si``/``so`` affect x only; a second suffix chooses the y interpolation.
    A zero-duration Arc uses its start at or before the event and end after it.
    Unknown curves raise instead of silently becoming a straight line.
    """
    if arc.interpolation not in SUPPORTED_INTERPOLATIONS:
        raise ValueError(f"unsupported Arc interpolation: {arc.interpolation}")
    if isinstance(time_ms, bool) or not math.isfinite(time_ms):
        raise ValueError("chart time must be finite milliseconds")
    duration_ms = arc.end_time_ms - arc.start_time_ms
    if duration_ms == 0:
        xy = arc.start_aff_xy if time_ms <= arc.start_time_ms else arc.end_aff_xy
        return float(xy[0]), float(xy[1])
    t = (time_ms - arc.start_time_ms) / duration_ms
    return interpolate_aff_xy(arc.interpolation, arc.start_aff_xy, arc.end_aff_xy, t)


def interpolate_aff_xy(
    interpolation: str,
    start_xy: tuple[float, float] | list[float],
    end_xy: tuple[float, float] | list[float],
    t: float,
) -> tuple[float, float]:
    """Evaluate an AFF curve at normalized progress, clamped to [0, 1]."""
    if interpolation not in SUPPORTED_INTERPOLATIONS:
        raise ValueError(f"unsupported Arc interpolation: {interpolation}")
    if isinstance(t, bool) or not math.isfinite(t):
        raise ValueError("normalized Arc progress must be finite")
    if len(start_xy) != 2 or len(end_xy) != 2 or any(
        isinstance(value, bool) or not math.isfinite(value) for value in (*start_xy, *end_xy)
    ):
        raise ValueError("Arc endpoints must be finite AFF coordinate pairs")
    t = min(1.0, max(0.0, t))
    if interpolation in {"s", "b"}:
        x_curve = y_curve = interpolation
    else:
        x_curve = interpolation[:2]
        y_curve = interpolation[2:] if len(interpolation) == 4 else "s"
    wx, wy = _weight(x_curve, t), _weight(y_curve, t)
    x0, y0 = start_xy
    x1, y1 = end_xy
    return x0 + (x1 - x0) * wx, y0 + (y1 - y0) * wy
