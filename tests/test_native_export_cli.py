"""Exercise the adapter's actual CLI gate and shell failure reporting."""
from pathlib import Path
import os
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_native_cli_accepts_negative_audio_start_but_rejects_invalid_windows(tmp_path):
    if not shutil.which('mcs') or not shutil.which('mono'):
        pytest.skip('Mono is required to exercise the native adapter argument gate')
    source = (ROOT / 'arc-create-export/Export.cs').read_text()
    # Compile the real entry-point body; the engine coroutine is a recording stub.
    start = source.index('    public static void Start(')
    stop = source.index('    private static IEnumerator Guard(', start)
    gate = source[start:stop]
    harness = '''using System; using System.Collections;
class ComposeManager { public bool started; public void StartCoroutine(IEnumerator job) { started=true; } }
static class Ending { public static bool Start(ComposeManager manager) { return false; } }
static class Debug { public static void LogError(string text) {} }
static class Application { public static int code; public static void Quit(int value) { code=value; } }
static class Export {
''' + gate + '''
private static IEnumerator Guard(IEnumerator task,string ffmpeg) { yield break; }
private static IEnumerator Run(string output,int from,int to) { yield break; }
}
class Harness { static void Main() { var manager=new ComposeManager(); Export.Start(manager);
Console.WriteLine(manager.started ? "accepted" : "rejected:"+Application.code); } }
'''
    script = tmp_path/'gate.cs'; script.write_text(harness)
    binary = tmp_path/'gate.exe'
    subprocess.run(['mcs',f'-out:{binary}',str(script)],check=True,capture_output=True)
    def verdict(a,b):
        return subprocess.check_output(['mono',str(binary),'--export-aff-video','out.mp4',str(a),str(b),'/ffmpeg'],text=True).strip()
    assert verdict(-1297,160000) == 'accepted'
    assert verdict(0,160000) == 'accepted'
    assert verdict(56000,160000) == 'accepted'
    for a,b in [(100,100),(200,100),('bad',100),(0,'bad')]:
        assert verdict(a,b) == 'rejected:2'


def test_failed_native_export_reports_cause_instead_of_shutdown_allocation_dump(tmp_path):
    app=tmp_path/'fake.app/Contents/MacOS/ArcCreate'; app.parent.mkdir(parents=True)
    app.write_text('''#!/usr/bin/env python3
import sys
from pathlib import Path
p=Path(sys.argv[sys.argv.index('-logFile')+1])
p.write_text('Expected --export-aff-video OUTPUT START_MS END_MS FFMPEG.\\n' + 'allocation shutdown noise\\n'*200)
sys.exit(2)
''')
    app.chmod(0o755)
    project=tmp_path/'project.arcproj'; project.write_text('')
    replay=tmp_path/'replay.json'; replay.write_text('{}')
    output=tmp_path/'clip.mp4'
    result=subprocess.run([str(ROOT/'arc-create-export/export.sh'),str(project),str(output),'-1297','1000',str(replay)],
                          env={**os.environ,'ARCCREATE_EXPORT_APP':str(tmp_path/'fake.app')},capture_output=True,text=True)
    assert result.returncode != 0
    assert 'RuntimeError: ArcCreate export failed (exit 2)' in result.stderr
    assert 'Expected --export-aff-video' in result.stderr
    assert 'allocation shutdown noise' not in result.stderr
    assert Path(str(output)+'.export.log').read_text().endswith('allocation shutdown noise\n')
