"""Backend policy must never disguise Python work as native measurements."""
from types import SimpleNamespace
import sys
import pytest
from arc_hand_order.planning import execution, multi_finger


@pytest.mark.parametrize('backend,workers', [('bad',0),('python',-1),('native',True),('auto',1.5)])
def test_invalid_execution_options(backend, workers):
    with pytest.raises(ValueError):execution.validate_execution(backend, workers)


def test_auto_remains_reference_until_gate(monkeypatch):
    calls=[];events=[]
    monkeypatch.setattr(execution, 'NATIVE_AUTO_ENABLED', False)
    monkeypatch.setattr(multi_finger,'_plan_python',lambda *a,**kw:calls.append(kw) or 'reference')
    assert execution.plan(None,None,None,None,events.append)=='reference'
    assert len(calls)==1
    assert events[0]['search_backend']=='python' and events[0]['search_workers']==1


def test_direct_python_entry_reports_effective_execution(monkeypatch):
    events = []
    def reference(*args, progress, **kwargs):
        progress({'phase': 'search'})
        return 'reference'
    monkeypatch.setattr(multi_finger, '_plan_python', reference)
    assert multi_finger.plan_multi_finger(None, None, progress=events.append,
        search_backend='python', search_workers=8) == 'reference'
    assert [e['phase'] for e in events] == ['backend', 'search']
    assert all(e['search_backend'] == 'python' and e['search_workers'] == 1 for e in events)


def fake_native(monkeypatch, reasons=()):
    class Unsupported(Exception):pass
    module=SimpleNamespace(availability=lambda:True,unsupported_reasons=lambda *a:list(reasons),NativeUnsupported=Unsupported)
    monkeypatch.setitem(sys.modules,'arc_hand_order.planning.native_search',module)
    # from-package imports can otherwise retain a previously imported attribute.
    import arc_hand_order.planning as package
    monkeypatch.setattr(package,'native_search',module,raising=False)
    return module


def test_native_workers_and_diagnostics(monkeypatch):
    module=fake_native(monkeypatch);events=[]
    def run(*args,progress,workers,**kwargs):
        assert workers==2;progress({'phase':'search'});return 'native result'
    module.plan_items=run
    assert execution.plan(None,None,None,None,events.append,search_backend='native',search_workers=2)=='native result'
    assert all(e['search_backend']=='native' and e['search_workers']==2 for e in events)


def test_auto_uses_native_only_after_rollout_gate(monkeypatch):
    module = fake_native(monkeypatch)
    monkeypatch.setattr(execution, 'NATIVE_AUTO_ENABLED', True)
    module.plan_items = lambda *args, **kwargs: 'native result'
    events = []
    assert execution.plan(None, None, None, None, events.append) == 'native result'
    assert events[0]['search_backend'] == 'native'
    assert events[0]['requested_backend'] == 'auto'


def test_unsupported_is_explicit_reference_fallback(monkeypatch):
    fake_native(monkeypatch,['unsupported scoring feature']);events=[]
    monkeypatch.setattr(multi_finger,'_plan_python',lambda *a,**kw:'reference')
    assert execution.plan(None,None,None,None,events.append,search_backend='native')=='reference'
    assert events[0]['fallback_reason']=='unsupported scoring feature'
    assert events[0]['search_backend']=='python'


def test_runtime_errors_and_cancellation_do_not_fall_back(monkeypatch):
    module=fake_native(monkeypatch)
    def fail(*a,**kw):raise ValueError('invalid native result')
    module.plan_items=fail
    with pytest.raises(ValueError,match='invalid native result'):
        execution.plan(None,None,None,None,search_backend='native')
    def cancel(event):raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        execution.plan(None,None,None,None,cancel,search_backend='native')


def test_automatic_workers_reserve_cpu(monkeypatch):
    monkeypatch.setattr(execution.os,'process_cpu_count',lambda:8,raising=False)
    assert execution.worker_count()==4
    assert execution.worker_count(8)==8
    monkeypatch.setattr(execution.os,'process_cpu_count',lambda:1)
    assert execution.worker_count()==1


def test_unloadable_extension_reports_reference_fallback(monkeypatch):
    from arc_hand_order.planning import native_search
    def broken(name):raise OSError('incompatible architecture')
    monkeypatch.setattr(native_search.importlib,'import_module',broken)
    events=[]
    monkeypatch.setattr(multi_finger,'_plan_python',lambda *a,**kw:'reference')
    assert execution.plan(None,None,None,None,events.append,search_backend='native')=='reference'
    assert events[0]['search_backend']=='python'
    assert 'incompatible architecture' in events[0]['fallback_reason']


@pytest.mark.parametrize('extension,reason', [
    (SimpleNamespace(), 'incompatible search API'),
    (SimpleNamespace(Search=lambda: None), 'missing its implementation identity'),
    (SimpleNamespace(Search=lambda: None, implementation_id=lambda: 'arc-hand-order-native/0.1.0;schema=1'),
     'incompatible input schema'),
])
def test_old_extension_reports_reference_fallback(monkeypatch, extension, reason):
    from arc_hand_order.planning import native_search
    monkeypatch.setattr(native_search.importlib, 'import_module', lambda name: extension)
    monkeypatch.setattr(multi_finger, '_plan_python', lambda *a, **kw: 'reference')
    events = []
    assert execution.plan(None, None, None, None, events.append, search_backend='native') == 'reference'
    assert events[0]['search_backend'] == 'python'
    assert reason in events[0]['fallback_reason']


def test_extension_identity_runtime_errors_are_not_hidden(monkeypatch):
    from arc_hand_order.planning import native_search
    def broken_identity():
        raise RuntimeError('identity failure')
    monkeypatch.setattr(native_search.importlib, 'import_module',
                        lambda name: SimpleNamespace(Search=lambda: None, implementation_id=broken_identity))
    with pytest.raises(RuntimeError, match='identity failure'):
        native_search.availability()


def test_create_plan_without_progress_does_not_hide_native_fallback(tmp_path):
    from plan_excerpt import create_plan
    chart=tmp_path/'fallback.aff';chart.write_text('AudioOffset:0\n-\n(500,1);\n')
    with pytest.warns(RuntimeWarning,match='Native search unavailable; using Python'):
        create_plan(chart,0,1000,{'arc_path_mode':'trace'},search_backend='native')
