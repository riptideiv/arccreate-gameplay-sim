"""One fresh-process contact-planning trial; no graphics or disk-plan cache."""
import argparse,cProfile,hashlib,importlib,json,os,resource,sys,time
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--chart',type=Path,required=True);p.add_argument('--settings',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--device',type=Path,required=True);p.add_argument('--profile',type=Path);p.add_argument('--cache-dir',type=Path);a=p.parse_args()
source=a.source.resolve();sys.path[:0]=[str(source),str(source/'vendor/arc-hand-order/src')]
start=time.perf_counter();cpu=time.process_time()
from plan_excerpt import create_plan
import arc_hand_order
from arc_hand_order import api
from functools import partial
# Pin the identical physical device file in both isolated imports, including output metadata.
api.plan_chart = partial(api.plan_chart, device_path=a.device)
assert Path(arc_hand_order.__file__).resolve().is_relative_to(source),arc_hand_order.__file__
config=json.loads(a.settings.read_text())
def progress(event):print('PLANNER_PROGRESS '+json.dumps(event),flush=True)
def run():
 kwargs={'progress':progress}
 if a.cache_dir:kwargs['cache_dir']=a.cache_dir
 result=create_plan(a.chart,round(config['start_s']*1000),round(config['end_s']*1000),config,**kwargs)
 a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
if a.profile:
 profiler=cProfile.Profile();profiler.runcall(run);profiler.dump_stats(str(a.profile))
else:run()
rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/(1024**2 if sys.platform=='darwin' else 1024)
metrics={'wall_s':time.perf_counter()-start,'cpu_s':time.process_time()-cpu,'peak_rss_mib':rss,'finished_unix_s':time.time(),'pid':os.getpid(),'python':sys.version,'source':str(source),'profiled':bool(a.profile),'disk_cache_enabled':bool(a.cache_dir)}
a.output.with_suffix('.timing.json').write_text(json.dumps(metrics,indent=2));print(json.dumps(metrics),flush=True)
