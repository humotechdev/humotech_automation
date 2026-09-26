"""Признак жизни циклических напоминаний (attendance и onboarding).

Healthcheck раньше проверял только доступ к базе и не замечал ни
зависшего цикла, ни цикла, который крутится на одних исключениях.
Теперь файл трогается только после удачного прохода.
"""

from __future__ import annotations

import os
import time

import pytest
from django.core.management import call_command

from humotech.attendance import heartbeat
from humotech.attendance.management.commands import attendance_reminders
from humotech.onboarding.management.commands import onboarding_reminders


class StopLoop(Exception):
    pass


def _age(path) -> float:
    return time.time() - path.stat().st_mtime


# ------------------------------------------------------------------ модуль


def test_successful_tick_touches_file(tmp_path):
    path = tmp_path / "hb"
    assert heartbeat.tick(lambda: 3, path=path, what="t") is True
    assert path.exists()


def test_failed_tick_does_not_touch_file_and_does_not_raise(tmp_path):
    path = tmp_path / "hb"

    def boom():
        raise RuntimeError("база недоступна")

    assert heartbeat.tick(boom, path=path, what="t") is False
    assert not path.exists()


def test_loop_survives_exceptions_and_beats_after_recovery(tmp_path):
    path = tmp_path / "hb"
    calls = {"n": 0}

    def step():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("временный сбой")

    heartbeat.run_loop(step, pause=1, path=path, what="t",
                       sleep=lambda _: None, iterations=4)
    assert calls["n"] == 4
    assert path.exists()


def test_unwritable_heartbeat_does_not_break_the_loop(tmp_path):
    path = tmp_path / "missing-dir" / "hb"
    heartbeat.run_loop(lambda: None, pause=1, path=path, what="t",
                       sleep=lambda _: None, iterations=2)
    assert not path.exists()


def test_healthcheck_cli(tmp_path, capsys):
    path = tmp_path / "hb"
    assert heartbeat.main([str(path), "900"]) == 1  # файла нет

    path.touch()
    assert heartbeat.main([str(path), "900"]) == 0

    old = time.time() - 1000
    os.utime(path, (old, old))
    assert heartbeat.main([str(path), "900"]) == 1

    assert heartbeat.main([str(path)]) == 2
    assert heartbeat.main([str(path), "abc"]) == 2


def test_max_age_covers_two_pauses():
    assert heartbeat.max_age_for(300) == 900
    assert heartbeat.max_age_for(3600) == 7500


# ---------------------------------------------------------------- команды


@pytest.mark.parametrize("module,env,command", [
    (attendance_reminders, "ATTENDANCE_REMINDERS_HEARTBEAT", "attendance_reminders"),
    (onboarding_reminders, "ONBOARDING_REMINDERS_HEARTBEAT", "onboarding_reminders"),
])
class TestCommandLoop:
    def _run(self, monkeypatch, module, command, results, passes):
        """Прогнать `--loop` ровно `passes` оборотов."""
        outcomes = iter(results)

        def fake_run_once():
            value = next(outcomes)
            if isinstance(value, Exception):
                raise value
            return value

        sleeps = {"n": 0}

        def fake_sleep(_seconds):
            sleeps["n"] += 1
            if sleeps["n"] >= passes:
                raise StopLoop

        monkeypatch.setattr(module, "run_once", fake_run_once)
        monkeypatch.setattr(module.time, "sleep", fake_sleep)
        with pytest.raises(StopLoop):
            call_command(command, loop=60)

    def test_beats_after_successful_pass(
        self, tmp_path, monkeypatch, module, env, command
    ):
        path = tmp_path / "hb"
        monkeypatch.setenv(env, str(path))
        self._run(monkeypatch, module, command, [0], passes=1)
        assert path.exists()
        assert _age(path) < 60

    def test_failing_passes_leave_heartbeat_stale_but_loop_alive(
        self, tmp_path, monkeypatch, module, env, command
    ):
        path = tmp_path / "hb"
        monkeypatch.setenv(env, str(path))
        # Три оборота подряд падают: цикл жив (дошёл до третьей паузы),
        # но признака жизни нет — healthcheck увидит unhealthy.
        self._run(monkeypatch, module, command,
                  [RuntimeError("x"), RuntimeError("y"), RuntimeError("z")],
                  passes=3)
        assert not path.exists()

    def test_heartbeat_resumes_after_recovery(
        self, tmp_path, monkeypatch, module, env, command
    ):
        path = tmp_path / "hb"
        monkeypatch.setenv(env, str(path))
        self._run(monkeypatch, module, command, [RuntimeError("x"), 2], passes=2)
        assert path.exists()

    def test_single_pass_mode_is_unchanged(
        self, tmp_path, monkeypatch, module, env, command
    ):
        path = tmp_path / "hb"
        monkeypatch.setenv(env, str(path))
        monkeypatch.setattr(module, "run_once", lambda: 5)
        call_command(command)
        assert not path.exists()
