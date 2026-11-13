"""Native interruption never publishes a partial completed-plan entry."""
from pathlib import Path
import pytest
from plan_excerpt import create_plan


def test_callback_cancels_native_without_cache_publish(tmp_path):
    pytest.importorskip('arc_hand_order_native')
    chart=tmp_path/'cancel.aff'
    chart.write_text('AudioOffset:0\n-\ntiming(0,120,4);\n'+''.join(f'({500+i*100},{i%4+1});\n' for i in range(50)))
    events=[]
    def interrupt(event):
        events.append(event)
        if event['phase']=='search':
            assert event['search_backend']=='native'
            raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        create_plan(chart,0,6000,{'arc_path_mode':'score','off_center_contacts':False},
                    cache_dir=tmp_path/'cache',search_backend='native',search_workers=4,progress=interrupt)
    assert not list((tmp_path/'cache').glob('*'))
    assert any(e.get('search_backend')=='native' for e in events)


def test_sigint_interrupts_active_native_layer_without_publishing(tmp_path):
    """Exercise real Python signal polling while Rust owns a long layer."""
    import os
    import subprocess
    import sys
    import textwrap
    if os.name != 'posix':
        pytest.skip('This regression exercises POSIX SIGINT delivery')
    pytest.importorskip('arc_hand_order_native')
    program = textwrap.dedent('''
        import json, os, signal, sys, threading, time
        from pathlib import Path
        import arc_hand_order_native
        from arc_hand_order.planning import native_search
        from plan_excerpt import create_plan

        folder=Path(sys.argv[1]);chart=folder/'signal.aff'
        chart.write_text('AudioOffset:0\\n-\\ntiming(0,120,4);\\n(500,1);\\n')
        original_prepare=native_search._prepared
        def long_layer(*args,**kwargs):
            values=original_prepare(*args,**kwargs)
            # Repeated identical candidates force a long real expansion with
            # bounded geometry. No sleeping/mock replaces native execution.
            values[0]['groups'][0]=values[0]['groups'][0]*250000
            return values
        native_search._prepared=long_layer
        original_search=arc_hand_order_native.Search
        searches=[]
        def capture(*args,**kwargs):
            value=original_search(*args,**kwargs);searches.append(value);return value
        arc_hand_order_native.Search=capture
        timer=None;started=None
        def progress(event):
            global timer,started
            if event['phase']=='search' and timer is None:
                assert event['search_backend']=='native'
                started=time.monotonic()
                timer=threading.Timer(.12,lambda:os.kill(os.getpid(),signal.SIGINT))
                timer.start()
        try:
            create_plan(chart,0,1000,{'arc_path_mode':'score','off_center_contacts':False},
                        cache_dir=folder/'cache',search_backend='native',search_workers=4,
                        progress=progress)
        except KeyboardInterrupt:
            elapsed=time.monotonic()-started
        else:
            raise AssertionError('Long native layer completed without being interrupted')
        finally:
            if timer is not None:timer.cancel();timer.join()
        assert .10<=elapsed<2,elapsed
        assert len(searches)==1
        try:searches[0].result()
        except ValueError as error:assert 'not completed' in str(error)
        else:raise AssertionError('Interrupted layer published a completed result')
        assert not list((folder/'cache').glob('*'))
        print(json.dumps({'interrupted_s':elapsed,'cache_published':False}))
    ''')
    result = subprocess.run([sys.executable, '-c', program, str(tmp_path)],
                            cwd=Path(__file__).resolve().parents[1], capture_output=True,
                            text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
