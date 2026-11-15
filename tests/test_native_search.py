"""Differential tests require a real compiled engine; fallback never satisfies them."""
from copy import deepcopy
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
pytest.importorskip('arc_hand_order_native', reason='Build native-search with maturin to exercise the native engine')
from plan_excerpt import create_plan


def chart(tmp_path, body, offset=0):
    path = tmp_path / 'native-differential.aff'
    path.write_text(f'AudioOffset:{offset}\n-\ntiming(0,120,4);\n' + body.replace(';', ';\n'))
    return path


BASE = {'arc_path_mode': 'score', 'search_width': 256, 'max_speed_m_s': 4,
        'allow_misses': True}


def native_plan(path, settings, *, workers=1, end=3500, **kwargs):
    events = []
    result = create_plan(path, 0, end, settings, search_backend='native', search_workers=workers,
                         progress=events.append, **kwargs)
    evidence = [event for event in events if 'search_backend' in event]
    assert evidence and all(event['search_backend'] == 'native' for event in evidence), evidence
    assert all(event['search_workers'] == workers for event in evidence)
    return result


def comparable(plan):
    value = deepcopy(plan)
    value['stats'].pop('elapsed_s', None)
    if 'contact_placement' in value:
        value['contact_placement'].pop('elapsed_s', None)
    return value


def assert_equivalent(reference, actual):
    assert actual['cost']['components']['missed_ticks'] <= reference['cost']['components']['missed_ticks']
    assert actual['cost']['components']['missed_red_notes'] <= reference['cost']['components']['missed_red_notes']
    assert actual['cost']['weighted'] <= reference['cost']['weighted'] + max(1e-9, abs(reference['cost']['weighted']) * 1e-12)
    # Synthetic cases are small enough that both engines should retain the same optimum.
    for key in ('touches', 'misses', 'arc_tick_targets'):
        assert actual.get(key) == reference.get(key), key
    assert actual['cost']['components'] == pytest.approx(reference['cost']['components'], rel=1e-10, abs=1e-9)


BODIES = {
    'taps': ''.join(f'({500 + i * 160},{i % 4 + 1});' for i in range(14)),
    'hold_and_taps': 'hold(500,2100,1);(750,4);(1050,3);(1350,4);(2400,2);',
    'crossing_arcs': 'arc(500,2200,.1,.9,s,.5,.5,0,none,false);arc(500,2200,.9,.1,s,.6,.6,1,none,false);(2450,1);',
    'forced_skip': '(700,1);(700,2);(700,3);(700,4);(1500,2);',
    'colour_reacquisition': 'arc(500,1100,.2,.2,s,.6,.6,0,none,false);arc(1150,2000,.8,.8,s,.6,.6,0,none,false);(2200,2);',
}

SEGMENTED_SNAKE = (
    'arc(500,1100,.1,.9,s,.5,.5,0,none,false);'
    'arc(1100,1800,.9,.2,s,.5,.7,0,none,false);'
    'arc(1800,2500,.2,.8,s,.7,.5,0,none,false);'
    'arc(600,2450,.7,.3,s,.6,.6,1,none,false);'
    '(950,1);(1450,4);(2200,2);'
)


def segmented_snake_exclusion(path):
    from arc_hand_order import api
    # A real requested exclusion exercises the existing split-tick retry,
    # independently of whether the initial whole-snake search is feasible.
    return (next(event.event_id for event in api.load_chart(path).chart.events
                 if event.type == 'tap'),)


def test_native_preparation_preserves_raw_source_segments_and_cold_parent_cache(tmp_path, monkeypatch):
    from arc_hand_order.planning import native_search
    from arc_hand_order.planning.two_finger import frame_ms, target_xy
    original = native_search._prepared
    checked = []

    def inspect(*args, **kwargs):
        prepared = original(*args, **kwargs)
        data, items, _, _, graph = prepared
        raw_by_key = {}
        parents = {item.parent_occupation_id for item in items if item.parent_occupation_id}
        if not parents:
            return prepared
        # Preparing arrays must not choose the first parent/frame source. A
        # discarded expansion can be the first actual caller in the search.
        assert not any(key[0] in parents for key in graph.points)
        for item, record in zip(items, data['items']):
            if not item.parent_occupation_id:
                continue
            for frame, xy in enumerate(record['points'], record['points_start']):
                assert xy == target_xy(item, frame_ms(frame, graph.offset), graph.projection)
                raw_by_key.setdefault((item.parent_occupation_id, frame), set()).add(tuple(xy))
        # The fixture would have caught eager parent-key memoization: two
        # source segments request different targets for the same cache key.
        assert any(len(points) > 1 for points in raw_by_key.values())
        checked.append(True)
        return prepared

    monkeypatch.setattr(native_search, '_prepared', inspect)
    path = chart(tmp_path, SEGMENTED_SNAKE)
    native_plan(path, BASE, workers=4, excluded=segmented_snake_exclusion(path))
    assert checked


def test_point_press_can_serve_both_overlapping_snake_ticks(tmp_path):
    from arc_hand_order import api
    from arc_hand_order.planning.arc_scoring import native_occupations
    from arc_hand_order.planning.occupations import occupations_from_chart
    from plan_excerpt import split_arc_ticks

    path = chart(tmp_path, 'timing(0,172,4);'
                 'arc(1000,1087,.42,.42,s,0,0,0,none,false);'
                 'arc(1000,1087,.58,.58,s,0,0,1,none,false);'
                 'arc(1033,1034,.5,.5,s,0,0,0,none,true)[arctap(1033)];')
    loaded = api.load_chart(path)
    items = split_arc_ticks(native_occupations(occupations_from_chart(loaded.chart),
                                             chart_events=loaded.chart.events))
    multi = dict(second_finger=False, second_finger_again=False, meeting_swaps=False,
                 covered_holds=False, covered_arcs=False, steady_arcs=False, enveloped_arcs=False,
                 tick_contacts=True, share_weight=0, arm_crossing_travel=(.012, .7, 100))
    planner = dict(scoring_arcs=True, point_contact_frames=1, point_window_ms=0,
                   contact_offset_fractions=(.6, .3), colour_weight=0, repeat_weight=100)

    def plan(backend):
        return api.plan_to_dict(api.plan_chart(loaded, multi=multi, planner=planner,
                                             occupation_set=items, search_backend=backend))

    reference = plan('python')
    actual = plan('native')
    assert_equivalent(reference, actual)
    assert len(actual['touches']) == 1
    touch = actual['touches'][0]
    assert touch['down_frame'] == 62
    assert [part['kind'] for part in touch['parts']] == ['sky', 'chain', 'chain']


@pytest.mark.parametrize('backend', ['python', 'native'])
@pytest.mark.parametrize('second_ms, expected_taps', [(1117, 1), (1167, 2)])
def test_shared_sky_press_counts_toward_minimum_tap_interval(tmp_path, backend, second_ms, expected_taps):
    from arc_hand_order import api
    from arc_hand_order.planning.arc_scoring import native_occupations
    from arc_hand_order.planning.occupations import occupations_from_chart
    from plan_excerpt import split_arc_ticks

    path = chart(tmp_path, 'timing(0,172,4);'
                 'arc(1000,1087,.42,.42,s,0,0,0,none,false);'
                 'arc(1000,1087,.58,.58,s,0,0,1,none,false);'
                 f'arc(1033,{second_ms},.5,.5,s,0,0,0,none,true)[arctap(1033),arctap({second_ms})];')
    loaded = api.load_chart(path)
    items = split_arc_ticks(native_occupations(occupations_from_chart(loaded.chart),
                                             chart_events=loaded.chart.events))
    multi = dict(second_finger=False, second_finger_again=False, meeting_swaps=False,
                 covered_holds=False, covered_arcs=False, steady_arcs=False, enveloped_arcs=False,
                 tick_contacts=True, share_weight=0, allow_misses=True)
    planner = dict(scoring_arcs=True, point_contact_frames=1, point_window_ms=0,
                   contact_offset_fractions=(.6, .3), colour_weight=0, repeat_weight=100,
                   fixed_hands=((1033, 'left'), (second_ms, 'left')))

    def plan(interval):
        result = api.plan_chart(loaded, multi={**multi, 'tap_interval_ms': interval}, planner=planner,
                                occupation_set=items, search_backend=backend)
        return [[p['kind'] for p in t['parts']] for t in api.plan_to_dict(result)['touches']]

    unlocked = plan(0)
    assert sum('sky' in t for t in unlocked) == 2
    assert any('sky' in t and 'chain' in t for t in unlocked)
    assert sum('sky' in t for t in plan(130)) == expected_taps


def test_dense_sky_candidates_follow_snakes_and_reserve_new_presses_for_taps(tmp_path):
    from arc_hand_order import api
    from arc_hand_order.planning.arc_scoring import native_occupations
    from arc_hand_order.planning.occupations import occupations_from_chart
    from arc_hand_order.planning.projection import GameplayProjection
    from arc_hand_order.planning.two_finger import PlannerOptions, build_candidates
    from plan_excerpt import point_windows, split_arc_ticks

    path = chart(tmp_path, 'timing(0,172,4);'
                 'arc(1000,1290,.42,.42,s,0,.08,0,none,false);'
                 'arc(1000,1290,.58,.58,s,0,.08,1,none,false);'
                 'arc(1000,1262,.5,.5,s,0,.08,0,none,true)'
                 '[arctap(1000),arctap(1087),arctap(1174),arctap(1262)];')
    loaded = api.load_chart(path)
    items = native_occupations(occupations_from_chart(loaded.chart), chart_events=loaded.chart.events)
    items = point_windows(split_arc_ticks(items), 2000, 25)
    projection = GameplayProjection(api.load_device())
    options = PlannerOptions(scoring_arcs=True, point_contact_frames=1, min_air_frames=1,
                             full_point_window=True, contact_offset_fractions=(.6, .3),
                             protect_snake_presses=True)
    candidates = [c for group in build_candidates(items, projection, options) for c in group]
    held = [c for c in candidates if c.occupation.kind == 'sky' and c.up_frame - c.down_frame > 2]
    assert any(c.first_xy[0] < 0 and c.first_xy != c.last_xy for c in held)
    assert any(c.first_xy[0] > 0 and c.first_xy != c.last_xy for c in held)
    snake_presses = [c for c in candidates if c.occupation.kind == 'chain' and 1067 <= c.down_ms <= 1167]
    assert snake_presses and all(c.share_only for c in snake_presses)


def test_native_segmented_snake_matches_an_independently_prepared_python_graph(tmp_path):
    path = chart(tmp_path, SEGMENTED_SNAKE)
    settings = {**BASE, 'prefer_arc_trace': True, 'arc_trace_weight': 100}
    excluded = segmented_snake_exclusion(path)
    # This reference must be built before native preparation on a separate
    # graph. Replaying both engines on a prewarmed graph hides source aliasing.
    reference = create_plan(path, 0, 3500, settings, excluded=excluded, search_backend='python')
    expected = comparable(reference)
    expected_cost = expected.pop('cost')
    native_reference = None
    for workers in (1, 4):
        actual = comparable(native_plan(path, settings, workers=workers, excluded=excluded))
        cost = actual.pop('cost')
        # Equivalent curve lengths can differ by one floating-point step
        # between engines. Worker count must still give the same native cost.
        assert cost['components'] == pytest.approx(expected_cost['components'], rel=1e-10, abs=1e-9)
        assert cost['weighted'] == pytest.approx(expected_cost['weighted'], rel=1e-10, abs=1e-9)
        assert actual == expected
        if native_reference is None:
            native_reference = cost
        else:
            assert cost == native_reference


def test_selected_route_validation_restores_all_branch_point_choices(tmp_path, monkeypatch):
    from arc_hand_order.planning import native_search
    original = native_search._validate
    checked = []

    def validate(result, graph, *args):
        expected = {(parent, frame): tuple(xy) for parent, frame, xy in result['point_cache']}
        # The target branch honours candidate contact paths, so this chart
        # need not consult the parent/frame cache. Supply an unused entry to
        # exercise snapshot hydration independently of that implementation detail.
        if not expected:
            result['point_cache'] = [['validation-fixture', 0, [0., 0.]]]
            expected = {('validation-fixture', 0): (0., 0.)}
        first_steps = graph.steps

        def inspect_steps(*step_args, **step_kwargs):
            # Geometry chosen by rejected branches must already be present
            # before the first chosen transition is reconstructed.
            assert all(graph.points.get(key) == xy for key, xy in expected.items())
            checked.append(True)
            return first_steps(*step_args, **step_kwargs)

        graph.steps = inspect_steps
        return original(result, graph, *args)

    monkeypatch.setattr(native_search, '_validate', validate)
    path = chart(tmp_path, SEGMENTED_SNAKE)
    native_plan(path, BASE, workers=4, excluded=segmented_snake_exclusion(path))
    assert checked


@pytest.mark.parametrize('name', list(BODIES))
def test_native_matches_python_and_is_deterministic_across_workers(tmp_path, name):
    path = chart(tmp_path, BODIES[name])
    reference = create_plan(path, 0, 3500, BASE, search_backend='python')
    first = None
    for workers in (1, 2, 4, 8):
        actual = native_plan(path, BASE, workers=workers)
        assert_equivalent(reference, actual)
        if first is None:
            first = comparable(actual)
        assert comparable(actual) == first


@pytest.mark.parametrize('feature', [
    {'prefer_arc_trace': True, 'arc_trace_weight': 100},
    {'tap_interval_ms': 150},
    {'contact_offset_x_percent': 0, 'contact_offset_y_percent': 0},
    {'contact_offset_x_percent': 40, 'contact_offset_y_percent': 20},
    {'full_arc_hold': True},
])
def test_native_optional_costs_preserve_objective(tmp_path, feature):
    path = chart(tmp_path, BODIES['crossing_arcs'])
    settings = {**BASE, **feature}
    reference = create_plan(path, 0, 3500, settings, search_backend='python')
    actual = native_plan(path, settings, workers=2)
    assert_equivalent(reference, actual)


def test_native_early_prefix_scoring_and_negative_audio_offset(tmp_path):
    body = ''.join(f'arc({500+k*120},{620+k*120},{k%2},{1-k%2},s,.5,.5,0,none,false);' for k in range(12))
    path = chart(tmp_path, body, offset=-100)
    settings = {**BASE, 'max_speed_m_s': .1}
    reference = create_plan(path, 0, 3500, settings, search_backend='python')
    actual = native_plan(path, settings, workers=4)
    assert_equivalent(reference, actual)


def test_native_trace_increments_preserve_prefix_arithmetic(tmp_path, monkeypatch):
    from arc_hand_order.planning import native_search
    original = native_search._validate
    checked = []

    def validate(result, *args):
        validated = original(result, *args)
        for native, (_, reference) in zip(result['steps'], validated[2]):
            # A final total can hide cancelling roundoff errors that change
            # intermediate beam ties. Check the raw worker increments.
            assert native['increment'][6] == reference.base.distance_m
            assert native['increment'][33] == reference.arc_trace_deviation_s
            assert native['increment'][35] == 0
            checked.append(native['extension'])
        return validated

    monkeypatch.setattr(native_search, '_validate', validate)
    path = chart(tmp_path, 'arc(500,2800,.1,.9,s,.5,.7,0,none,false);'
                 'arc(500,2800,.9,.1,s,.7,.5,1,none,false);(1700,2);')
    native_plan(path, {**BASE, 'prefer_arc_trace': True, 'arc_trace_weight': 100,
                       'tap_interval_ms': 130}, workers=4)
    assert checked and any(checked)


def test_native_forced_misses_keep_requested_exclusion(tmp_path):
    path = chart(tmp_path, BODIES['taps'])
    original = create_plan(path, 0, 3500, BASE, search_backend='python')
    excluded = (original['notes'][0]['event_id'],)
    reference = create_plan(path, 0, 3500, BASE, excluded=excluded, search_backend='python')
    actual = native_plan(path, BASE, workers=2, excluded=excluded)
    assert_equivalent(reference, actual)
    assert any(excluded[0] in miss['events'] for miss in actual['misses'])


def test_native_contact_exclusion_preserves_colour_release_lock(tmp_path):
    from arc_hand_order.api import load_chart
    from arc_hand_order.planning.occupations import occupations_from_chart
    path = chart(tmp_path, 'arc(500,3000,.5,.5,s,.5,.5,0,none,false);')
    occupation, = occupations_from_chart(load_chart(path).chart).items
    avoid = [{'parent': occupation.occupation_id, 'start_ms': 1200, 'end_ms': 1400}]
    reference = create_plan(path, 0, 3500, BASE, avoid_contacts=avoid, search_backend='python')
    actual = native_plan(path, BASE, workers=4, avoid_contacts=avoid)
    assert_equivalent(reference, actual)
    assert all(touch['up_ms'] < 1200 or touch['down_ms'] > 1400 for touch in actual['touches'])


def test_native_red_note_priority_and_cancellation(tmp_path):
    path = chart(tmp_path, '(1000,1);(1000,4);arc(0,3000,.5,.5,s,1,1,0,none,designant)[arctap(1000)];')
    settings = {**BASE, 'target_all_red_notes': True, 'allow_misses': False, 'point_contact_frames': 6}
    reference = create_plan(path, 0, 3500, settings, search_backend='python')
    actual = native_plan(path, settings, workers=2)
    assert_equivalent(reference, actual)
    assert actual['planning']['skipped_ticks'] == 0
    assert actual['planning']['skipped_red_notes'] == 1
    cache = tmp_path / 'cancelled-cache'
    def cancel(event):
        if event.get('phase') == 'search':
            raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        create_plan(path, 0, 3500, settings, search_backend='native', progress=cancel, cache_dir=cache)
    assert not list(cache.glob('*.json'))


def test_trace_mode_falls_back_explicitly(tmp_path):
    path = chart(tmp_path, '(500,1);(1000,4);')
    events = []
    create_plan(path, 0, 1500, {**BASE, 'arc_path_mode': 'trace'},
                search_backend='native', progress=events.append)
    backend = [event for event in events if event.get('phase') == 'backend']
    assert backend and all(event['search_backend'] == 'python' for event in backend)
    assert all(event.get('fallback_reason') for event in backend)


@pytest.mark.parametrize('corruption', ['short', 'long', 'order', 'forced_hit', 'miss', 'hand'])
def test_selected_route_validator_rejects_invalid_structure(corruption):
    from arc_hand_order.planning.native_search import _validate
    multi = SimpleNamespace(jack_weight=0, arc_takeover_weight=0, tick_contacts=True,
                            allow_misses=True, forced_misses=())
    graph = SimpleNamespace(multi=multi, initial_state=lambda: (-1,) * 8)
    item = SimpleNamespace(first_tick=SimpleNamespace(time_ms=100), occupation_id='target',
                           source_event_ids=('event',), is_red=False, score_weight=1, start_ms=100)
    step = {'item': 0, 'touch': None, 'hand': 0}
    steps = [step]
    if corruption == 'short':
        steps = []
    elif corruption == 'long':
        steps = [step, step]
    elif corruption == 'order':
        step['item'] = 1
    elif corruption == 'forced_hit':
        multi.forced_misses = ('event',)
        step['touch'] = {}
    elif corruption == 'miss':
        multi.allow_misses = False
    else:
        step.update(touch={}, hand=4)
    with pytest.raises(RuntimeError):
        _validate({'steps': steps}, graph, [item], [], lambda *args, **kwargs: None)


@pytest.mark.parametrize('point', [[float('nan'), 0], [float('inf'), 0], [0], [0, 0, 0]])
def test_selected_route_matching_rejects_nonfinite_or_malformed_points(point):
    from arc_hand_order.planning.native_search import _matches
    candidate = SimpleNamespace(id=7)
    touch = SimpleNamespace(down=0, path=((0., 0.),), bridges=(),
                            parts=(SimpleNamespace(candidate=candidate, start=0, stop=1),))
    record = {'down': 0, 'path': [point], 'bridges': [], 'arrival': None,
              'parts': [{'candidate': 0, 'start': 0, 'stop': 1}]}
    assert not _matches(touch, record, [candidate])

def test_native_checkpoint_retry_preserves_the_perfect_prefix(tmp_path):
    body = ('(1000,1);arc(1800,2600,.5,.5,s,1,1,0,none,false);'
            '(3200,1);(4200,2);arc(4600,5400,.5,.5,s,1,1,1,none,false);'
            '(5200,3);(6000,1);(6017,4);(6033,1);(6050,4);(6700,4);')
    path=chart(tmp_path,body)
    settings={**BASE,'tap_interval_ms':150}
    reference=create_plan(path,0,7200,settings,search_backend='python')
    first=None
    for workers in (1,4):
        events=[]
        actual=create_plan(path,0,7200,settings,search_backend='native',search_workers=workers,progress=events.append)
        assert any(e.get('resumed_from') for e in events)
        assert_equivalent(reference,actual)
        if first is not None: assert comparable(actual)==first
        first=comparable(actual)


def test_center_cost_preserves_room_for_close_repeats(tmp_path):
    from arc_hand_order.api import GameplayProjection, load_device

    rows = [(500, 1), (900, 4), (1300, 2), (1700, 3), (2100, 1), (2500, 4),
            *((3100 + 83 * i, 2) for i in range(6)),
            (4200, 3), (4600, 1), (5000, 4), (5400, 1), (5800, 3)]
    path = chart(tmp_path, ''.join(f'({time},{lane});' for time, lane in rows))
    settings = {**BASE, 'note_center_weight': 1.5, 'note_center_free_mm': 3,
                'contact_offset_x_percent': 60, 'contact_offset_y_percent': 30,
                'tap_interval_ms': 150, 'point_contact_frames': 1,
                'crossing_weight': 0.5, 'repeat_weight': 3,
                'repeat_comfort_ms': 180, 'full_ground_hold': True}
    plan = native_plan(path, settings, end=6400, workers=3)
    assert not plan['misses']
    assert not plan['planning']['planned_far_taps']
    assert plan['cost']['components']['note_center_deviation'] > 0
    projection = GameplayProjection(load_device())
    touches = {t['id']: t for t in plan['touches']}
    for note, (time, lane) in zip(plan['notes'], rows, strict=True):
        centre_x = projection.lane_center_to_device(lane)[0] * 1000
        x = touches[note['touches'][0]]['path_mm'][0][0]
        if 3100 <= time <= 3515:
            assert abs(x - centre_x) >= 11
        else:
            assert abs(x - centre_x) <= 3


def test_native_empty_window_has_a_finished_arm_route(tmp_path):
    path=chart(tmp_path,'(5000,1);')
    reference=create_plan(path,0,1500,BASE,search_backend='python')
    actual=native_plan(path,BASE,end=1500)
    assert comparable(actual)==comparable(reference)


@pytest.mark.parametrize('workers', [1, 4])
def test_native_multifinger_staggered_chord_matches_reference(tmp_path, workers):
    path = chart(tmp_path,
                 'hold(800,2100,2);hold(800,2100,3);'
                 'arc(1100,2400,0,.25,si,1,1,0,none,false);'
                 'arc(1450,2400,1,.75,si,1,1,1,none,false);(2650,2);')
    settings = {**BASE, 'multifinger': True, 'tap_interval_ms': 130}
    reference = create_plan(path, 0, 3500, settings, search_backend='python')
    actual = native_plan(path, settings, workers=workers)
    assert_equivalent(reference, actual)
    assert {touch['hand'] for touch in actual['touches'] if touch['finger'] == 2} == {'left', 'right'}
    assert not actual['misses']
