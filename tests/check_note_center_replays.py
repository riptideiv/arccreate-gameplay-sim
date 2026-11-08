"""Audit real production replays and measure offsets from their serialized contacts."""
import argparse
from pathlib import Path
import json
import sys
from types import SimpleNamespace as N
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from arc_hand_order import api
from arc_hand_order.planning.occupations import occupations_from_chart
from arc_hand_order.planning.projection import GameplayProjection
from arc_hand_order.planning.note_center import NoteCenterPreference
from dashboard.chart_library import load_catalog,chart_paths
from scoring_report import compare_ticks


def measure(plan,aff):
    items={o.occupation_id:o for o in occupations_from_chart(api.load_chart(aff).chart).items}
    rows=[]
    for t in plan['touches']:
        parts=[]
        for p in t['parts']:
            if p['kind'] not in ('tap','sky','hold'):continue
            parts.append(N(candidate=N(occupation=items[p['occupation_id']]),start=p['from_frame'],stop=p['to_frame']))
        path=tuple((x/1000,y/1000) for x,y in t['path_mm']);down=t['down_frame']
        rows.append(N(touch=N(parts=parts,down=down,up=t['up_frame'],first_xy=path[0],xy=lambda f,p=path,d=down:p[min(max(f-d,0),len(p)-1)])))
    return NoteCenterPreference(GameplayProjection(api.load_device())).metrics(rows)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--pair',action='append',required=True,help='before_take:after_take')
    args=ap.parse_args();results=[];catalog=load_catalog(ROOT)
    for pair in args.pair:
        before,after=pair.split(':')
        def read(take,name):return json.loads((ROOT/'output/runs'/take/name).read_text())
        old,p=read(before,'plan.json'),read(after,'plan.json');oldj,j=read(before,'judgements.json'),read(after,'judgements.json');run=read(after,'run.json')
        assert run['status']=='complete' and run['settings']['note_center_weight']==50
        assert not p['planning']['skipped_ticks'] and not p['planning']['contact_exclusions']
        for k in ('hits','misses','score'):assert j[k]==oldj[k],(pair,k,oldj[k],j[k])
        a,b=compare_ticks(old,oldj),compare_ticks(p,j)
        assert not b['unconfirmed_predictions']
        assert {(x['id'],x['native_hit']) for x in a['ticks']}=={(x['id'],x['native_hit']) for x in b['ticks']}
        w=run['metrics']['arm_winding'];assert w['interpolation_certified'] and -260<=w['minimum_deg']<=w['maximum_deg']<=260
        _,aff,_=chart_paths(ROOT,run['settings']['chart'],catalog[run['settings']['chart']])
        oldm,newm=measure(old,aff),measure(p,aff)
        if 'axium' in run['settings']['chart']:
            assert newm['point_hits']['median_mm']<oldm['point_hits']['median_mm']*.5
        results.append({'before':before,'after':after,'native_hits':j['hits'],'native_misses':j['misses'],'score':j['score'],
            'arc_ticks':f"{b['confirmed_hits']}/{b['predicted_hits']}",'before_offsets':oldm,'after_offsets':newm})
    print(json.dumps(results,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
