"""Waiting jobs retain their settings and share one render worker."""
import threading

import pytest

from dashboard import server


def test_generation_queue_advances_after_failure_and_can_remove_waiting_job(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "RUNS", tmp_path)
    queue = server.RenderQueue()
    release = threading.Event()
    processed = []

    def work(settings):
        processed.append(settings.lift_mm)
        if settings.lift_mm == 6:
            assert release.wait(5)
            queue.update(status="failed")
        else:
            queue.update(status="complete")

    monkeypatch.setattr(queue, "work", work)
    first = queue.submit(server.Settings(lift_mm=6))
    removed = queue.submit(server.Settings(lift_mm=7))
    next_run = queue.submit(server.Settings(lift_mm=8))
    assert first["status"] == "running"
    assert [run["id"] for run in queue.state()["queue"]] == [removed["id"], next_run["id"]]
    queue.cancel(removed["id"], queued_only=True)
    with pytest.raises(ValueError, match="no longer waiting"):
        queue.cancel(first["id"], queued_only=True)
    first_worker = queue.worker
    release.set()
    first_worker.join(5)
    if queue.worker:
        queue.worker.join(5)

    assert processed == [6, 8]
    assert queue.state()["queue"] == []
    assert {run["id"]: run["status"] for run in queue.state()["history"]} == {
        first["id"]: "failed", removed["id"]: "cancelled", next_run["id"]: "complete",
    }


def test_queue_restores_waiting_jobs_in_submission_order(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "RUNS", tmp_path)
    queue = server.RenderQueue()
    for run_id, created, status in (("z", 1, "queued"), ("a", 2, "queued"), ("old", 0, "running")):
        directory = tmp_path / run_id
        directory.mkdir()
        server.write_json(directory / "run.json", {
            "id": run_id, "created": created, "status": status,
            "settings": server.Settings().model_dump(),
        })
    waiting = threading.Event()
    monkeypatch.setattr(queue, "work", lambda settings: waiting.wait(5))
    queue.resume()
    try:
        state = queue.state()
        assert state["active"]["id"] == "z"
        assert [run["id"] for run in state["queue"]] == ["a"]
        assert state["history"][0]["status"] == "failed"
        queue.cancel("a", queued_only=True)
    finally:
        worker = queue.worker
        waiting.set()
        if worker:
            worker.join(5)
