import json
from types import SimpleNamespace

from arc_hand_order import api
from arc_hand_order.__main__ import main

CHART = ("AudioOffset:0\n-\ntiming(0,120,4);(1000,1);(1250,4);hold(1500,2200,2);"
         "arc(1600,2400,0.75,1.00,s,1.00,0.50,1,none,false);(2600,3);"
         "arc(2700,3000,0.25,0.00,s,0.00,0.50,0,none,false);"
         "arc(3200,3600,0.00,1.00,s,0.50,0.50,0,none,true)[arctap(3400)];\n")


def plan_of(tmp_path, *args):
    chart = tmp_path / "small.aff"
    chart.write_text(CHART, encoding="utf-8", newline="\n")
    out = tmp_path / "plan.json"
    assert main(["plan", str(chart), "--out", str(out), *args]) == 0
    return json.loads(out.read_text(encoding="utf-8"))


def test_a_plan_has_every_note_on_a_hand_and_a_point_per_frame(tmp_path):
    data = plan_of(tmp_path)
    assert data["format"] == api.FORMAT and data["preset"] == "on5"
    assert data["stats"]["pruned_items"] == 0
    assert {n["type"] for n in data["notes"]} == {"tap", "hold", "arc", "arctap"}
    assert all(n["hands"] and set(n["hands"]) <= {"left", "right", "covered"} for n in data["notes"])
    ids = [t["id"] for t in data["touches"]]
    assert ids == list(range(1, len(ids) + 1))
    for t in data["touches"]:
        assert len(t["path_mm"]) == t["up_frame"] - t["down_frame"] and t["finger"] in (1, 2)
        assert all(t["down_frame"] <= p["from_frame"] < p["to_frame"] <= t["up_frame"] for p in t["parts"])
    touched = {e for t in data["touches"] for p in t["parts"] for e in p["served_events"]}
    assert touched | {e for c in data["covered"] for e in c["events"]} == {n["event_id"] for n in data["notes"]}


def test_presets_and_overrides_are_written_with_the_plan(tmp_path):
    data = plan_of(tmp_path, "--preset", "on4", "--set", "jack_weight=0", "--planner-set", "offsets_ms=[0]")
    assert data["preset"] == "on4"
    assert data["multi_finger_options"]["jack_weight"] == 0.0 and not data["multi_finger_options"]["jump_slides"]
    assert data["planner_options"]["offsets_ms"] == [0]


def test_options_prints_a_preset(capsys):
    assert main(["options", "--preset", "on5"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["multi_finger_options"]["side_by_side_weight"] == 5.0
    assert data["planner_options"]["offsets_ms"] == list(range(-25, 26))


def test_an_unknown_option_is_an_error(tmp_path, capsys):
    chart = tmp_path / "small.aff"
    chart.write_text(CHART, encoding="utf-8", newline="\n")
    assert main(["plan", str(chart), "--set", "no_such_option=1"]) == 1
    assert "no_such_option" in capsys.readouterr().err


def test_a_switched_chain_serves_each_chain_on_its_side_of_the_switch():
    events = {"a1": SimpleNamespace(start_time_ms=1000, end_time_ms=1500),
              "a2": SimpleNamespace(start_time_ms=1500, end_time_ms=2000),
              "b1": SimpleNamespace(start_time_ms=1000, end_time_ms=1500),
              "b2": SimpleNamespace(start_time_ms=1500, end_time_ms=2000)}
    part = {"kind": "chain", "events": ["a1", "a2", "b1", "b2"], "to_ms": 2017,
            "switch": {"at_ms": 1500, "before": {"events": ["a1", "a2"]}, "after": {"events": ["b1", "b2"]}}}
    assert api._served(part, events) == ["a1", "b2"]  # b1 ends at the switch: the other finger's alone
    part["switch"]["at_ms"] = 1400
    assert api._served(part, events) == ["a1", "b1", "b2"]  # b1 runs on past the switch: both fingers'
    # The finger that lets its chain go at a relay keeps only what began before it let go.
    assert api._served({"kind": "chain", "events": ["b1", "b2"], "to_ms": 1500}, events) == ["b1"]
