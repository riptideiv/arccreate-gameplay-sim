from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from arc_hand_order import api
from arc_hand_order.planning.occupations import occupations_from_chart
from arc_hand_order.planning.arc_scoring import native_occupations, covers
from plan_excerpt import create_plan, split_arc_ticks
from scoring_report import compare_ticks


def chart(tmp_path, body, header=""):
    f = tmp_path / "test.aff"
    f.write_text(
        "AudioOffset:0\n" + header + "-\ntiming(0,120,4);\n" + body.replace(";", ";\n")
    )
    return f


def test_stationary_finger_scores_a_zigzag_that_is_impossible_to_trace(tmp_path):
    f = chart(
        tmp_path,
        "".join(
            f"arc({1000+k*120},{1120+k*120},{k%2},{1-k%2},s,.5,.5,0,none,false);"
            for k in range(12)
        ),
    )
    score = create_plan(
        f,
        0,
        3000,
        {"max_speed_m_s": 0.1, "arc_path_mode": "score", "allow_misses": False},
    )
    assert score["planning"]["skipped_ticks"] == 0
    (touch,) = score["touches"]
    assert len(set(tuple(p) for p in touch["path_mm"])) == 1
    assert len(score["arc_tick_targets"]) == 12
    trace = create_plan(f, 0, 3000, {"max_speed_m_s": 0.1, "arc_path_mode": "trace"})
    assert trace["planning"]["skipped_ticks"] == 12


def test_native_windows_are_asymmetric_and_expire_exclusively(tmp_path):
    f = chart(tmp_path, "arc(1000,2000,0,1,s,.5,.5,0,none,false);")
    native = native_occupations(occupations_from_chart(api.load_chart(f).chart))
    points = native.items[0].score_points
    assert [p.time_ms for p in points] == [1250, 1500, 1750]
    assert [
        (p.contact_window.earliest_ms, p.contact_window.latest_ms) for p in points
    ] == [(1000, 1749), (1250, 1999), (1500, 2240)]
    assert [p.native_auto_ms for p in points] == [1000, 1250, 1500]


def test_ticks_keep_their_source_segment_after_splitting(tmp_path):
    f = chart(
        tmp_path,
        "arc(1000,1500,0,0,s,.5,.5,0,none,false);arc(1500,2000,0,1,s,.5,.5,0,none,false);",
    )
    o = native_occupations(occupations_from_chart(api.load_chart(f).chart))
    first = split_arc_ticks(o).items[0]
    # Its expired visual geometry stays on its own endpoint. It must not
    # silently jump to the next segment just because that segment is active.
    assert first.region_at(1800).x == -425


def test_a_spatially_near_snake_at_the_wrong_time_does_not_count(tmp_path):
    f = chart(tmp_path, "arc(1000,2000,0,1,s,.5,.5,0,none,false);")
    loaded = api.load_chart(f)
    o = native_occupations(occupations_from_chart(loaded.chart)).items[0]
    projection = __import__(
        "arc_hand_order.planning.projection", fromlist=["GameplayProjection"]
    ).GameplayProjection(api.load_device())
    q = projection.judgement_to_device(425, 325)
    assert covers(o, [q, q], 60, 0, projection) is None


def test_native_density_changes_requests_not_just_visual_timing(tmp_path):
    f = chart(
        tmp_path,
        "arc(1000,2000,.5,.5,s,.5,.5,0,none,false);",
        "TimingPointDensityFactor:2\n",
    )
    out = create_plan(f, 0, 2500, {"max_speed_m_s": 4, "arc_path_mode": "score"})
    assert len(out["arc_tick_targets"]) == 7


def test_native_tick_report_does_not_hide_an_unconfirmed_prediction():
    target = {
        "id": "tick",
        "time_ms": 1200,
        "planned_hit": True,
        "arc_start_ms": 1000,
        "arc_end_ms": 1500,
        "auto_ms": 1000,
        "color": 0,
        "x_start": 0.0,
        "x_end": 1.0,
        "y_start": 0.5,
        "y_end": 0.5,
    }
    native = {**target, "result": "MissLate", "chart_ms": 1700}
    report = compare_ticks({"arc_tick_targets": [target]}, {"arc_ticks": [native]})
    assert report["predicted_hits"] == 1 and report["confirmed_hits"] == 0
    assert report["unconfirmed_predictions"][0]["native_result"] == "MissLate"


def test_zero_duration_connector_changes_ticks_and_connects_the_snake(tmp_path):
    f = chart(
        tmp_path,
        "arc(1000,1250,0,.5,s,.5,.5,0,none,false);arc(1250,1250,.5,.25,s,.5,.5,0,none,false);arc(1250,1750,.25,1,s,.5,.5,0,none,false);",
    )
    out = create_plan(f, 0, 2500, {"arc_path_mode": "score", "max_speed_m_s": 4})
    assert len(out["arc_tick_targets"]) == 3
    assert [(p["arc_start_ms"], p["time_ms"]) for p in out["arc_tick_targets"]] == [
        (1000, 1125),
        (1250, 1250),
        (1250, 1500),
    ]


def test_native_seek_consumes_a_tick_exactly_at_the_window_start(tmp_path):
    f = chart(
        tmp_path,
        "arc(500,1000,.3,.5,s,.5,.5,0,none,false);"
        "arc(1000,1500,.5,.7,s,.5,.5,0,none,false);",
    )
    out = create_plan(f, 1000, 1800, {"arc_path_mode": "score", "max_speed_m_s": 4})
    assert [p["time_ms"] for p in out["arc_tick_targets"]] == [1250]


def test_native_seek_does_not_consume_a_fractional_tick_rounded_to_the_boundary(
    tmp_path,
):
    f = tmp_path / "fractional.aff"
    f.write_text(
        "AudioOffset:0\n-\ntiming(0,184,4);\narc(1000,2000,.5,.5,s,.5,.5,0,none,false);\n"
    )
    out = create_plan(f, 1163, 2000, {"arc_path_mode": "score", "max_speed_m_s": 4})
    # FirstJudgeTime is 1163.043..., so ComboAt(1163) is still zero.
    assert out["arc_tick_targets"][0]["time_ms"] == 1163


def test_ground_hold_ticks_are_not_settled_before_the_native_request_starts(tmp_path):
    f = chart(tmp_path, "hold(1000,2000,2);")
    out = create_plan(
        f,
        0,
        2500,
        {"arc_path_mode": "score", "max_speed_m_s": 4, "full_ground_hold": False},
    )
    (touch,) = out["touches"]
    assert touch["up_ms"] >= 1750


def test_a_continuous_touch_cannot_claim_two_colours_without_grace(tmp_path):
    f = chart(
        tmp_path,
        "hold(1000,3000,4);arc(1000,3000,0,0,s,.5,.5,0,none,false);arc(1500,2500,.35,.35,s,.5,.5,1,none,false);",
    )
    out = create_plan(f, 0, 3500, {"arc_path_mode": "score", "max_speed_m_s": 4})
    for touch in out["touches"]:
        colours = {p["colour"] for p in touch["parts"] if p["kind"] == "chain"}
        assert len(colours) <= 1


def test_trace_preference_tracks_the_geometry_and_tail_without_changing_scoring(
    tmp_path,
):
    f = chart(tmp_path, "arc(1000,3000,.3,.7,s,.5,.5,0,none,false);")
    settings = {
        "arc_path_mode": "score",
        "max_speed_m_s": 1.6,
        "prefer_arc_trace": True,
        "arc_trace_weight": 1000,
    }
    traced = create_plan(f, 0, 3500, settings)
    cheap = create_plan(f, 0, 3500, {**settings, "arc_trace_weight": 0})
    low = create_plan(f, 0, 3500, {**settings, "arc_trace_weight": 0.001})
    assert (
        traced["planning"]["skipped_ticks"] == cheap["planning"]["skipped_ticks"] == 0
    )
    assert traced["touches"][0]["up_ms"] > 3000 > cheap["touches"][0]["up_ms"]
    assert len({tuple(xy) for xy in traced["touches"][0]["path_mm"]}) > 50
    assert len({tuple(xy) for xy in cheap["touches"][0]["path_mm"]}) == 1
    assert len({tuple(xy) for xy in low["touches"][0]["path_mm"]}) == 1
    assert traced["cost"]["components"]["arc_trace_deviation_s"] < 1e-9
    assert traced["planner_options"]["arc_trace_weight"] == 1000


def test_trace_preference_can_still_shortcut_an_impossible_zigzag(tmp_path):
    f = chart(
        tmp_path,
        "".join(
            f"arc({1000+k*120},{1120+k*120},{k%2},{1-k%2},s,.5,.5,0,none,false);"
            for k in range(12)
        ),
    )
    output = create_plan(
        f,
        0,
        3000,
        {
            "arc_path_mode": "score",
            "max_speed_m_s": 0.1,
            "allow_misses": False,
            "prefer_arc_trace": True,
            "arc_trace_weight": 10000,
        },
    )
    assert output["planning"]["skipped_ticks"] == 0
    assert output["cost"]["components"]["arc_trace_deviation_s"] > 0


def test_split_tick_trace_cost_covers_each_visual_frame_once(tmp_path):
    from arc_hand_order.planning.arc_scoring import trace_cells

    f = chart(tmp_path, "arc(1000,3000,.3,.7,s,.5,.5,0,none,false);")
    occupations = native_occupations(occupations_from_chart(api.load_chart(f).chart))
    cells = sorted(trace_cells(split_arc_ticks(occupations).items, 0).values())
    assert cells[0][0] == 60 and cells[-1][1] == 181
    assert all(a[1] == b[0] for a, b in zip(cells, cells[1:]))


def test_trace_preference_also_follows_geometry_between_individual_tick_jobs(tmp_path):
    f = chart(tmp_path, "arc(1000,2000,.3,.7,s,.5,.5,0,none,false);")
    output = create_plan(
        f,
        0,
        2500,
        {
            "arc_path_mode": "score",
            "max_speed_m_s": 1.6,
            "prefer_arc_trace": True,
            "arc_trace_weight": 1000,
        },
        excluded=("force-tick-search",),
    )
    assert output["planning"]["skipped_ticks"] == 0
    assert output["cost"]["components"]["arc_trace_deviation_s"] < 1e-9
    assert len(output["touches"]) == 1
    assert output["touches"][0]["up_ms"] > 2000
