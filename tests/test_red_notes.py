from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from arc_hand_order.api import load_chart
from arc_hand_order.planning.occupations import occupations_from_chart
from dashboard.red_notes import render_chart, prepare_project, MARKER
from plan_excerpt import create_plan


def chart(tmp_path, body='(1000,1);\n(2200,4);', red_time=1600):
    p = tmp_path / '0.aff'
    p.write_text(f'AudioOffset:0\n-\ntiming(0,120,4);\n{body}\n'
                 f'arc(0,3000,0.5,0.5,s,1,1,0,none,designant)[arctap({red_time})];\n')
    return p


def test_designant_is_a_trace_with_zero_weight_sky_taps(tmp_path):
    loaded = load_chart(chart(tmp_path))
    arc, = (e for e in loaded.chart.events if e.type == 'arc')
    assert arc.is_designant and arc.is_guideline
    occupations = occupations_from_chart(loaded.chart)
    red, = (o for o in occupations.items if o.is_red)
    assert red.kind == 'sky' and red.score_weight == 0
    assert sum(o.score_weight for o in occupations.items) == 2
    assert not any(o.kind == 'chain' for o in occupations.items)


@pytest.mark.parametrize('mode', ['score', 'trace'])
def test_red_visibility_and_targeting_are_independent(tmp_path, mode):
    p = chart(tmp_path)
    settings = {'arc_path_mode': mode, 'max_speed_m_s': 4, 'enable_red_notes': True,
                'target_all_red_notes': True, 'allow_misses': False}
    targeted = create_plan(p, 0, 3000, settings)
    assert len(targeted['touches']) == 3
    assert targeted['planning']['planned_red_notes'] == 1
    assert targeted['planning']['skipped_ticks'] == 0
    ignored = create_plan(p, 0, 3000, {**settings, 'target_all_red_notes': False})
    hidden = create_plan(p, 0, 3000, {**settings, 'enable_red_notes': False})
    assert len(ignored['touches']) == len(hidden['touches']) == 2
    assert ignored['planning']['planned_red_notes'] == hidden['planning']['planned_red_notes'] == 0


def test_infeasible_red_note_does_not_displace_scoring_notes_or_fail_strict_score(tmp_path):
    p = chart(tmp_path, '(1000,1);\n(1000,4);', red_time=1000)
    plan = create_plan(p, 0, 2000, {'arc_path_mode': 'score', 'max_speed_m_s': 4,
                       'target_all_red_notes': True, 'allow_misses': False,
                       'point_contact_frames': 6})
    assert plan['planning']['skipped_ticks'] == 0
    assert plan['planning']['skipped_red_notes'] == 1
    assert len(plan['touches']) == 2
    assert plan['misses'][0]['kind'] == 'red_sky'
    assert plan['misses'][0]['scoring_ticks'] == 0


def test_render_translation_preserves_original_and_normal_notes(tmp_path):
    p = chart(tmp_path)
    original = p.read_text()
    on, count = render_chart(original, True)
    off, count_off = render_chart(original, False)
    assert count == count_off == 1
    assert p.read_text() == original

    assert f',0,{MARKER},true)[arctap(1600)]' in on
    assert '(1000,1);' in off and 'arctap' not in off
    assert off.count('\n') == original.count('\n')
    project = tmp_path/'project.arcproj'
    project.write_text('lastOpenedChartPath: 0.aff\ncharts:\n- chartPath: 0.aff\n  audioPath: base.ogg\n')
    (tmp_path/'base.ogg').write_bytes(b'fixture')
    run = tmp_path/'run'; run.mkdir()
    directory, aff, arcproj = prepare_project(tmp_path, p, project, run, True)
    assert aff.read_text() == on
    assert (directory/'base.ogg').read_bytes() == b'fixture'
    assert arcproj.read_text().count('lastOpenedChartPath') == 1
    assert p.read_text() == original


def test_negative_source_offset_keeps_early_excerpt_contacts_cacheable(tmp_path):
    p = tmp_path/'negative.aff'
    p.write_text('AudioOffset:-1297\n-\ntiming(0,120,4);\n(4250,1);\n(4500,4);\n')
    plan = create_plan(p, 4000, 5000, {'arc_path_mode':'score', 'max_speed_m_s':4}, cache_dir=tmp_path/'cache')
    assert len(plan['touches']) == 2
    assert all(0 <= t['down_frame'] < t['up_frame'] for t in plan['touches'])
    assert plan['chart']['audio_offset_ms'] == 0
    assert plan['chart']['source_audio_offset_ms'] == -1297
    assert plan['preview']['audio_start_ms'] == 2703
