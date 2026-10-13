"""Очередь заявок: вкладки и фильтр статуса делят заявки без пересечений.

Пять групп, которые кадровик различает и которые раньше слипались:

* **ожидают документа** — больничный создан, справку ещё не приносили;
* **на рассмотрении** — ждут решения кадровика: справка у него, или
  бумаг не нужно вовсе (отпуск, исправление отметки);
* **исправление** — справку вернули, ждут новую от сотрудника;
* **отклонённые** — окончательный отказ кадровика;
* одобренные и отозванные — своими пунктами.

Вкладка «Требуют решения» = «на рассмотрении», вкладка «Исправления» —
исправления отметок и больничные с возвращённой справкой, вкладка
«Отклонённые» — только отказ. Счётчики вкладок считаются тем же отбором.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from django_tests.test_absences import (  # noqa: F401 — фикстуры
    annual_leave,
    balance,
    certificate,
    context,
    sick_leave,
)
from django_tests.test_home_attention import board, queue_client  # noqa: F401
from humotech.absences.models import AbsenceRequest
from humotech.attendance.models import AttendanceCorrectionRequest

API = "/api/v1"
OPEN = "SUBMITTED,IN_REVIEW"

pytestmark = pytest.mark.django_db


@pytest.fixture()
def everything(board, organization, employee):
    """Доска больничных и отпусков, плюс исправления отметок и отказ по отпуску."""
    def correction(status: str):
        return AttendanceCorrectionRequest.objects.create(
            organization=organization, employee=employee,
            requested_entry_at=datetime(2026, 3, 10, 4, tzinfo=timezone.utc),
            reason="Забыл отметиться", status=status,
            submitted_at=datetime(2026, 3, 10, 14, tzinfo=timezone.utc),
        )

    rows = dict(board)
    rows["corr"] = correction("SUBMITTED")
    rows["corr-rejected"] = correction("REJECTED")
    return rows


def _names(answer, rows) -> set[str]:
    assert answer.status_code == 200, answer.content
    back = {str(one.id): name for name, one in rows.items()}
    return {back[row["id"]] for row in answer.json()["items"]}


# Вкладки — параметрами, которыми их зовёт CRM.
TABS = {
    "open": f"status={OPEN}&stage=HR_REVIEW,PENDING",
    "fixes": "kind=fixes",
    "rejected": "status=REJECTED",
    "sick": "kind=absence&type=SICK_LEAVE&request_kind=CREATE,EXTEND",
    "leave": "kind=absence&type=ANNUAL_LEAVE,UNPAID_LEAVE&request_kind=CREATE,EXTEND",
}


class TestTabs:
    def test_open_tab_waits_for_hr_only(self, queue_client, everything):
        """Возвращённая справка и ещё не принесённая — не решение кадровика."""
        answer = queue_client.get(f"{API}/requests?{TABS['open']}")

        assert _names(answer, everything) == {"review", "verified", "leave", "corr"}

    def test_fixes_tab_holds_everything_to_fix(self, queue_client, everything):
        answer = queue_client.get(f"{API}/requests?{TABS['fixes']}")

        assert _names(answer, everything) == {"fix", "corr", "corr-rejected"}

    def test_rejected_tab_is_the_final_refusal(self, queue_client, everything):
        """Отказ кадровика — здесь; возвращённая на исправление — нет."""
        answer = queue_client.get(f"{API}/requests?{TABS['rejected']}")

        assert _names(answer, everything) == {"decided", "corr-rejected"}

    def test_counts_match_the_tabs(self, queue_client, everything):
        counts = queue_client.get(f"{API}/requests/counts").json()

        # Вкладка «Требуют решения» считается ключом `decide`: `open` —
        # все нерешённые, его читает карточка сотрудника.
        keys = {"open": "decide"}
        for tab, params in TABS.items():
            listed = queue_client.get(f"{API}/requests?{params}&limit=100")
            assert counts[keys.get(tab, tab)] == len(listed.json()["items"]), tab
        assert counts["open"] == 6  # ждут справку и с возвращённой тоже не решены
        assert "cancel" not in counts


# Фильтр статуса: пункт → stage, как его отправляет CRM.
GROUPS = {
    "waiting": ("WAITING_DOCUMENTS", {"waiting"}),
    "review": ("HR_REVIEW,PENDING", {"review", "verified", "leave", "corr"}),
    "fix": ("NEEDS_FIX", {"fix"}),
    "rejected": ("REJECTED", {"decided", "corr-rejected"}),
}


class TestStatusFilter:
    @pytest.mark.parametrize("group", list(GROUPS))
    def test_each_group_only_in_its_filter(self, queue_client, everything, group):
        stage, expected = GROUPS[group]

        answer = queue_client.get(f"{API}/requests?stage={stage}")

        assert _names(answer, everything) == expected

    def test_groups_do_not_overlap(self, queue_client, everything):
        seen: dict[str, str] = {}
        for group, (stage, _) in GROUPS.items():
            for name in _names(queue_client.get(f"{API}/requests?stage={stage}"), everything):
                assert name not in seen, f"{name}: {seen.get(name)} и {group}"
                seen[name] = group

    def test_leave_tab_with_review_filter(self, queue_client, everything):
        """Отпуска: «на рассмотрении» — открытые, решённый туда не попадает."""
        answer = queue_client.get(f"{API}/requests?{TABS['leave']}&stage=HR_REVIEW,PENDING")

        assert _names(answer, everything) == {"leave"}

    def test_fixes_tab_with_review_filter_is_open_corrections(self, queue_client, everything):
        """Так открывается «Исправления отметок» с главной."""
        answer = queue_client.get(f"{API}/requests?{TABS['fixes']}&stage=HR_REVIEW,PENDING")

        assert _names(answer, everything) == {"corr"}

    def test_cancelled_is_not_a_refusal(self, queue_client, everything):
        AbsenceRequest.objects.filter(pk=everything["leave"].pk).update(status="CANCELLED")

        rejected = queue_client.get(f"{API}/requests?{TABS['rejected']}")
        withdrawn = queue_client.get(f"{API}/requests?stage=CANCELLED")

        assert "leave" not in _names(rejected, everything)
        assert _names(withdrawn, everything) == {"leave"}
