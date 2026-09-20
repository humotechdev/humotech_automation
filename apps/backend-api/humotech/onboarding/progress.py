"""Сколько пройдено и открыт ли доступ. Единственное место, где это решают.

Правило, ради которого модуль существует: **состояние вычисляется, а не
читается из колонки.** `employee_onboarding.status` хранится и
обновляется, но он — витрина для списков и фильтров. Стоит гейту начать
смотреть на него, и выпуск новой редакции обязательного документа
перестанет кого-либо закрывать: у всех, кто прошёл ознакомление год
назад, в колонке навсегда останется COMPLETED.

Что именно считается завершением:

  * подтверждена каждая ДЕЙСТВУЮЩАЯ информационная карточка. Редакция
    карточки при этом неважна: карточка сообщает, а не обязывает, и
    правка опечатки не должна возвращать сто человек к чтению;
  * по каждому обязательному документу есть согласие с его ТЕКУЩЕЙ
    опубликованной редакцией. Здесь редакция важна ровно наоборот:
    согласие относится к формулировке, и новая формулировка требует
    нового согласия.

Документ без опубликованной редакции не требует ничего: соглашаться
не с чем. Черновик в CRM не закрывает бота никому — иначе половина
компании оставалась бы без доступа, пока юрист правит запятую.

Кто под гейтом. Только те, у кого есть строка `employee_onboarding`, то
есть кого в программу позвали. Сотрудники, работавшие до появления
раздела, продолжают работать; включить их в программу — отдельное
решение кадровика, а не побочный эффект выката.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from django.db.models import Prefetch
from django.utils import timezone

from humotech.onboarding.models import (
    EmployeeOnboarding,
    EmployeePolicyAcceptance,
    EmployeeSectionAcknowledgement,
    OnboardingSection,
    PolicyDocument,
    PolicyDocumentVersion,
)


@dataclass(frozen=True)
class SectionState:
    """Одна карточка глазами конкретного сотрудника."""

    section: OnboardingSection
    acknowledged_at: datetime | None
    acknowledged_version: int | None

    @property
    def done(self) -> bool:
        return self.acknowledged_at is not None


@dataclass(frozen=True)
class PolicyState:
    """Один обязательный документ глазами конкретного сотрудника.

    `version` — действующая опубликованная редакция. `None` означает,
    что документ заведён, но ещё не выпущен: подтверждать нечего.
    """

    document: PolicyDocument
    version: PolicyDocumentVersion | None
    decision: str | None
    decided_at: datetime | None

    @property
    def required(self) -> bool:
        """Требует ли документ решения прямо сейчас."""
        return self.document.is_mandatory and self.version is not None

    @property
    def accepted(self) -> bool:
        return self.decision == "ACCEPTED"

    @property
    def declined(self) -> bool:
        return self.decision == "DECLINED"


@dataclass(frozen=True)
class Progress:
    """Полное состояние ознакомления одного человека.

    Собирается из базы целиком и отвечает на все вопросы сразу: и
    «сколько пройдено» для списка HR, и «пускать ли» для гейта, и «что
    показать дальше» для бота. Три отдельных запроса на эти три вопроса
    неизбежно разошлись бы.
    """

    onboarding: EmployeeOnboarding
    sections: tuple[SectionState, ...]
    policies: tuple[PolicyState, ...]

    # ---------------------------------------------------------- счётчики

    @property
    def sections_total(self) -> int:
        return len(self.sections)

    @property
    def sections_done(self) -> int:
        return sum(1 for one in self.sections if one.done)

    @property
    def required_policies(self) -> tuple[PolicyState, ...]:
        return tuple(one for one in self.policies if one.required)

    @property
    def policies_total(self) -> int:
        return len(self.required_policies)

    @property
    def policies_done(self) -> int:
        return sum(1 for one in self.required_policies if one.accepted)

    # ------------------------------------------------------------ выводы

    @property
    def info_completed(self) -> bool:
        return self.sections_done >= self.sections_total

    @property
    def has_declined(self) -> bool:
        return any(one.declined for one in self.required_policies)

    @property
    def completed(self) -> bool:
        """То самое условие, по которому открывается бот."""
        return self.info_completed and self.policies_done >= self.policies_total

    @property
    def status(self) -> str:
        """Как это называется в списке HR.

        Отказ показывается раньше всего остального: человек, отказавшийся
        подтвердить правила, «на седьмой карточке из десяти» не находится —
        он ждёт разговора с кадровиком.
        """
        if self.has_declined:
            return "BLOCKED_BY_DECLINED_POLICY"
        if self.completed:
            return "COMPLETED"
        if self.info_completed:
            return "POLICIES_IN_PROGRESS" if self.policies_done else "INFO_COMPLETED"
        if self.sections_done or self.onboarding.started_at is not None:
            return "IN_PROGRESS"
        return "NOT_STARTED"

    # ----------------------------------------------------- что дальше

    @property
    def next_section(self) -> OnboardingSection | None:
        """Первая неподтверждённая карточка — та, на которой остановились."""
        for one in self.sections:
            if not one.done:
                return one.section
        return None

    @property
    def next_policy(self) -> PolicyState | None:
        """Первый документ, по которому нет согласия."""
        for one in self.required_policies:
            if not one.accepted:
                return one
        return None

    @property
    def stage(self) -> str:
        """Что показывать человеку: карточки, документы или меню.

        Три значения вместо шести статусов: боту нужно выбрать экран, а
        не назвать состояние. `BLOCKED` от `POLICIES` отличается тем,
        что кнопки согласия в нём не ведут вперёд сами по себе — там
        уже решает кадровик.
        """
        if self.completed:
            return "DONE"
        if not self.info_completed:
            return "SECTIONS"
        return "BLOCKED" if self.has_declined else "POLICIES"

    def section_at(self, position: int) -> SectionState | None:
        for one in self.sections:
            if one.section.position == position:
                return one
        return None


# --------------------------------------------------------------- сборка


def active_sections(program_id: uuid.UUID):
    return (
        OnboardingSection.objects.filter(
            program_id=program_id, archived_at__isnull=True
        )
        .order_by("position")
    )


def published_documents(organization_id: uuid.UUID):
    """Документы организации с их действующей редакцией, по порядку.

    Опубликованная редакция подтягивается `Prefetch`-ем, а не отдельным
    запросом на документ: список HR показывает всю компанию сразу, и
    запрос на строку превратил бы страницу в сотню запросов.
    """
    return (
        PolicyDocument.objects.filter(
            organization_id=organization_id, archived_at__isnull=True
        )
        .prefetch_related(
            Prefetch(
                "versions",
                queryset=PolicyDocumentVersion.objects.filter(status="PUBLISHED"),
                to_attr="published",
            )
        )
        .order_by("position", "created_at")
    )


def compute(
    onboarding: EmployeeOnboarding,
    *,
    sections: list[OnboardingSection] | None = None,
    documents: list[PolicyDocument] | None = None,
) -> Progress:
    """Состояние одного сотрудника.

    `sections` и `documents` можно передать снаружи — список HR читает
    их один раз на всю страницу. Без них они читаются здесь: для
    одиночного запроса это дешевле, чем обязанность вызывающего
    помнить про два справочника.
    """
    rows = (
        list(active_sections(onboarding.program_id))
        if sections is None else sections
    )
    docs = (
        list(published_documents(onboarding.organization_id))
        if documents is None else documents
    )

    acks = {
        row.section_id: row
        for row in EmployeeSectionAcknowledgement.objects.filter(
            onboarding_id=onboarding.id
        ).order_by("acknowledged_at")
    }
    section_states = tuple(
        SectionState(
            section=row,
            acknowledged_at=(
                acks[row.id].acknowledged_at if row.id in acks else None
            ),
            acknowledged_version=(
                acks[row.id].section_version if row.id in acks else None
            ),
        )
        for row in rows
    )

    live_versions = {
        document.id: (getattr(document, "published", None) or [None])[0]
        for document in docs
    }
    version_ids = [one.id for one in live_versions.values() if one is not None]
    decisions = {
        row.version_id: row
        for row in EmployeePolicyAcceptance.objects.filter(
            employee_id=onboarding.employee_id, version_id__in=version_ids
        )
    }
    policy_states = tuple(
        PolicyState(
            document=document,
            version=live_versions[document.id],
            decision=(
                decisions[live_versions[document.id].id].decision
                if live_versions[document.id] is not None
                and live_versions[document.id].id in decisions
                else None
            ),
            decided_at=(
                decisions[live_versions[document.id].id].decided_at
                if live_versions[document.id] is not None
                and live_versions[document.id].id in decisions
                else None
            ),
        )
        for document in docs
    )

    return Progress(
        onboarding=onboarding, sections=section_states, policies=policy_states
    )


def refresh(
    onboarding: EmployeeOnboarding,
    *,
    now: datetime | None = None,
    sections: list[OnboardingSection] | None = None,
    documents: list[PolicyDocument] | None = None,
) -> Progress:
    """Пересчитать и записать витрину: статус и три отметки времени.

    Отметки ставятся один раз и больше не двигаются. Выпуск новой
    редакции документа возвращает человека к подтверждению, но дату, когда
    он впервые закончил ознакомление, это не отменяет: это состоявшийся
    факт, а не текущее состояние.
    """
    progress = compute(onboarding, sections=sections, documents=documents)
    moment = now or timezone.now()

    changed: list[str] = []
    status = progress.status
    if onboarding.status != status:
        onboarding.status = status
        changed.append("status")
    if progress.info_completed and onboarding.info_completed_at is None:
        onboarding.info_completed_at = moment
        changed.append("info_completed_at")
    if progress.completed and onboarding.completed_at is None:
        onboarding.completed_at = moment
        changed.append("completed_at")

    if changed:
        onboarding.save(update_fields=[*changed, "updated_at"])
    return progress


# ------------------------------------------------------------------ гейт


def gate(employee_id: uuid.UUID) -> Progress | None:
    """Состояние для проверки допуска. `None` — человек вне программы.

    Отдельная функция, а не «позови compute», чтобы у гейта был один
    вход и его нельзя было случайно обойти, забыв проверить отсутствие
    строки.
    """
    onboarding = (
        EmployeeOnboarding.objects.select_related("program")
        .filter(employee_id=employee_id)
        .first()
    )
    if onboarding is None:
        return None
    return compute(onboarding)


def is_blocked(employee_id: uuid.UUID) -> bool:
    """Закрыты ли рабочие действия сотруднику прямо сейчас."""
    progress = gate(employee_id)
    return progress is not None and not progress.completed


__all__ = [
    "PolicyState",
    "Progress",
    "SectionState",
    "active_sections",
    "compute",
    "gate",
    "is_blocked",
    "published_documents",
    "refresh",
]
