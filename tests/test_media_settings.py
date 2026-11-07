import hashlib
from pathlib import Path
import subprocess
import sys
import wave

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dashboard.media import MEDIA_SETTINGS, mix_gameplay
from dashboard.server import Settings
from plan_cache import request_key
from plan_excerpt import create_plan


def wav(path, samples):
    with wave.open(str(path), 'wb') as out:
        out.setparams((1, 2, 48000, 0, 'NONE', 'not compressed'))
        out.writeframes((samples * 32767).astype('<i2').tobytes())


@pytest.fixture(scope='module')
def stems(tmp_path_factory):
    root = tmp_path_factory.mktemp('media')
    prefix = root / 'native.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=black:s=64x48:r=30:d=1',
                    '-an', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(prefix)+'.base.mp4'], check=True)
    song = np.zeros(5*48000)
    tone = np.sin(2*np.pi*1000*np.arange(2400)/48000) * .3
    song[round(2.4*48000):round(2.4*48000)+len(tone)] = tone
    hit = np.zeros(48000)
    hit[round(.75*48000):round(.75*48000)+len(tone)] = tone
    wav(Path(str(prefix)+'.music.wav'), song)
    wav(Path(str(prefix)+'.hits.wav'), hit)
    return prefix


def decode(path):
    return np.frombuffer(subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(path),
                         '-map', '0:a:0', '-ac', '1', '-ar', '48000', '-f', 'f32le', '-']), dtype='<f4')


def video_hash(path):
    return hashlib.sha256(subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(path),
                           '-map', '0:v:0', '-c:v', 'copy', '-f', 'h264', '-'])).hexdigest()


@pytest.mark.parametrize('delay,expected', [(200,.6),(-200,.2),(0,.4)])
def test_song_delay_sign_and_muted_hits_preserve_video(stems, delay, expected):
    output = stems.parent/f'delay-{delay}.mp4'
    mix_gameplay(stems, output, song_delay_ms=delay, hit_sounds=False)
    samples = decode(output)
    energy = np.abs(samples)
    centroid = np.sum(np.arange(len(samples))*energy)/np.sum(energy)/48000
    assert abs(centroid-(expected+.025)) < .01
    assert np.max(np.abs(samples[int(.72*48000):int(.85*48000)])) < .005
    assert video_hash(output) == video_hash(Path(str(stems)+'.base.mp4'))


def test_hit_toggle_does_not_shift_hit_track_with_song(stems):
    output = stems.parent/'with-hits.mp4'
    mix_gameplay(stems, output, song_delay_ms=200, hit_sounds=True)
    samples = decode(output)
    assert np.max(np.abs(samples[int(.75*48000):int(.8*48000)])) > .1
    assert video_hash(output) == video_hash(Path(str(stems)+'.base.mp4'))


def test_media_settings_reuse_identical_contact_plan(tmp_path):
    chart = tmp_path/'chart.aff';chart.write_text('AudioOffset:0\n-\ntiming(0,120,4);\n(500,1);\n')
    baseline = {'arc_path_mode':'score','max_speed_m_s':4}
    changed = {**baseline,'scroll_speed':8,'song_delay_ms':200,'hit_sounds':False}
    assert set(changed)-set(baseline) == MEDIA_SETTINGS
    assert request_key(chart,0,1000,baseline,False,(),())[0] == request_key(chart,0,1000,changed,False,(),())[0]
    first = create_plan(chart,0,1000,baseline,cache_dir=tmp_path/'cache')
    progress=[]
    second = create_plan(chart,0,1000,changed,cache_dir=tmp_path/'cache',progress=progress.append)
    assert first==second and progress[0]['cache_hit']


def test_media_setting_ranges_and_defaults():
    settings=Settings()
    assert settings.scroll_speed==5 and settings.song_delay_ms==0 and settings.hit_sounds
    for values in ({'scroll_speed':0},{'scroll_speed':11},{'song_delay_ms':2001}):
        with pytest.raises(ValueError):Settings(**values)
