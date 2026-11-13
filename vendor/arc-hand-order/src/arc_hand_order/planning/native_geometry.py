"""Immutable ArcCreate input geometry transferred once to native search."""

import math
from .arc_scoring import source_note
from .two_finger import frame_ms, frame_at_or_after


def prepare(graph, items):
    geometry = graph.native_geometry
    camera = geometry.camera
    scores = []
    for item in items:
        points = []
        if item.kind == "chain":
            for point in item.score_points:
                note = source_note(item, point)
                first = frame_at_or_after(
                    point.contact_window.earliest_ms, graph.offset
                )
                stop = frame_at_or_after(
                    point.contact_window.latest_ms + 1, graph.offset
                )
                points.append(
                    {
                        "first": first,
                        "targets": [
                            note.position(
                                min(
                                    max(frame_ms(f, graph.offset), note.start), note.end
                                )
                            )
                            for f in range(first, stop)
                        ],
                    }
                )
        for point in points:
            point["rectangles"] = (
                [
                    camera.contact_rectangle(
                        "chain", *xy, graph.options.contact_offset_fractions
                    )
                    for xy in point["targets"]
                ]
                if graph.options.contact_offset_fractions is not None
                else []
            )
        scores.append(
            {
                "native": item.native_scoring,
                "colour": item.colour if item.colour is not None else -1,
                "points": points,
            }
        )
    guards = {}
    for f, weight in geometry.guards.items():
        targets = {}
        for colour, note in geometry.active_notes.get(f, ()):
            targets.setdefault(colour, []).append(
                graph.projection.judgement_to_device(
                    *note.position(frame_ms(f, graph.offset))
                )
            )
        guards[f] = {"weight": weight, "targets": targets}
    return {
        "camera": {
            "width": camera.width,
            "height": camera.height,
            "position": camera.position,
            "forward": camera.forward,
            "scale": camera.scale,
            "bases": camera.bases,
        },
        "offset": graph.offset,
        "regions": geometry.regions,
        "grace": sorted(geometry.graced),
        "guards": guards,
        "scores": scores,
        "lock_grace": graph.lock_grace,
        "arc_notes": [
            (c, a, b, interval if math.isfinite(interval) else None)
            for c, a, b, interval in graph.arc_notes
        ],
    }
