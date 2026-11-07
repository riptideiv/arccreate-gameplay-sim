"""Build a native results ending from saved judgements, without contact/replay work."""
from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
FIELDS={'Max':'MaxCount','PerfectEarly':'EarlyPerfectCount','PerfectLate':'LatePerfectCount',
        'GoodEarly':'EarlyGoodCount','GoodLate':'LateGoodCount','MissEarly':'EarlyMissCount','MissLate':'LateMissCount',
        'PerfectMapped':'MappedPerfectCount','GoodMapped':'MappedGoodCount','MissMapped':'MappedMissCount'}


def results_spec(run,judgements):
    if run['settings']['start_s'] != 0:
        raise ValueError('Results endings require a chart-complete take starting at zero.')
    counts=Counter(j['result'] for j in judgements['judgements'])
    if set(counts)-set(FIELDS):raise ValueError('Unknown native judgement type.')
    play={field:counts.get(key,0) for key,field in FIELDS.items()}
    perfect=sum(counts[k] for k in ('Max','PerfectEarly','PerfectLate','PerfectMapped'))
    good=sum(counts[k] for k in ('GoodEarly','GoodLate','GoodMapped'))
    total=sum(counts.values()); denominator=judgements['score']-counts['Max']
    saved=judgements.get('play_result') or {}
    if saved.get('NoteCount'): note_count=saved['NoteCount']
    elif denominator>0:
        inferred=(perfect+.5*good)*10_000_000/denominator
        note_count=round(inferred)
        if abs(note_count-inferred)>.01: raise ValueError('Could not validate native chart note count.')
    else: raise ValueError('A saved native note count is needed for this result.')
    if total != note_count:raise ValueError('The selected take does not cover the complete chart.')
    combo=peak=0
    for item in judgements['judgements']:
        combo=0 if item['result'].startswith('Miss') else combo+1
        peak=max(peak,combo)
    play.update(NoteCount=note_count,MaxCombo=peak,GaugeValue=saved.get('GaugeValue',100),GaugeClearRequirement=saved.get('GaugeClearRequirement',70),GaugeMax=100)
    # Arcaea terminology for the banner; native results keep ArcCreate's badges.
    banner='PURE MEMORY' if perfect==note_count else 'FULL RECALL' if peak==note_count else 'TRACK COMPLETE'
    return play,banner


def duration(path):
    return float(subprocess.check_output(['ffprobe','-v','error','-show_entries','format=duration','-of','default=nw=1:nk=1',str(path)],text=True))


def command(args):
    subprocess.run([str(a) for a in args],check=True)


def prepare(directory):
    directory=Path(directory).resolve();run=json.loads((directory/'run.json').read_text())
    if run['status']!='complete' and not run.get('ending_only'):raise ValueError('Choose a completed live preview.')
    plan=json.loads((directory/'plan.json').read_text());judgements=json.loads((directory/'judgements.json').read_text())
    play,banner=results_spec(run,judgements)
    original=run.get('ending',{}).get('original_video',run['video'])
    if not original.startswith('/media/'):raise ValueError('Unexpected saved gameplay path.')
    source=(ROOT/'output'/original.removeprefix('/media/')).resolve()
    if not source.is_relative_to((ROOT/'output').resolve()):raise ValueError('Invalid gameplay path.')
    last=max(j['chart_ms'] for j in judgements['judgements'])
    last_video=(last+plan['chart'].get('source_audio_offset_ms',plan['chart']['audio_offset_ms'])-plan['preview']['audio_start_ms'])/1000
    join=min(math.ceil((last_video+1.2)*60)/60,math.floor(duration(source)*60)/60)
    assets=Path(plan['chart']['path']).parent;project=(assets/'project.arcproj').read_text()
    def scalar(name):
        match=re.search(r'^  '+name+r': (.*)$',project,re.M)
        return json.loads(match[1]) if match and match[1].startswith('"') else match[1] if match else ''
    identity=hashlib.sha256(json.dumps([play,banner,join,original],sort_keys=True).encode()+(ROOT/'arc-create-export/Ending.cs').read_bytes()+Path(__file__).read_bytes()).hexdigest()[:12]
    folder=directory/f'ending-{identity}';folder.mkdir(exist_ok=True)
    config=dict(output=str(folder/'native-ending.mp4'),ffmpeg=shutil.which('ffmpeg'),background=str(folder/'background.png'),jacket=str((assets/scalar('jacketPath')).resolve()),title=scalar('title'),composer=scalar('composer'),difficulty=scalar('difficulty'),charter=scalar('charter'),color=scalar('difficultyColor'),banner=banner,play=play)
    (folder/'config.json').write_text(json.dumps(config,ensure_ascii=False,indent=2))
    command(['ffmpeg','-v','error','-ss',join,'-i',source,'-frames:v','1','-update','1','-y',folder/'background.png'])
    native=folder/'native-ending.mp4'
    if not Path(str(native)+'.result.json').exists():
        app=Path(os.environ.get('ARCCREATE_EXPORT_APP',ROOT/'.tools/ArcCreate/ArcCreate-media.app'))/'Contents/MacOS/ArcCreate'
        native_project=directory/'arccreate/project.arcproj'
        if not native_project.is_file():native_project=assets/'project.arcproj'
        command([app,native_project.resolve(),'--export-ending',folder/'config.json','-screen-fullscreen','0','-screen-width','1148','-screen-height','802','-logFile',folder/'native.log'])
    silent,_,_=splice_picture(source,native,join,8.2,folder,'native')
    music=ROOT/'.tools/ArcCreate/source/Assets/AudioClips/ResultScreen.ogg'
    output=folder/'gameplay-with-results.mp4'
    filters=(f'[0:a]atrim=0:{join},asetpts=PTS-STARTPTS[body];'
             f'[0:a]atrim=start={join}:end={join+2.2},asetpts=PTS-STARTPTS,apad,atrim=0:2.2,afade=t=out:st=1.5:d=0.7[clear];'
             '[2:a]atrim=0:6,asetpts=PTS-STARTPTS,volume=0.5,afade=t=in:st=0:d=0.2,afade=t=out:st=5.5:d=0.5[result];'
             '[body][clear][result]concat=n=3:v=0:a=1[a]')
    command(['ffmpeg','-v','error','-i',source,'-i',silent,'-stream_loop','-1','-i',music,'-filter_complex',filters,'-map','1:v','-map','[a]','-c:v','copy','-c:a','aac','-b:a','192k','-t',join+8.2,'-movflags','+faststart','-y',output])
    metadata={'revision':identity,'start_s':join,'duration_s':8.2,'banner':banner,'native_scene':'Result','original_video':original,'video':'/media/'+output.relative_to(ROOT/'output').as_posix(),'play':play,'folder':folder.name}
    (folder/'ending.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2))
    print('ENDING_RESULT '+json.dumps(metadata),flush=True)
    return metadata


def splice_picture(body, tail, join, length, folder, label):
    # Preserve complete GOPs. Cutting a stream-copy at an arbitrary B frame can
    # leave a forward reference across the splice and duplicate presentation times.
    keyframes=json.loads(subprocess.check_output(['ffprobe','-v','error','-select_streams','v:0','-skip_frame','nokey','-show_frames','-show_entries','frame=best_effort_timestamp_time','-of','json',str(body)],text=True))['frames']
    boundary=max(float(f['best_effort_timestamp_time']) for f in keyframes if float(f['best_effort_timestamp_time'])<=join)
    boundary_frame=round(boundary*60);lead_frames=round(join*60)-boundary_frame
    encoded=folder/f'{label}-tail-60fps.mp4';prefix=folder/f'{label}-body-prefix.mp4'
    filters=(f'[0:v]trim=end_frame={lead_frames},setpts=PTS-STARTPTS[lead];'
             f'[1:v]fps=60:start_time=0,trim=end_frame={round(length*60)},setpts=PTS-STARTPTS[tail];'
             '[lead][tail]concat=n=2:v=1:a=0,fps=60,format=yuv420p[v]')
    if lead_frames:
        command(['ffmpeg','-v','error','-ss',boundary,'-i',body,'-i',tail,'-filter_complex',filters,'-map','[v]','-an','-c:v','libx264','-preset','medium','-bf','0','-crf','18','-pix_fmt','yuv420p','-y',encoded])
    else:
        command(['ffmpeg','-v','error','-i',tail,'-vf','fps=60:start_time=0','-t',length,'-an','-c:v','libx264','-preset','medium','-bf','0','-crf','18','-pix_fmt','yuv420p','-y',encoded])
    if boundary_frame:
        command(['ffmpeg','-v','error','-i',body,'-frames:v',boundary_frame,'-an','-c:v','copy','-y',prefix])
    listing=folder/f'{label}-list.txt';listing.write_text((f"file '{prefix}'\n" if boundary_frame else '')+f"file '{encoded}'\n")
    silent=folder/f'{label}-video.mp4'
    command(['ffmpeg','-v','error','-f','concat','-safe','0','-i',listing,'-an','-c:v','copy','-y',silent])
    normalized=folder/f'{label}-clock.mp4'
    command(['ffmpeg','-v','error','-i',silent,'-an','-c:v','copy','-bsf:v','setts=pts=PTS:dts=N/(60*TB)+STARTDTS:duration=1/(60*TB)','-y',normalized])
    return normalized,boundary_frame,lead_frames


def finalize(directory, tail, body):
    directory=Path(directory).resolve();tail=Path(tail).resolve();body=Path(body).resolve()
    run=json.loads((directory/'run.json').read_text());ending=run['ending'];join=ending['start_s'];length=ending['duration_s'];folder=directory/ending['folder']
    if duration(body)<join or duration(tail)<length-.1:raise ValueError('The supplied body or ending is incomplete.')
    from fractions import Fraction
    def video_info(path):
        data=json.loads(subprocess.check_output(['ffprobe','-v','error','-select_streams','v:0','-show_entries','stream=codec_name,width,height,avg_frame_rate','-of','json',str(path)],text=True))
        return data['streams'][0]
    source_info,tail_info=video_info(body),video_info(tail)
    if source_info['codec_name']!='h264' or abs(float(Fraction(source_info['avg_frame_rate']))-60)>.01:
        raise ValueError('Use a constant-60-fps H.264 body video for an end-only splice.')
    if (source_info['width'],source_info['height']) != (tail_info['width'],tail_info['height']):
        raise ValueError('The body and ending must have the same canvas size and camera.')
    silent,boundary_frame,lead_frames=splice_picture(body,tail,join,length,folder,'3d')
    output=directory/'preview-60fps-with-results.mp4'
    muxed=folder/'final-mux.mp4'
    filters=(f'[0:a]atrim=0:{join},asetpts=PTS-STARTPTS[body];[1:a]atrim=0:{length},asetpts=PTS-STARTPTS,apad,atrim=0:{length}[tail];[body][tail]concat=n=2:v=0:a=1[a]')
    command(['ffmpeg','-v','error','-i',body,'-i',tail,'-i',silent,'-filter_complex',filters,'-map','2:v','-map','[a]','-c:v','copy','-c:a','aac','-b:a','192k','-t',join+length,'-movflags','+faststart','-y',muxed])
    clocked=folder/'final-clock.mp4'
    command(['ffmpeg','-v','error','-i',muxed,'-map','0','-c','copy','-bsf:v','setts=pts=PTS:dts=N/(60*TB)+STARTDTS:duration=1/(60*TB)','-movflags','+faststart','-y',clocked])
    os.replace(clocked,output)
    result={'video':'/media/'+output.relative_to(ROOT/'output').as_posix(),'ending_start_s':join,'ending_duration_s':length,'body':str(body),'tail':str(tail),'fps':60,'copied_body_frames':boundary_frame,'reencoded_seam_frames':lead_frames}
    (folder/'final-export.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))
    return result


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('take',type=Path);parser.add_argument('--finalize',type=Path);parser.add_argument('--body',type=Path)
    args=parser.parse_args()
    if args.finalize:
        if not args.body:parser.error('--finalize requires --body')
        finalize(args.take,args.finalize,args.body)
    else:prepare(args.take)

