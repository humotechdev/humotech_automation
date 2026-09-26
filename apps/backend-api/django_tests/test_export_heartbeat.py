"""Признак жизни исполнителя выгрузок для healthcheck контейнера."""

from __future__ import annotations

import os
import time

import pytest

from humotech.reports import heartbeat


@pytest.fixture(autouse=True)
def heartbeat_in_a_temporary_place(monkeypatch, tmp_path):
    path = tmp_path / "export-worker.heartbeat"
    monkeypatch.setenv(heartbeat.PATH_ENV, str(path))
    monkeypatch.setattr(heartbeat, "_last", 0.0)
    return path


def test_no_file_means_unhealthy(heartbeat_in_a_temporary_place):
    assert heartbeat.main() == 1


def test_a_fresh_beat_is_healthy(heartbeat_in_a_temporary_place):
    heartbeat.beat(force=True)
    assert heartbeat_in_a_temporary_place.exists()
    assert heartbeat.main() == 0


def test_a_stale_beat_is_unhealthy(heartbeat_in_a_temporary_place):
    heartbeat.beat(force=True)
    old = time.time() - heartbeat.MAX_AGE_SECONDS - 5
    os.utime(heartbeat_in_a_temporary_place, (old, old))
    assert heartbeat.main() == 1


def test_frequent_beats_are_throttled(heartbeat_in_a_temporary_place):
    heartbeat.beat(force=True)
    old = time.time() - 60
    os.utime(heartbeat_in_a_temporary_place, (old, old))
    heartbeat.beat()  # сразу после предыдущего — файл не трогается
    assert heartbeat.age_seconds() >= 59


@pytest.mark.django_db
def test_the_worker_loop_beats(heartbeat_in_a_temporary_place, monkeypatch):
    """Один проход исполнителя с `--loop` оставляет свежий признак жизни."""
    from django.core.management import call_command

    from humotech.reports.management.commands import run_export_worker as command

    def stop(_seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(command.time, "sleep", stop)
    with pytest.raises(KeyboardInterrupt):
        call_command("run_export_worker", "--loop=5")
    assert heartbeat.main() == 0
