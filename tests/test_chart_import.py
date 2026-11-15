import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import import_charts
from dashboard.chart_library import chart_paths, load_catalog
from dashboard import server


def collection(tmp_path):
    pack = tmp_path / 'charts/imported/test-pack'
    song = pack / 'example'
    song.mkdir(parents=True)
    (song / 'songlist').write_text(json.dumps({'songs': [{
        'id': 'example', 'title_localized': {'en': 'Example: "Song"'}, 'artist': 'Artist',
        'bpm_base': 180, 'bpm': '180', 'difficulties': [
            {'ratingClass': 0, 'rating': 5},
            {'ratingClass': 3, 'rating': 11, 'audioOverride': True, 'jacketOverride': True,
             'title_localized': {'en': 'Example remix'}}]}]}))
    for name in ('base.ogg', '3.ogg', '1080_base.jpg', '1080_3.jpg'):
        (song / name).write_bytes(b'local fixture')
    (song / '0.aff').write_text('AudioOffset:250\n-\ntiming(0,180,4);\n(1000,1);\n')
    (song / '3.aff').write_text('AudioOffset:0\n-\ntiming(0,180,4);\narc(0,1000,0,1,s,0,1,0,none,designant);\n')
    return pack


def test_bulk_import_preserves_sources_and_is_repeatable(tmp_path, monkeypatch):
    pack = collection(tmp_path)
    original = {p: p.read_bytes() for p in pack.rglob('*') if p.is_file()}
    monkeypatch.setattr(import_charts, 'audio_duration', lambda path: 12 if path.name == '3.ogg' else 10)
    result = import_charts.import_collection(pack, tmp_path)
    assert (result['songs'], result['charts'], result['available']) == (1, 2, 2)
    assert all(path.read_bytes() == data for path, data in original.items())
    charts = load_catalog(tmp_path)
    easy = charts['test-pack--example--0']
    hard = charts['test-pack--example--3']
    assert easy['duration'] == 9.75
    assert hard['duration'] == 12
    assert hard['available'] is True
    assert hard['unavailable_reason'] is None
    directory, aff, project = chart_paths(tmp_path, 'test-pack--example--3', hard)
    assert aff.is_symlink() and aff.read_bytes() == (pack/'example/3.aff').read_bytes()
    text = project.read_text()
    assert 'chartPath: "3.aff"' in text
    assert 'audioPath: "3.ogg"' in text
    assert 'jacketPath: "1080_3.jpg"' in text
    assert 'title: "Example remix"' in text
    before = (tmp_path/'charts/imported/catalog.json').read_bytes()
    assert import_charts.import_collection(pack, tmp_path) == result
    assert (tmp_path/'charts/imported/catalog.json').read_bytes() == before


def test_import_failure_does_not_publish_partial_catalog(tmp_path, monkeypatch):
    pack = collection(tmp_path)
    (pack/'example/3.ogg').unlink()
    monkeypatch.setattr(import_charts, 'audio_duration', lambda path: 10)
    with pytest.raises(ValueError, match='Missing or invalid asset'):
        import_charts.import_collection(pack, tmp_path)
    assert not (tmp_path/'charts/imported/catalog.json').exists()


def test_settings_accept_catalog_charts_and_reject_unknown_or_unavailable(monkeypatch):
    monkeypatch.setattr(server, 'CHARTS', {'imported': {'duration': 20},
                        'unsupported': {'duration': 20, 'available': False, 'unavailable_reason': 'unsupported arc type'}})
    assert server.Settings(chart='imported').chart == 'imported'
    with pytest.raises(ValueError, match='Choose a chart'):
        server.Settings(chart='../../etc/passwd')
    with pytest.raises(ValueError, match='unsupported arc type'):
        server.Settings(chart='unsupported')


def test_chart_paths_cannot_escape_library(tmp_path):
    with pytest.raises(ValueError, match='inside charts/imported'):
        chart_paths(tmp_path, 'bad', {'directory': '../outside'})
    with pytest.raises(ValueError, match='inside charts/imported'):
        chart_paths(tmp_path, 'bad', {'aff': '../../../../outside.aff'})
