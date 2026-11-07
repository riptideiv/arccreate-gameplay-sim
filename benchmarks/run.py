"""Reproducible sequential baseline/optimized trials. See benchmarks/README.md."""
import argparse,hashlib,json,os,platform,shutil,statistics,subprocess,sys,time,tarfile,importlib.metadata
from pathlib import Path
HERE=Path(__file__).resolve().parent
p=argparse.ArgumentParser();p.add_argument('--workspace',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--stage',choices=['snapshot','baseline-first','all','profiles','cache-hits','report'],default='all');p.add_argument('--baseline-revision',default='25f6003');a=p.parse_args()
original=a.workspace.resolve()/'prototypes/ipad-rhythm';optimized=HERE.parent
out=a.output.resolve();out.mkdir(parents=True,exist_ok=True);baseline=out/'baseline-source'
windows=[(20,30),(65,75),(90,100)]
def fingerprint(source):
 files=[source/'plan_excerpt.py',*sorted((source/'vendor/arc-hand-order/src').rglob('*.py'))]
 if (source/'plan_cache.py').exists():files.append(source/'plan_cache.py')
 hashes={str(f.relative_to(source)):hashlib.sha256(f.read_bytes()).hexdigest() for f in files}
 return {'files':hashes,'sha256':hashlib.sha256(json.dumps(hashes,sort_keys=True).encode()).hexdigest()}
if a.stage=='snapshot':
 if baseline.exists():raise SystemExit('Baseline already exists; refusing to overwrite')
 baseline.mkdir()
 # The packaged runnable snapshot works without Git. Git is a read-only
 # fallback for explicitly requested alternative revisions.
 archive=HERE/'snapshots'/f'baseline-{a.baseline_revision}.tar.gz'
 if archive.exists():
  with tarfile.open(archive) as saved:saved.extractall(baseline,filter='data')
 else:
  prefix=Path('prototypes/ipad-rhythm')
  paths=subprocess.check_output(['git','-C',str(a.workspace),'ls-tree','-r','--name-only',a.baseline_revision,'--',str(prefix/'plan_excerpt.py'),str(prefix/'vendor/arc-hand-order/src')],text=True).splitlines()
  for name in paths:
   dest=baseline/Path(name).relative_to(prefix);dest.parent.mkdir(parents=True,exist_ok=True)
   dest.write_bytes(subprocess.check_output(['git','-C',str(a.workspace),'show',f'{a.baseline_revision}:{name}']))
 shutil.copy2(HERE/'worker.py',out/'worker.py')
 for lo,hi in windows:
  config=json.loads((HERE/'fixtures/bibideba-settings.json').read_text());config.update(start_s=lo,end_s=hi)
  assert all(config[k]==v for k,v in {'max_speed_m_s':4,'search_width':256,'arc_path_mode':'score','prefer_arc_trace':False,'allow_misses':True}.items())
  (out/f'{lo}-{hi}-settings.json').write_text(json.dumps(config,indent=2))
 manifest={'baseline':fingerprint(baseline),'chart_sha256':hashlib.sha256((original/'charts/imported/bibideba/2.aff').read_bytes()).hexdigest(),'host':platform.platform(),'machine':platform.machine(),'interpreter':sys.executable,'dependencies':{n:importlib.metadata.version(n) for n in ('numpy','pydantic')},'device_sha256':hashlib.sha256((original/'vendor/arc-hand-order/src/arc_hand_order/devices/ipad_pro_11_m2.json').read_bytes()).hexdigest(),'baseline_commit':a.baseline_revision,'progress':'same callback/logging for both','contention':'No render/export during headline trials; dashboard idle. Python editing only.'}
 (out/'manifest.json').write_text(json.dumps(manifest,indent=2));print('Preserved isolated runnable baseline',baseline);raise SystemExit()
def trial(kind,lo,hi,n,profile=False,cache=False):
 source=baseline if kind=='baseline' else optimized
 target=out/f'{kind}-{lo}-{hi}-{n}.json'
 if target.with_suffix('.timing.json').exists():return
 assert fingerprint(baseline)==json.loads((out/'manifest.json').read_text())['baseline'],'Baseline modified'
 assert hashlib.sha256((original/'charts/imported/bibideba/2.aff').read_bytes()).hexdigest()==json.loads((out/'manifest.json').read_text())['chart_sha256'],'Chart changed'
 source_id=fingerprint(source);target.with_suffix('.sources.json').write_text(json.dumps(source_id,indent=2))
 command=[sys.executable,str(out/'worker.py'),'--source',str(source),'--chart',str(original/'charts/imported/bibideba/2.aff'),'--settings',str(out/f'{lo}-{hi}-settings.json'),'--output',str(target),'--device',str(original/'vendor/arc-hand-order/src/arc_hand_order/devices/ipad_pro_11_m2.json')]
 if profile:command+=['--profile',str(target.with_suffix('.prof'))]
 if cache:command+=['--cache-dir',str(out/'completed-plan-cache')]
 print('Starting',target.name,flush=True)
 with target.with_suffix('.log').open('w') as log:
  subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True,env={**os.environ,'PYTHONHASHSEED':'0','PYTHONDONTWRITEBYTECODE':'1'},cwd=source)
 assert source_id==fingerprint(source),'Source changed during measured trial'
 assert hashlib.sha256((original/'charts/imported/bibideba/2.aff').read_bytes()).hexdigest()==json.loads((out/'manifest.json').read_text())['chart_sha256'],'Chart changed during trial'
 print('Finished',target.name,json.loads(target.with_suffix('.timing.json').read_text()),flush=True)
if a.stage=='cache-hits':
 hits=[]
 for lo,hi in windows:
  trial('cache-fill',lo,hi,0,cache=True)
  for n in range(1,4):
   trial('cache-hit',lo,hi,n,cache=True)
   log=(out/f'cache-hit-{lo}-{hi}-{n}.log').read_text()
   assert '"cache_hit": true' in log and '"phase": "search"' not in log
  values=[json.loads((out/f'cache-hit-{lo}-{hi}-{n}.timing.json').read_text()) for n in range(1,4)]
  expected=json.loads((out/f'optimized-{lo}-{hi}-1.json').read_text());expected['stats'].pop('elapsed_s')
  for n in range(1,4):
   actual=json.loads((out/f'cache-hit-{lo}-{hi}-{n}.json').read_text());actual['stats'].pop('elapsed_s');assert actual==expected
  hits.append({'window_s':[lo,hi],'median_s':statistics.median(v['wall_s'] for v in values),'range_s':[min(v['wall_s'] for v in values),max(v['wall_s'] for v in values)],'peak_rss_mib':max(v['peak_rss_mib'] for v in values),'trials':3,'search_performed':False})
 (out/'cache-hit-report.json').write_text(json.dumps(hits,indent=2));print(json.dumps(hits,indent=2))
elif a.stage=='baseline-first':
 for lo,hi in windows:trial('baseline',lo,hi,1)
elif a.stage=='profiles':
 for lo,hi in ((20,30),(65,75)):
  for kind in ('baseline','optimized'):trial(kind,lo,hi,'profile',True)
elif a.stage=='all':
 for n in range(1,4):
  for lo,hi in windows:
   for kind in ('baseline','optimized'):trial(kind,lo,hi,n)
if a.stage in ('report','all'):
 rows=[];comparisons=[]
 for lo,hi in windows:
  times={k:[json.loads((out/f'{k}-{lo}-{hi}-{n}.timing.json').read_text()) for n in range(1,4)] for k in ('baseline','optimized')}
  base=json.loads((out/f'baseline-{lo}-{hi}-1.json').read_text());base['stats'].pop('elapsed_s',None)
  for k in ('baseline','optimized'):
   for n in range(1,4):
    plan=json.loads((out/f'{k}-{lo}-{hi}-{n}.json').read_text());plan['stats'].pop('elapsed_s',None)
    same=plan==base;comparisons.append({'window':[lo,hi],'kind':k,'trial':n,'entire_plan_except_stats_elapsed_s_identical':same})
    assert same,(lo,hi,k,n,'PLAN DIFFERENCE')
  row={'window_s':[lo,hi]}
  for kind,values in times.items():row[kind]={'median_s':statistics.median(v['wall_s'] for v in values),'range_s':[min(v['wall_s'] for v in values),max(v['wall_s'] for v in values)],'peak_rss_mib':max(v['peak_rss_mib'] for v in values),'cpu_median_s':statistics.median(v['cpu_s'] for v in values)}
  row['speedup']=row['baseline']['median_s']/row['optimized']['median_s'];row['identical']=True;rows.append(row)
 report={'rows':rows,'aggregate_speedup':sum(r['baseline']['median_s'] for r in rows)/sum(r['optimized']['median_s'] for r in rows),'comparisons':comparisons}
 (out/'report.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
