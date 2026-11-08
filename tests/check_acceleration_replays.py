"""Audit completed production/native takes, including source-specific tick equality.

See docs/continuous-acceleration.md for the verified take ids and invocation.
This checks real ArcCreate ledgers, not a planner-only hit prediction.
"""
import argparse
import json
from pathlib import Path
import sys
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scoring_report import compare_ticks
from arc_hand_order.planning.trajectory import motion_metrics


def audit(before,after):
    def load(take,name):return json.loads((ROOT/'output/runs'/take/name).read_text())
    old,p=load(before,'plan.json'),load(after,'plan.json')
    oldj,j=load(before,'judgements.json'),load(after,'judgements.json')
    run=load(after,'run.json')
    assert run['status']=='complete'
    for field in ('hits','misses','score'):
        assert oldj[field]==j[field],(field,oldj[field],j[field])
    check=compare_ticks(p,j);prior=compare_ticks(old,oldj)
    assert not check['unconfirmed_predictions']
    assert {(x['id'],x['native_hit']) for x in prior['ticks']}=={(x['id'],x['native_hit']) for x in check['ticks']}
    assert not p['planning']['contact_exclusions'] and not p['planning']['skipped_ticks']
    assert run['settings']['arc_trace_weight']==1000 and run['settings']['acceleration_weight']==100
    assert run['settings']['prefer_arc_trace']
    m=run['metrics'];w=m['arm_winding']
    assert w['interpolation_certified'] and w['minimum_deg']>=-260 and w['maximum_deg']<=260
    assert all(h['peak_surface_bound_m_s']<=m['max_speed_m_s']+.001 for h in m['hands'].values())
    cutoff=p['preview']['chart_end_ms']
    misses=[x for x in j['judgements'] if x['result'].startswith('Miss')]
    assert all(x['chart_ms']>cutoff for x in misses)
    return p,{'take':after,'hits':j['hits'],'misses_after_cutoff':len(misses),'score':j['score'],
        'ticks':f"{check['confirmed_hits']}/{check['predicted_hits']}",'acceleration':motion_metrics(p['touches'])}


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    for name in ('arcana-before','arcana-after','bibideba-before','bibideba-after'):ap.add_argument('--'+name,required=True)
    a=ap.parse_args();p,arcana=audit(a.arcana_before,a.arcana_after);_,bib=audit(a.bibideba_before,a.bibideba_after)
    def stats(t):
        xy=np.array(t['path_mm'])/1000
        return float(np.linalg.norm(np.diff(xy,axis=0),axis=1).sum()),float(np.linalg.norm(np.diff(xy,n=2,axis=0)*3600,axis=1).max(initial=0))
    regular=[t for t in p['touches'] if 6.24<=t['down_ms']/1000+3.85<=6.28 and any(x['kind']=='chain' for x in t['parts'])]
    assert len(regular)==2 and all(stats(t)[0]>.07 and stats(t)[1]<15 for t in regular)
    teeth=[t for t in p['touches'] if 6.8<=t['down_ms']/1000+3.85<=6.9 and any(x['kind']=='chain' for x in t['parts'])]
    assert len(teeth)==1 and stats(teeth[0])[0]<.025 and stats(teeth[0])[1]<5
    print(json.dumps({'arcana':arcana,'bibideba':bib},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
