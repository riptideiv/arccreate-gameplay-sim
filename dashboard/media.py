"""Mix cached native replay stems without changing video or chart timing."""
import argparse
import json
from pathlib import Path
import subprocess
from uuid import uuid4

MEDIA_SETTINGS = frozenset(('scroll_speed', 'song_delay_ms', 'hit_sounds'))
MUSIC_MARGIN_MS = 2000


def mix_gameplay(prefix, output, song_delay_ms=0, hit_sounds=True):
    prefix, output = Path(prefix), Path(output)
    if not -MUSIC_MARGIN_MS <= song_delay_ms <= MUSIC_MARGIN_MS:
        raise ValueError('Song delay must be between -2000 and 2000 ms')
    source = Path(str(prefix) + '.base.mp4')
    duration = float(subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                     '-of', 'default=noprint_wrappers=1:nokey=1', str(source)], text=True).strip())
    start = (MUSIC_MARGIN_MS - song_delay_ms) / 1000
    filters = (f'[1:a]atrim=start={start:.6f},asetpts=PTS-STARTPTS,apad,atrim=duration={duration:.6f}[song];'
               f'[2:a]volume={int(hit_sounds)}[hits];'
               '[song][hits]amix=inputs=2:duration=first:normalize=0[a]')
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.stem + '.' + uuid4().hex + '.tmp.mp4')
    arguments = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-i', str(source)]
    for suffix in ('.music.wav', '.hits.wav'):
        arguments.extend(['-i', str(prefix) + suffix])
    arguments.extend(['-filter_complex', filters, '-map', '0:v:0', '-map', '[a]', '-c:v', 'copy',
                      '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-t', f'{duration:.6f}',
                      '-movflags', '+faststart', str(temporary)])
    try:
        subprocess.run(arguments, check=True)
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('prefix', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--settings', type=Path)
    args = parser.parse_args()
    settings = json.loads(args.settings.read_text()) if args.settings else {}
    mix_gameplay(args.prefix, args.output, **{k: settings[k] for k in
                 ('song_delay_ms', 'hit_sounds') if k in settings})
