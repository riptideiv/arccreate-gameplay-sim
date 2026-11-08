from pathlib import Path
import sys
from types import SimpleNamespace as N
from dataclasses import replace
import numpy as np
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from arc_hand_order.planning.note_center import NoteCenterPreference
from arc_hand_order.planning.multi_finger import MultiCost,MultiFingerOptions
from arc_hand_order.planning.two_finger import PlannerOptions


def touch(kind,frames=2,offset=.015,parts=1):
    item=N(kind=kind,occupation_id='note',start_ms=0)
    part=N(candidate=N(occupation=item),start=0,stop=frames)
    return N(id=frames*10+parts,parts=(part,)*parts,down=0,up=frames,first_xy=(offset,0),xy=lambda f:(offset,0))


def preference():
    p=NoteCenterPreference(None,.005);p.centres['note']=(0,0);return p


def test_free_radius_and_quadratic_growth():
    p=preference()
    assert p.penalty((.003,.004),(0,0))==0
    assert p.penalty((.015,0),(0,0))==pytest.approx(.25)
    assert p.penalty((.025,0),(0,0))==pytest.approx(1)


def test_point_cost_is_once_per_note_not_per_frame_or_duplicate_part():
    for kind in ('tap','sky'):
        for frames,parts in ((2,1),(60,1),(60,2)):
            assert preference().measure(touch(kind,frames,parts=parts))==pytest.approx(.25)


def test_hold_integrates_time_independent_of_tick_density():
    assert preference().measure(touch('hold',60))==pytest.approx(.25)
    assert preference().measure(touch('hold',120,parts=3))==pytest.approx(.5)
    t=touch('hold',120);part=t.parts[0]
    t.parts=(N(candidate=part.candidate,start=0,stop=60),N(candidate=part.candidate,start=60,stop=120))
    assert preference().metrics([N(touch=t)])['ground_hold_frames']['samples']==120


def test_arcs_are_never_charged_twice():
    assert preference().measure(touch('chain',120,offset=.1,parts=10))==0


def test_weights_add_and_validate():
    c=MultiCost(note_center_deviation=2)
    assert c.weighted(PlannerOptions(note_center_weight=3),MultiFingerOptions())==6
    assert (c+c).note_center_deviation==4
    for name in ('note_center_weight','note_center_free_mm'):
        for value in (-1,float('inf'),float('nan')):
            with pytest.raises(ValueError):PlannerOptions(**{name:value})


def test_preference_changes_actual_discrete_contact_selection(tmp_path):
    from arc_hand_order import api
    from arc_hand_order.planning.two_finger import target_xy
    from arc_hand_order.planning.projection import GameplayProjection
    f=tmp_path/'notes.aff';times=(1000,1250,1500,1750)
    f.write_text('AudioOffset:0\n-\n'+''.join(f'({t},{lane});\n' for t,lane in zip(times,(1,3,1,3))))
    def plan(weight):return api.plan_chart(api.load_chart(f),planner={'off_center_contacts':True,'scoring_arcs':True,
        'fixed_hands':tuple((t,'left') for t in times),'note_center_weight':weight},
        multi={'second_finger':False,'second_finger_again':False,'tick_contacts':True,
            'meeting_swaps':False,'covered_holds':False,'covered_arcs':False,'steady_arcs':False,
            'enveloped_arcs':False,'max_states':64})
    old,new=plan(0),plan(20);projection=GameplayProjection(new.device)
    def deviations(result):return [np.linalg.norm(np.array(t.touch.first_xy)-target_xy(t.touch.parts[0].candidate.occupation,t.touch.down_ms,projection)) for t in result.plan.touches]
    assert np.mean(deviations(new)) < np.mean(deviations(old))*.2
    assert len(new.plan.touches)==len(times) and not new.plan.skipped


def test_refinement_keeps_necessary_offsets_but_reduces_excess(tmp_path):
    import runpy
    from contact_placement import refine
    from arc_hand_order.planning.projection import GameplayProjection
    fixture=runpy.run_path(str(Path(__file__).with_name('test_contact_placement.py')))['alternating']
    r=fixture(tmp_path);old,_=refine(r)
    r=replace(r,plan=replace(r.plan,options=replace(r.plan.options,note_center_weight=20)))
    new,_=refine(r);p=NoteCenterPreference(GameplayProjection(r.device))
    before=p.metrics(old.plan.touches)['point_hits'];after=p.metrics(new.plan.touches)['point_hits']
    assert after['mean_mm']<before['mean_mm']
    assert after['mean_mm']>5  # close alternating hands still get useful side-by-side room
    assert [(t.hand,t.touch.down,t.touch.up) for t in old.plan.touches]==[(t.hand,t.touch.down,t.touch.up) for t in new.plan.touches]
