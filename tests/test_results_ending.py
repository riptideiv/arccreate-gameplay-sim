from pathlib import Path
import json
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from dashboard.ending import results_spec
from dashboard import server


def ledger(results,score):
    return {'score':score,'judgements':[{'chart_ms':i*100,'result':r} for i,r in enumerate(results)]}


def test_pure_memory_requires_all_native_perfects_and_complete_chart():
    run={'settings':{'start_s':0}}
    play,banner=results_spec(run,ledger(['Max']*9+['PerfectLate'],10_000_009))
    assert banner=='PURE MEMORY' and play['NoteCount']==10 and play['MaxCount']==9
    assert play['LatePerfectCount']==1 and play['MaxCombo']==10
    # No misses alone does not mean Pure Memory: GOOD is a full recall.
    play,banner=results_spec(run,ledger(['Max']*9+['GoodEarly'],9_500_009))
    assert banner=='FULL RECALL'
    # A perfect excerpt is not a completed song.
    with pytest.raises(ValueError,match='complete chart'):
        results_spec(run,ledger(['Max']*5,5_000_005))
    with pytest.raises(ValueError,match='starting at zero'):
        results_spec({'settings':{'start_s':10}},ledger(['Max']*10,10_000_010))


def test_max_combo_is_reconstructed_rather_than_using_final_combo():
    j=ledger(['Max']*4+['MissLate']+['Max']*3,8_750_007)
    play,banner=results_spec({'settings':{'start_s':0}},j)
    assert play['MaxCombo']==4 and banner=='TRACK COMPLETE' and play['LateMissCount']==1


def test_ending_failure_keeps_saved_gameplay_and_success_publishes_only_new_media(tmp_path,monkeypatch):
    monkeypatch.setattr(server,'RUNS',tmp_path)
    q=server.RenderQueue();directory=tmp_path/'take';directory.mkdir()
    q.active={'id':'take','status':'running','ending_only':True,'video':'original.mp4',
              'live':{'scene':'scene.glb','motion':'motion.json'},'export_video':'old.mp4','metrics':{'contacts':12}}
    before=json.loads(json.dumps(q.active))
    def fail(*a,**kw):raise ValueError('native results unavailable')
    monkeypatch.setattr(q,'command',fail);q.work_ending(directory)
    assert q.active['status']=='complete' and q.active['video']=='original.mp4' and q.active['export_video']=='old.mp4'
    assert q.active['live']==before['live'] and q.active['metrics']==before['metrics']
    result={'video':'ending.mp4','start_s':4,'banner':'PURE MEMORY'}
    def succeed(*a,**kw):(directory/'render.log').write_text('ENDING_RESULT '+json.dumps(result)+'\n')
    monkeypatch.setattr(q,'command',succeed);q.work_ending(directory)
    assert q.active['video']=='ending.mp4' and q.active['ending']==result
    assert q.active['export_video'] is None and q.active['live']==before['live'] and q.active['metrics']==before['metrics']


@pytest.mark.parametrize("join,copied", [(1,0),(3,120)])
def test_short_video_splice_has_exact_frame_clock_even_before_first_gop(tmp_path,monkeypatch,join,copied):
    import shutil,subprocess
    from dashboard import ending
    if not shutil.which('ffmpeg'):pytest.skip('ffmpeg required')
    monkeypatch.setattr(ending,'ROOT',tmp_path)
    directory=tmp_path/'output/runs/take';folder=directory/'ending-test';folder.mkdir(parents=True)
    (directory/'run.json').write_text(json.dumps({'ending':{'folder':'ending-test','start_s':join,'duration_s':1}}))
    body=directory/'body.mp4';tail=directory/'tail.mp4'
    for path,color in [(body,'blue'),(tail,'red')]:
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i',f'color=c={color}:s=64x48:r=60:d=4','-f','lavfi','-i','sine=frequency=440:duration=4','-c:v','libx264','-g','120','-pix_fmt','yuv420p','-c:a','aac','-y',str(path)],check=True)
    report=ending.finalize(directory,tail,body)
    assert report['copied_body_frames']==copied and report['reencoded_seam_frames']==60
    output=directory/'preview-60fps-with-results.mp4'
    probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-select_streams','v:0','-show_entries','stream=avg_frame_rate,nb_frames','-of','json',str(output)],text=True))['streams'][0]
    assert probe['avg_frame_rate']=='60/1' and int(probe['nb_frames'])==(join+1)*60
    check=subprocess.run(['ffmpeg','-v','error','-i',str(output),'-f','null','-'],capture_output=True,text=True)
    assert check.returncode==0 and not check.stderr

    clock=json.loads(subprocess.check_output(['ffprobe','-v','error','-select_streams','v:0','-show_frames','-show_entries','frame=best_effort_timestamp_time','-of','json',str(output)],text=True))['frames']
    times=[float(f['best_effort_timestamp_time']) for f in clock]
    assert len(times)==(join+1)*60 and times[0]==0
    assert all(abs(b-a-1/60)<.00001 for a,b in zip(times,times[1:]))
