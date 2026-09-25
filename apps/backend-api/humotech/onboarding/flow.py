"""Ознакомление глазами сотрудника: что показать и что записать.

Сюда ходит бот. Прав CRM здесь не спрашивают — их у сотрудника нет
вовсе; право действовать даёт проверенная привязка Telegram, которую
установил вызывающий (`EmployeeContext`). Ни `employee_id`, ни
`organization_id` из запроса не читаются: их негде передать.

Главное правило: **следующий шаг решает сервер.** Бот присылает «я
подтвердил такую-то карточку», а не «покажи мне седьмую». Кнопка в
Telegram живёт в чате вечно, её можно нажать через месяц, из старого
сообщения, дважды подряд, — и если бы порядок держался на ней, человек
перескакивал бы разделы, просто прокрутив чат вверх.

Отсюда же идемпотентность. Двойное нажатие — обычное дело: Telegram
показывает часы, человек жмёт ещё раз. Повтор не должен ни удваивать
запись, ни ломаться: уникальные ключи в базе и `get_or_create` делают
это свойством схемы, а не аккуратности обработчика.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from django.db import transaction
from django.utils import timezone

from humotech.core.errors import Conflict, NotFound
from humotech.core.rbac import AuditTrail
from humotech.onboarding import progress as progress_module
from humotech.onboarding.models import (
    EmployeeOnboarding,
    EmployeePolicyAcceptance,
    EmployeeSectionAcknowledgement,
    OnboardingSection,
    PolicyDocumentVersion,
)
from humotech.onboarding.progress import Progress

ENTITY_ONBOARDING = "employee_onboarding"
ENTITY_ACK = "employee_onboarding_section_acks"
ENTITY_ACCEPTANCE = "employee_policy_acceptances"


class NotEnrolled(NotFound):
    """Сотрудника не звали проходить ознакомление.

    Отдельный класс, чтобы бот мог отличить «не начал» от «не участвует»:
    первому показывают кнопку «Начать», второму — обычное меню.
    """


class OnboardingFlow:
    """Шаги сотрудника. Один экземпляр на запрос, состояния не держит."""

    def __init__(self) -> None:
        self.audit = AuditTrail()

    # ------------------------------------------------------------- чтение

    def _row(self, employee_id: uuid.UUID) -> EmployeeOnboarding:
        row = (
            EmployeeOnboarding.objects.select_related("program")
            .filter(employee_id=employee_id)
            .first()
        )
        if row is None:
            raise NotEnrolled("Ознакомление для вас не назначено")
        return row

    def state(self, employee_id: uuid.UUID) -> Progress:
        """Где человек сейчас. Без записи в базу."""
        return progress_module.compute(self._row(employee_id))

    def state_or_none(self, employee_id: uuid.UUID) -> Progress | None:
        """То же, но молча для тех, кого в программе нет.

        Нужно профилю: он отвечает на вопрос «кто я», и отсутствие
        ознакомления — не повод отказывать в ответе.
        """
        try:
            return self.state(employee_id)
        except NotEnrolled:
            return None

    def section(self, employee_id: uuid.UUID, position: int):
        """Карточка по номеру — для «Назад» и перечитывания из меню.

        Номер, а не идентификатор: так устроены кнопки «← Назад», и так
        короче callback data. Чужую карточку по номеру не достать —
        программа берётся из строки сотрудника.
        """
        progress = self.state(employee_id)
        found = progress.section_at(position)
        if found is None:
            raise NotFound("Раздел не найден")
        return progress, found

    # -------------------------------------------------------------- шаги

    def begin(
        self,
        employee_id: uuid.UUID,
        *,
        message_id: int | None = None,
        now: datetime | None = None,
    ) -> Progress:
        """«Начать ознакомление». Повтор ничего не сбрасывает.

        `started_at` ставится один раз: человек, вернувшийся через
        неделю, начал не сегодня, и подменять дату значило бы стирать
        единственное свидетельство того, сколько он тянул.
        """
        moment = now or timezone.now()
        with transaction.atomic():
            row = EmployeeOnboarding.objects.select_for_update().filter(
                employee_id=employee_id
            ).first()
            if row is None:
                raise NotEnrolled("Ознакомление для вас не назначено")

            changed: list[str] = []
            if row.started_at is None:
                row.started_at = moment
                changed.append("started_at")
            if message_id is not None and row.chat_message_id != message_id:
                row.chat_message_id = message_id
                changed.append("chat_message_id")
            if changed:
                row.save(update_fields=[*changed, "updated_at"])
                if "started_at" in changed:
                    self.audit.record_by_employee(
                        organization_id=row.organization_id,
                        employee_id=employee_id,
                        action="onboarding.start",
                        entity_type=ENTITY_ONBOARDING,
                        entity_id=row.id,
                        after={"started_at": moment.isoformat()},
                    )
            return progress_module.refresh(row, now=moment)

    def acknowledge(
        self,
        employee_id: uuid.UUID,
        section_id: uuid.UUID,
        *,
        telegram_user_id: int | None = None,
        message_id: int | None = None,
        now: datetime | None = None,
    ) -> Progress:
        """«Я ознакомился» с карточкой.

        Порядок проверяется здесь, а не кнопкой: подтвердить можно
        текущую карточку либо любую из уже пройденных (повтор безвреден).
        Прыжок вперёд отклоняется — иначе достаточно было бы открыть
        старое сообщение и нажать не ту кнопку.
        """
        moment = now or timezone.now()
        with transaction.atomic():
            row = EmployeeOnboarding.objects.select_for_update().filter(
                employee_id=employee_id
            ).first()
            if row is None:
                raise NotEnrolled("Ознакомление для вас не назначено")

            section = OnboardingSection.objects.filter(
                id=section_id,
                program_id=row.program_id,
                archived_at__isnull=True,
            ).first()
            if section is None:
                raise NotFound("Раздел не найден")

            before = progress_module.compute(row)
            expected = before.next_section
            already = before.section_at(section.position)
            if already is not None and not already.done:
                if expected is not None and section.position > expected.position:
                    raise Conflict(
                        "Сначала подтвердите предыдущий раздел",
                        details={"expected_position": expected.position},
                    )
                # Двойной тап и повторное открытие старого сообщения
                # приходят сюда же. Ключ в базе решает их молча.
                _, created = EmployeeSectionAcknowledgement.objects.get_or_create(
                    onboarding_id=row.id,
                    section_id=section.id,
                    section_version=section.version,
                    defaults={
                        "organization_id": row.organization_id,
                        "acknowledged_at": moment,
                        "telegram_user_id": telegram_user_id,
                    },
                )
                if created:
                    self.audit.record_by_employee(
                        organization_id=row.organization_id,
                        employee_id=employee_id,
                        action="onboarding.section.acknowledge",
                        entity_type=ENTITY_ACK,
                        entity_id=section.id,
                        after={
                            "position": section.position,
                            "title": section.title,
                            "section_version": section.version,
                            "telegram_user_id": telegram_user_id,
                        },
                    )

            if message_id is not None and row.chat_message_id != message_id:
                row.chat_message_id = message_id
                row.save(update_fields=["chat_message_id", "updated_at"])

            return progress_module.refresh(row, now=moment)

    def decide(
        self,
        employee_id: uuid.UUID,
        version_id: uuid.UUID,
        decision: str,
        *,
        telegram_user_id: int | None = None,
        now: datetime | None = None,
    ) -> Progress:
        """Согласие или отказ по конкретной редакции документа.

        Решение принимается только по ДЕЙСТВУЮЩЕЙ редакции: кнопка под
        старым сообщением после выхода новой версии ничего не подтвердит.
        Иначе согласие с прошлогодним текстом открывало бы доступ поверх
        нового.

        До документов не пускают, пока не прочитаны карточки: порядок
        задан программой, а не тем, какую кнопку человек нашёл в чате.
        """
        if decision not in ("ACCEPTED", "DECLINED"):
            raise NotFound("Неизвестное решение")

        moment = now or timezone.now()
        with transaction.atomic():
            row = EmployeeOnboarding.objects.select_for_update().filter(
                employee_id=employee_id
            ).first()
            if row is None:
                raise NotEnrolled("Ознакомление для вас не назначено")

            before = progress_module.compute(row)
            if not before.info_completed:
                raise Conflict(
                    "Сначала прочитайте информационные разделы",
                    details={
                        "sections_done": before.sections_done,
                        "sections_total": before.sections_total,
                    },
                )

            # Документ, убранный в архив, ни к чему не обязывает: согласие
            # с ним — запись о том, чего от человека уже не требуют.
            version = PolicyDocumentVersion.objects.select_related("document").filter(
                id=version_id,
                organization_id=row.organization_id,
                status="PUBLISHED",
                document__archived_at__isnull=True,
            ).first()
            if version is None:
                raise Conflict(
                    "Эта редакция документа больше не действует: "
                    "откройте актуальную",
                    details={"reason": "version_outdated"},
                )

            acceptance = EmployeePolicyAcceptance.objects.filter(
                employee_id=employee_id, version_id=version.id
            ).first()
            was = acceptance.decision if acceptance else None
            if acceptance is None:
                acceptance = EmployeePolicyAcceptance.objects.create(
                    organization_id=row.organization_id,
                    employee_id=employee_id,
                    version_id=version.id,
                    decision=decision,
                    decided_at=moment,
                    telegram_user_id=telegram_user_id,
                )
            elif acceptance.decision != decision:
                # Передумал. Это одно решение, которое изменилось, а не
                # два разных факта: строка правится, история — в журнале.
                acceptance.decision = decision
                acceptance.decided_at = moment
                acceptance.telegram_user_id = telegram_user_id
                acceptance.save(
                    update_fields=[
                        "decision", "decided_at", "telegram_user_id", "updated_at",
                    ]
                )

            if was != decision:
                self.audit.record_by_employee(
                    organization_id=row.organization_id,
                    employee_id=employee_id,
                    action=(
                        "onboarding.policy.accept" if decision == "ACCEPTED"
                        else "onboarding.policy.decline"
                    ),
                    entity_type=ENTITY_ACCEPTANCE,
                    entity_id=acceptance.id,
                    before={"decision": was} if was else None,
                    after={
                        "decision": decision,
                        "document": version.document.title,
                        "version": version.version,
                        "telegram_user_id": telegram_user_id,
                    },
                )

            return progress_module.refresh(row, now=moment)

    def remember_message(self, employee_id: uuid.UUID, message_id: int) -> None:
        """Запомнить, в каком сообщении сейчас живёт карточка.

        Нужно ровно затем, чтобы не засорять чат: показывая новую
        карточку, бот снимает кнопки с предыдущей. Без этого в истории
        остаются десять сообщений с рабочими кнопками, и человек жмёт
        не то, что видит сейчас.
        """
        EmployeeOnboarding.objects.filter(employee_id=employee_id).update(
            chat_message_id=message_id
        )


__all__ = ["NotEnrolled", "OnboardingFlow"]
