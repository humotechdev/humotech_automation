"""Периодические напоминания о незавершённом ознакомлении.

Ознакомление обязательно, но доступа не отбирает: человек отмечается и
подаёт заявки с первого дня. Значит, единственное, чем система может
довести дело до конца, — вовремя напомнить. Отсюда этот модуль.

Четыре правила, и каждое из них про то, чтобы напоминание не
превратилось в назойливость:

  * **писать некуда — не пишем.** Бот не может написать первым, это
    правило Telegram. Без живой привязки сообщение ушло бы в очередь и
    было бы снято там как недоставляемое; кадровик при этом считал бы,
    что напомнил;

  * **отказавшемуся не напоминаем.** Человек, нажавший «Не согласен»,
    ждёт разговора с кадровиком, а не третьего уведомления. Он уже в
    списке требующих внимания;

  * **не ночью.** Напоминание уходит в рабочие часы организации.
    «Дочитайте правила» в три часа ночи — это не напоминание, а
    причина отключить бота;

  * **не чаще, чем раз в несколько дней.** Пауза в настройке, ключ
    идемпотентности содержит дату — даже при сбое расписания в один
    день уйдёт одно сообщение.

Процесс отдельный, как и у напоминаний о начале дня: проход идёт по
всем участникам программы и ходит в базу, и делать это в процессе,
который одновременно отвечает на запросы, значит однажды придержать
интерфейс ради рассылки.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from django.conf import settings
from django.utils import timezone

from humotech.core.timeframes import zone
from humotech.notifications.models import Notification
from humotech.notifications.outbox import enqueue
from humotech.onboarding import progress as progress_module
from humotech.onboarding.models import EmployeeOnboarding
from humotech.telegram.models import TelegramAccount

logger = logging.getLogger("humotech.onboarding.reminders")

ENTITY = "employee_onboarding"
NOTIFICATION_TYPE = "onboarding.reminder"


def _settings() -> dict:
    return settings.ONBOARDING


def text_for(progress: progress_module.Progress) -> str:
    """Что именно написать. Состояние называется своими словами.

    Общее «завершите ознакомление» одинаково обращено и к тому, кто не
    открыл ни одной карточки, и к тому, кому осталось подтвердить один
    документ. Второму такое сообщение говорит, что его труд не заметили.
    """
    if progress.status == "UPDATE_REQUIRED":
        return (
            "Обновился обязательный документ.\n\n"
            "Откройте бота и подтвердите новую редакцию — это одно "
            "нажатие. Раздел «Правила и документы» в меню."
        )
    if not progress.info_completed:
        done = progress.sections_done
        total = progress.sections_total
        if done == 0:
            return (
                "Первичное ознакомление ещё не начато.\n\n"
                f"Это {total} коротких разделов и примерно 10–15 минут. "
                "Нажмите «Продолжить ознакомление» в меню."
            )
        return (
            f"Ознакомление не завершено: пройдено {done} из {total} разделов.\n\n"
            "Прогресс сохранён — продолжите с того места, где остановились."
        )
    left = progress.policies_total - progress.policies_done
    return (
        f"Остались обязательные документы: {left} из "
        f"{progress.policies_total} ещё не подтверждены.\n\n"
        "Откройте бота и подтвердите их — это займёт пару минут."
    )


def due(now: datetime | None = None) -> list[tuple[EmployeeOnboarding, progress_module.Progress, TelegramAccount]]:
    """Кому напомнить прямо сейчас.

    Справочники карточек и документов читаются один раз на организацию,
    а не на человека: проход идёт по всей компании, и запрос на строку
    превратил бы его в тысячу запросов.
    """
    moment = now or timezone.now()
    config = _settings()
    pause = timedelta(hours=config["REMINDER_INTERVAL_HOURS"])
    grace = timedelta(hours=config["REMINDER_GRACE_HOURS"])

    rows = list(
        EmployeeOnboarding.objects
        .select_related("program", "employee", "organization")
        .exclude(status="COMPLETED")
    )
    if not rows:
        return []

    accounts = {
        one.employee_id: one
        for one in TelegramAccount.objects.filter(
            employee_id__in=[row.employee_id for row in rows], status="ACTIVE"
        )
    }

    documents: dict = {}
    sections: dict = {}
    chosen = []
    for row in rows:
        account = accounts.get(row.employee_id)
        if account is None:
            # Писать некуда: человек ещё не открывал бота.
            continue

        started = row.invited_at or row.created_at
        if started is not None and moment - started < grace:
            # Только что пригласили — дать день, прежде чем торопить.
            continue
        if row.last_reminder_at is not None and moment - row.last_reminder_at < pause:
            continue
        if not _working_hours(row.organization, moment):
            continue

        if row.organization_id not in documents:
            documents[row.organization_id] = list(
                progress_module.published_documents(row.organization_id)
            )
        if row.program_id not in sections:
            sections[row.program_id] = list(
                progress_module.active_sections(row.program_id)
            )

        state = progress_module.compute(
            row,
            sections=sections[row.program_id],
            documents=documents[row.organization_id],
        )
        if state.completed or state.has_declined:
            # Закончил либо отказался. Первому напоминать не о чем,
            # второму — бесполезно: он ждёт кадровика.
            continue
        chosen.append((row, state, account))
    return chosen


def _working_hours(organization, moment: datetime) -> bool:
    """Рабочее ли сейчас время в организации.

    Пояс берётся у организации, а не у офиса: напоминание не привязано
    к месту работы, а разница между офисами одной компании — часы, не
    полсуток. День недели не проверяется намеренно: у сервисных групп
    суббота рабочая, и молчать в неё значило бы откладывать напоминание
    на понедельник без причины.
    """
    config = _settings()
    local = moment.astimezone(zone(getattr(organization, "default_timezone", None)))
    return config["REMINDER_FROM_HOUR"] <= local.hour < config["REMINDER_TO_HOUR"]


def run_once(now: datetime | None = None) -> int:
    """Поставить напоминания в очередь. Возвращает, сколько поставлено.

    Считаются именно новые. Очередь на повторный ключ возвращает прежнюю
    строку, а не отказ, — и считать её отправкой значило бы отчитываться
    о сообщениях, которых никто не получал.
    """
    moment = now or timezone.now()
    items = due(moment)
    if not items:
        return 0

    keys = {
        row.id: f"onboarding-reminder:{row.employee_id}:{moment.date().isoformat()}"
        for row, _, _ in items
    }
    already = set(
        Notification.objects.filter(idempotency_key__in=keys.values())
        .values_list("idempotency_key", flat=True)
    )

    sent = 0
    for row, state, _account in items:
        key = keys[row.id]
        if key in already:
            continue
        enqueue(
            organization_id=row.organization_id,
            employee_id=row.employee_id,
            notification_type=NOTIFICATION_TYPE,
            title="Ознакомление не завершено",
            body=text_for(state),
            # Тот же ключ, что и у ручного напоминания кадровика: два
            # сообщения в один день — это одно сообщение, отправленное
            # дважды, и получать его человеку незачем.
            idempotency_key=key,
            related_entity_type=ENTITY,
            related_entity_id=row.id,
        )
        EmployeeOnboarding.objects.filter(pk=row.pk).update(last_reminder_at=moment)
        sent += 1

    if sent:
        logger.info("onboarding reminders queued", extra={"count": sent})
    return sent


__all__ = ["due", "run_once", "text_for"]
