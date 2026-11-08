from pathlib import Path
import json
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from dashboard.ending import results_spec
from dashboard import server


def ledger(results,score):
    return {'score':score,'judgements':[{'chart_ms':i*100,'result':r} for i,r in enumerate(results)]}


def test_native_rank_requires_complete_chart_and_matches_arccreate():
    run={'settings':{'start_s':0}}
    play,rank=results_spec(run,ledger(['Max']*9+['PerfectLate'],10_000_009))
    assert rank=='AllPerfect' and play['NoteCount']==10 and play['MaxCount']==9
    assert play['LatePerfectCount']==1 and play['MaxCombo']==10
    # GOOD preserves combo, but cannot earn ArcCreate's AllPerfect rank.
    play,rank=results_spec(run,ledger(['Max']*9+['GoodEarly'],9_500_009))
    assert rank=='FullCombo'
    # A perfect excerpt is not a completed song.
    with pytest.raises(ValueError,match='complete chart'):
        results_spec(run,ledger(['Max']*5,5_000_005))
    with pytest.raises(ValueError,match='starting at zero'):
        results_spec({'settings':{'start_s':10}},ledger(['Max']*10,10_000_010))


def test_max_combo_is_reconstructed_rather_than_using_final_combo():
    j=ledger(['Max']*4+['MissLate']+['Max']*3,8_750_007)
    play,rank=results_spec({'settings':{'start_s':0}},j)
    assert play['MaxCombo']==4 and rank=='Clear' and play['LateMissCount']==1


@pytest.mark.parametrize('results,score,gauge,rank',[
    (['Max']*10,10_000_010,100,'Max'),
    (['GoodEarly']*10,5_000_000,100,'AllGood'),
    (['Max']*9+['MissLate'],9_000_009,70,'Fail'),
    (['Max']*9+['MissLate'],9_000_009,71,'Clear'),
])
def test_native_special_ranks_and_strict_gauge_threshold(results,score,gauge,rank):
    j=ledger(results,score);j['play_result']={'NoteCount':10,'GaugeValue':gauge,'GaugeClearRequirement':70}
    assert results_spec({'settings':{'start_s':0}},j)[1]==rank


def test_result_music_waits_for_native_activation_when_gameplay_audio_ends_early(tmp_path):
    import shutil,subprocess
    import numpy as np
    from dashboard.ending import mix_result_audio
    if not shutil.which('ffmpeg'):pytest.skip('ffmpeg required')
    song=tmp_path/'song.wav';music=tmp_path/'results.wav';movie=tmp_path/'movie.mp4';output=tmp_path/'output.mp4'
    for path,hz in [(song,440),(music,880)]:
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i',f'sine=frequency={hz}:duration=1','-y',str(path)],check=True)
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=blue:s=64x48:r=60:d=3','-c:v','libx264','-pix_fmt','yuv420p','-y',str(movie)],check=True)
    mix_result_audio(song,movie,music,output,2,3,1)
    pcm=np.frombuffer(subprocess.check_output(['ffmpeg','-v','error','-i',str(output),'-vn','-ac','1','-ar','8000','-f','f32le','-']),dtype='<f4')
    def rms(start,end):return float(np.sqrt(np.mean(pcm[round(start*8000):round(end*8000)]**2)))
    assert rms(.2,.8)>.04 and rms(1.2,1.8)<.001 and rms(2.2,2.8)>.04


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
    result={'video':'ending.mp4','start_s':4,'clear_result':'AllPerfect'}
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
