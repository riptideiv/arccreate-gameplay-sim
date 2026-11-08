"""Audit real native scores and measured wrist angles for completed takes."""
from pathlib import Path
import argparse
import math
import json
import sys
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scoring_report import compare_ticks
from arc_hand_order.planning.wrist import metrics


def measured(plan,motion,free_deg=30):
    # The pose report certifies zero held-contact drift. Use those exact device
    # coordinates and the actual recorded pitch/yaw, without a guessed camera.
    n=min(len(motion['hands'][h]) for h in ('left','right'))
    points=np.zeros((n,2,3));active=np.zeros((n,2),bool)
    angles=np.stack([np.asarray(motion['hands'][h])[:,7:9] for h in ('left','right')],axis=1)
    for t in plan['touches']:
        h=0 if t['hand']=='left' else 1;a,b=max(0,t['down_frame']),min(n,t['up_frame'])
        points[a:b,h,:2]=np.asarray(t['path_mm'])[a-t['down_frame']:b-t['down_frame']]/1000
        active[a:b,h]=True
    return metrics(points,angles,active,free_deg)


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--pair',action='append',required=True)
    args=ap.parse_args();rows=[]
    for pair in args.pair:
        old,new=pair.split(':')
        def read(take,name):return json.loads((ROOT/'output/runs'/take/name).read_text())
        a,b=read(old,'plan.json'),read(new,'plan.json');ja,jb=read(old,'judgements.json'),read(new,'judgements.json');run=read(new,'run.json')
        assert run['status']=='complete'
        assert run['settings']['crossing_depth_weight']==100 and run['settings']['wrist_twist_weight']==10
        assert not b['planning']['skipped_ticks'] and not b['planning']['contact_exclusions']
        for field in ('hits','misses','score'):assert ja[field]==jb[field],(new,field,ja[field],jb[field])
        for record in jb['judgements']:
            if record['result'].startswith('Miss'):assert record['chart_ms']>b['preview']['chart_end_ms']
        ca,cb=compare_ticks(a,ja),compare_ticks(b,jb)
        assert not cb['unconfirmed_predictions']
        assert {(t['id'],t['native_hit']) for t in ca['ticks']}=={(t['id'],t['native_hit']) for t in cb['ticks']}
        m=run['metrics'];assert not m.get('pose_solver_fallback_reason')
        assert m['maximum_contact_drift_m']<1e-9
        assert all(h['peak_surface_bound_m_s']<=m['max_speed_m_s']+.001 for h in m['hands'].values())
        w=m['arm_winding'];assert w['interpolation_certified'] and -260<=w['minimum_deg']<=w['maximum_deg']<=260
        before=measured(a,read(old,'motion.json'));after=measured(b,read(new,'motion.json'))
        assert math.isclose(after['active_strain_s'],m['wrist_twist']['active_strain_s'],abs_tol=1e-5,rel_tol=1e-8)
        rows.append({'before':old,'after':new,'hits':jb['hits'],'misses':jb['misses'],'score':jb['score'],
            'native_arc_ticks':f"{cb['confirmed_hits']}/{cb['predicted_hits']}",
            'before_wrist':before,'after_wrist':after,'crossing':b['crossing_ergonomics']})
    print(json.dumps(rows,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
