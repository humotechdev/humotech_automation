"""Очереди витрины: отсутствия, заявки, документы, Telegram, обращения.

Отдельно от команды, потому что это другая история: команда отвечает на
вопрос «кто работает», этот модуль — «что лежит в очередях». Держать то
и другое в одном файле означало бы, что ни того, ни другого не видно.

Ни одного статуса, которого нет в `humotech.core.enums`. Где состояния
нет — его здесь нет тоже: например, отдельного «отменено сотрудником» у
заявки не существует, отмена это `request_kind='CANCEL'` со своим
решением, и именно так она и заводится.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile

from humotech.absences.models import (
    AbsenceAction,
    AbsenceDocument,
    AbsenceRequest,
    EmployeeAbsence,
)
from humotech.attendance.models import AttendanceCorrectionRequest, AttendanceSession
from humotech.core.demo import images, papers as paper_texts, photos as photo_pack
from humotech.core.demo.catalog import (
    DEMO_SOURCES,
    NOTICES,
    PREFIX,
    QUESTIONS,
    DemoQuestion,
)
from humotech.employees.models import Employee, EmployeeDocument
from humotech.files.models import File
from humotech.files.storage import private_storage, store
from humotech.knowledge.models import KnowledgeSource
from humotech.notifications.models import Notification
from humotech.questions.inbox import REPLY_HEADER, SLA, _person, next_number
from humotech.questions.models import EmployeeQuestion, QuestionMessage
from humotech.telegram.models import TelegramAccount, TelegramLinkInvitation

#: Каталог приватного хранилища, в который витрина кладёт свои файлы.
#: По нему же она их и убирает: удалять чужое она права не имеет.
FILE_PREFIX = "demo"

WORK_START = time(9, 0)
WORK_END = time(18, 0)


def _at(day: date, moment: time, tz) -> datetime:
    return datetime.combine(day, moment).replace(tzinfo=tz)


def _pick(people, state: str, at: int):
    """Человек в нужном состоянии по порядку, а не наугад.

    Порядок важен: при повторном запуске заявка обязана достаться тому
    же сотруднику, иначе «повтор ничего не меняет» перестанет быть
    правдой.
    """
    pool = [one for one in people if one.state == state and one.scenario is None]
    return pool[at % len(pool)]


# --- отсутствия и заявки -----------------------------------------------------


def absences(org, people, types, stage: date, tz, reviewer) -> list[EmployeeAbsence]:
    """Отпуска и больничные плюс очередь заявок во всех состояниях.

    У каждого отсутствия есть своя заявка: в модели `origin_request`
    обязателен, и это не формальность — отсутствие появляется решением
    по заявке, а не само по себе.

    Состояния заявок берутся из `ABSENCE_REQUEST_STATUSES` целиком:
    черновик, отправлена, на рассмотрении, одобрена, отклонена,
    отменена. Отмена — это отдельная заявка вида `CANCEL` со ссылкой на
    исходную: база требует родителя, и придумывать отмену другим
    статусом значило бы соврать о процессе.
    """
    leave, sick, unpaid = types["ANNUAL_LEAVE"], types["SICK_LEAVE"], types["UNPAID_LEAVE"]
    rows: list[EmployeeAbsence] = []

    # 1. Действующие сегодня: отпуск и больничный. Они и дают числа
    #    «в отпуске» и «на больничном» на главной.
    for state, kind in (("vacation", leave), ("sick", sick)):
        bucket = [one for one in people if one.state == state]
        for at, person in enumerate(bucket):
            span = (5, 1, 6) if state == "vacation" else (3, 0, 3)
            period, before, after = span
            offset = at % period
            start = _at(stage - timedelta(days=offset + before), WORK_START, tz)
            end = _at(stage + timedelta(days=after - offset), WORK_END, tz)
            request = _request(
                org, person, kind, start, end, "APPROVED", reviewer,
                comment="Отпуск по графику." if state == "vacation" else "Больничный лист.",
            )
            rows.append(EmployeeAbsence(
                organization=org,
                employee=person.employee,
                absence_type=kind,
                origin_request=request,
                start_at=start,
                end_at=end,
                status="ACTIVE",
            ))

    # 2. Очередь: заявки, по которым решение ещё не принято или уже
    #    принято, но отсутствие сегодня не действует.
    queue = [
        # (состояние, вид отсутствия, сдвиг начала в днях, длина, комментарий)
        ("DRAFT", leave, 30, 5, "Черновик: даты ещё уточняются."),
        ("SUBMITTED", leave, 21, 7, "Прошу отпуск на неделю."),
        ("SUBMITTED", leave, 35, 10, "Отпуск по семейным обстоятельствам."),
        ("IN_REVIEW", leave, 18, 4, "Взято на рассмотрение руководителем."),
        ("IN_REVIEW", unpaid, 12, 2, "Отпуск за свой счёт на два дня."),
        ("REJECTED", leave, 9, 14, "Две недели в высокий сезон."),
        ("SUBMITTED", sick, -2, 3, "Больничный, справка будет позже."),
        ("SUBMITTED", unpaid, 16, 1, "Один день за свой счёт."),
        ("IN_REVIEW", sick, -4, 4, "Больничный, справка приложена."),
        ("REJECTED", unpaid, 6, 5, "Отпуск за свой счёт на неделю."),
        # Сотрудник отозвал заявку сам, до решения. Отдельного «отозвано»
        # у модели нет — это и есть CANCELLED.
        ("CANCELLED", leave, 28, 6, "Планы изменились, заявку отзываю."),
    ]
    for at, (status, kind, shift, length, comment) in enumerate(queue):
        person = _pick(people, "in_office", at)
        start = _at(stage + timedelta(days=shift), WORK_START, tz)
        end = _at(stage + timedelta(days=shift + length), WORK_END, tz)
        _request(org, person, kind, start, end, status, reviewer, comment=comment)

    # 3. Будущий одобренный отпуск: отсутствие заведено, но ещё не
    #    началось. `PLANNED` — именно это состояние.
    for at in range(3):
        person = _pick(people, "left", at)
        start = _at(stage + timedelta(days=14 + at * 5), WORK_START, tz)
        end = _at(stage + timedelta(days=21 + at * 5), WORK_END, tz)
        request = _request(org, person, leave, start, end, "APPROVED", reviewer,
                           comment="Плановый отпуск.")
        rows.append(EmployeeAbsence(
            organization=org, employee=person.employee, absence_type=leave,
            origin_request=request, start_at=start, end_at=end, status="PLANNED",
        ))

    # 4. Завершённые: отпуск и больничный, которые уже прошли.
    for at, kind in enumerate((leave, sick, leave)):
        person = _pick(people, "in_office", 20 + at)
        start = _at(stage - timedelta(days=40 + at * 6), WORK_START, tz)
        end = _at(stage - timedelta(days=33 + at * 6), WORK_END, tz)
        request = _request(org, person, kind, start, end, "APPROVED", reviewer,
                           comment="Завершённое отсутствие.")
        rows.append(EmployeeAbsence(
            organization=org, employee=person.employee, absence_type=kind,
            origin_request=request, start_at=start, end_at=end,
            status="COMPLETED", completed_at=end,
        ))

    # 5. Отмена. Заявка вида CANCEL обязана ссылаться на исходную —
    #    это правило базы (`ck_absence_requests_derived_needs_parent`).
    #    Одна отмена ждёт решения, вторая уже согласована, и вместе с
    #    ней отменено само отсутствие.
    for at, decided in enumerate((False, True)):
        person = _pick(people, "left", 5 + at)
        start = _at(stage + timedelta(days=25 + at * 7), WORK_START, tz)
        end = _at(stage + timedelta(days=32 + at * 7), WORK_END, tz)
        parent = _request(org, person, leave, start, end, "APPROVED", reviewer,
                          comment="Отпуск, который потом отменили.")
        _request(
            org, person, leave, start, end,
            "APPROVED" if decided else "SUBMITTED", reviewer,
            comment="Прошу отменить отпуск.", kind_of_request="CANCEL", parent=parent,
        )
        rows.append(EmployeeAbsence(
            organization=org, employee=person.employee, absence_type=leave,
            origin_request=parent, start_at=start, end_at=end,
            status="CANCELLED" if decided else "PLANNED",
            cancelled_at=_at(stage, WORK_END, tz) if decided else None,
        ))

    # 6. Продление больничного: заявка вида EXTEND со ссылкой на исходную.
    person = [one for one in people if one.state == "sick"][0]
    base = AbsenceRequest.objects.filter(
        organization=org, employee=person.employee, request_kind="CREATE"
    ).order_by("-created_at").first()
    if base is not None:
        _request(
            org, person, sick, base.requested_end_at,
            base.requested_end_at + timedelta(days=3), "SUBMITTED", reviewer,
            comment="Прошу продлить больничный на три дня.",
            kind_of_request="EXTEND", parent=base,
        )

    EmployeeAbsence.objects.bulk_create(rows, batch_size=500)
    return rows


def _request(
    org, person, kind, start, end, status, reviewer, *, comment: str,
    kind_of_request: str = "CREATE", parent=None,
) -> AbsenceRequest:
    """Одна заявка вместе со следом в истории решений.

    `submitted_at` у черновика пустой: черновик ещё не отправляли, и
    проставить ему время подачи значило бы сказать неправду о процессе.
    """
    submitted = None if status == "DRAFT" else start - timedelta(days=6)
    reviewed = None
    reviewed_by = None
    if status in ("APPROVED", "REJECTED"):
        reviewed = start - timedelta(days=5)
        reviewed_by = reviewer

    request = AbsenceRequest.objects.create(
        organization=org,
        employee=person.employee,
        absence_type=kind,
        request_kind=kind_of_request,
        parent_request=parent,
        requested_start_at=start,
        requested_end_at=end,
        employee_comment=comment,
        status=status,
        submitted_at=submitted,
        reviewed_by_user=reviewed_by,
        reviewed_at=reviewed,
        review_comment="Согласовано." if status == "APPROVED" else (
            "Период занят коллегой." if status == "REJECTED" else None
        ),
    )

    # История заявки — не украшение: по ней видно, кто и когда что
    # сделал, и без неё «решение принято» висит без автора.
    trail = [("CREATED", None, "DRAFT")]
    if submitted is not None:
        trail.append(("SUBMITTED", "DRAFT", "SUBMITTED"))
    if status == "IN_REVIEW":
        trail.append(("TAKEN_IN_REVIEW", "SUBMITTED", "IN_REVIEW"))
    if status in ("APPROVED", "REJECTED"):
        trail.append((status, "SUBMITTED", status))
    AbsenceAction.objects.bulk_create([
        AbsenceAction(
            organization=org,
            absence_request=request,
            action=action,
            actor_user=reviewer if action in ("APPROVED", "REJECTED", "TAKEN_IN_REVIEW") else None,
            actor_employee=person.employee if action in ("CREATED", "SUBMITTED") else None,
            previous_status=was,
            new_status=now,
        )
        for action, was, now in trail
    ])
    return request


# --- исправления отметок -----------------------------------------------------


def corrections(org, people, stage: date, tz, reviewer) -> None:
    """Заявки на исправление отметок во всех решаемых состояниях.

    Привязываются к настоящим закрытым сменам: заявка на исправление без
    смены — это заявка ни к чему, и в карточке её открыть нечем.
    """
    closed = list(
        AttendanceSession.objects.filter(organization=org, status="CLOSED")
        .order_by("employee__employee_number", "-started_at")[:400]
    )
    if not closed:
        return

    wanted = [
        ("SUBMITTED", "Забыл отметиться на входе — пришёл в 09:05.", "entry"),
        ("SUBMITTED", "Не сохранилась отметка выхода, ушёл в 18:10.", "exit"),
        ("IN_REVIEW", "Отметка выхода не прошла: турникет не сработал.", "exit"),
        ("APPROVED", "Вход не отметился, был на объекте с 08:50.", "entry"),
        ("REJECTED", "Прошу засчитать выход в 19:00.", "exit"),
    ]
    seen: set[uuid.UUID] = set()
    picked = []
    for session in closed:
        if session.employee_id in seen:
            continue
        seen.add(session.employee_id)
        picked.append(session)
        if len(picked) == len(wanted):
            break

    for at, (status, reason, which) in enumerate(wanted):
        if at >= len(picked):
            break
        session = picked[at]
        entry = session.started_at
        exit_at = session.ended_at or session.started_at
        if which == "entry":
            entry = entry - timedelta(minutes=25)
        else:
            exit_at = exit_at + timedelta(minutes=40)
        AttendanceCorrectionRequest.objects.create(
            organization=org,
            employee=session.employee,
            attendance_session=session,
            requested_entry_at=entry,
            requested_exit_at=exit_at,
            reason=reason,
            status=status,
            submitted_at=_at(stage, WORK_END, tz) - timedelta(hours=2 + at * 5),
            reviewed_by_user=reviewer if status in ("APPROVED", "REJECTED") else None,
            reviewed_at=(
                _at(stage, WORK_END, tz) - timedelta(hours=1)
                if status in ("APPROVED", "REJECTED") else None
            ),
            review_comment=(
                "Подтверждено записью с поста охраны." if status == "APPROVED"
                else "Подтверждения не нашлось." if status == "REJECTED" else None
            ),
        )


# --- Telegram ----------------------------------------------------------------


def telegram(org, people, now, reviewer) -> None:
    """Состояния привязки Telegram.

    Четыре разных состояния нужны очереди «требует внимания»: она
    показывает тех, у кого доступа ещё нет. Если бы все были привязаны,
    очередь была бы пустой, а проверить её нечем.

    Идентификаторы синтетические и не повторяются: база держит на них
    уникальность, а повтор означал бы, что один аккаунт Telegram
    принадлежит двоим.
    """
    accounts: list[TelegramAccount] = []
    invitations: list[TelegramLinkInvitation] = []
    connected: list[uuid.UUID] = []

    for at, person in enumerate(people):
        # Каждый девятый не привязан вовсе, каждый семнадцатый ждёт
        # подтверждения, один — с отозванным доступом.
        if at % 9 == 4:
            continue
        status = "ACTIVE"
        if at % 17 == 3:
            status = "PENDING"
        if at == 25:
            status = "REVOKED"

        accounts.append(TelegramAccount(
            organization=org,
            employee=person.employee,
            telegram_user_id=700_000_000 + at,
            telegram_chat_id=700_000_000 + at,
            telegram_username=f"demo_user_{at:03d}",
            language_code="ru",
            status=status,
            connected_at=now - timedelta(days=30 + at % 60),
            last_interaction_at=now - timedelta(hours=at % 48),
            revoked_at=now - timedelta(days=2) if status == "REVOKED" else None,
        ))
        if status == "ACTIVE":
            connected.append(person.employee.id)
        if status == "PENDING":
            invitations.append(TelegramLinkInvitation(
                organization=org,
                employee=person.employee,
                token_hash=hashlib.sha256(
                    f"{PREFIX}:invite:{person.employee.employee_number}".encode()
                ).hexdigest(),
                status="PENDING_CONFIRMATION",
                # Ссылку выдаёт кадровик: у приглашения обязан быть автор,
                # иначе непонятно, кто дал человеку доступ.
                created_by_user=reviewer,
                expires_at=now + timedelta(days=3),
                consumed_by_telegram_user_id=700_000_000 + at,
            ))

    TelegramAccount.objects.bulk_create(accounts, batch_size=500)
    TelegramLinkInvitation.objects.bulk_create(invitations, batch_size=200)
    # Признак в карточке обязан совпадать с состоянием привязки: иначе
    # карточка говорит «подключён», а очередь показывает того же
    # человека как неподключённого.
    Employee.objects.filter(id__in=connected).update(telegram_connected=True)


# --- обращения ---------------------------------------------------------------


def questions(org, people, now, reviewer) -> None:
    """Обращения сотрудников во всех состояниях, с перепиской.

    Каждое обращение собирается так, как его собрала бы сама система:
    первое сообщение сотрудника и событие «создано», взятие в работу,
    ответы HR со строкой очереди отправки, запрос уточнения, возврат в
    работу, закрытие с причиной. Ответ HR без строки очереди не
    заводится: в CRM он выглядел бы отправленным, а в Telegram его нет.

    Обращения с перепиской достаются людям с живой привязкой Telegram,
    одно — человеку без неё: на нём видно, что ответить нельзя.
    """
    sources = _demo_sources(org, now, reviewer)
    connected = set(
        TelegramAccount.objects.filter(organization=org, status="ACTIVE")
        .values_list("employee_id", flat=True)
    )
    pool = sorted(
        (one for one in people
         if one.scenario is None and one.employee.employment_status == "ACTIVE"),
        key=lambda one: one.employee.employee_number,
    )
    linked = [one for one in pool if one.employee.id in connected] or pool
    unlinked = [one for one in pool if one.employee.id not in connected] or pool

    first_number = next_number(org.id)
    for at, item in enumerate(QUESTIONS):
        group = linked if item.telegram else unlinked
        person = group[item.who % len(group)]
        _question(org, person.employee, item, first_number + at, now, reviewer, sources)


def _demo_sources(org, now, reviewer) -> dict[str, KnowledgeSource]:
    """Опубликованные документы, на которые ссылаются черновики обращений."""
    result = {}
    for one in DEMO_SOURCES:
        published = now - timedelta(days=one.days_ago)
        row = KnowledgeSource.objects.create(
            organization=org,
            title=one.title,
            source_type=one.source_type,
            language="ru",
            content=one.content,
            content_hash=hashlib.sha256(one.content.encode()).hexdigest(),
            status="ACTIVE",
            created_by_user=reviewer,
            approved_by_user=reviewer,
            approved_at=published,
            published_at=published,
            # По этой метке витрина снимает свои документы при повторе.
            meta={"demo": PREFIX},
        )
        KnowledgeSource.objects.filter(id=row.id).update(
            created_at=published, updated_at=published
        )
        result[one.key] = row
    return result


def _question(org, employee, item: DemoQuestion, number, now, reviewer, sources) -> None:
    steps = len(item.thread)
    last = now - timedelta(minutes=item.minutes_ago)
    gap = timedelta(minutes=max(3, min(item.minutes_ago // 8, 180)))
    first = last - gap * (steps - 1) - timedelta(minutes=2)
    moments = [first + timedelta(minutes=2) + gap * index for index in range(steps)]

    closed = item.status == "CLOSED"
    awaiting = item.thread[-1][0] == "E" and not closed
    replies = [at for at, (who, _) in zip(moments, item.thread) if who != "E"]
    closed_at = last + timedelta(minutes=10) if closed else None
    draft_sources = [str(sources[key].id) for key in item.sources]

    question = EmployeeQuestion.objects.create(
        organization=org,
        employee=employee,
        number=number,
        question_text=item.thread[0][1],
        normalized_topic=item.topic,
        status=item.status,
        priority=item.priority,
        category=item.category,
        assigned_to_user=reviewer if item.assigned else None,
        due_at=(
            last + SLA[item.priority]
            if awaiting and item.status in ("NEW", "IN_PROGRESS") else None
        ),
        last_message_at=last,
        unread=item.unread and not closed,
        awaiting_reply=awaiting,
        first_response_at=replies[0] if replies else None,
        closed_at=closed_at,
        closed_by_user=reviewer if closed else None,
        close_reason=item.close_reason if closed else None,
        ai_status=item.draft,
        ai_answer_text=item.answer,
        ai_confidence=(
            Decimal(str(item.confidence)) if item.confidence is not None else None
        ),
        ai_source_ids=draft_sources or None,
        answer_source=sources[item.sources[0]] if item.sources else None,
        ai_generated_at=first + timedelta(minutes=1) if item.draft else None,
    )
    EmployeeQuestion.objects.filter(id=question.id).update(created_at=first)

    hr = _person(reviewer)
    hr_json = {"id": str(hr["id"]), "name": hr["name"]}
    rows = [_event(question, "CREATED", first, None, {"source": "TELEGRAM"})]
    if item.assigned:
        rows.append(_event(
            question, "TAKEN", moments[0] + timedelta(minutes=1), reviewer,
            {"from": None, "to": hr_json, "status_from": "NEW",
             "status_to": "IN_PROGRESS"},
        ))

    previous = None
    for at, (who, text) in zip(moments, item.thread):
        if who == "E":
            if previous == "W":
                rows.append(_event(
                    question, "RESUMED", at, None,
                    {"from": "WAITING_EMPLOYEE", "to": "IN_PROGRESS"},
                ))
            rows.append(QuestionMessage(
                organization=org, question=question, kind="EMPLOYEE",
                source="TELEGRAM", body=text, author_employee=employee,
                created_at=at,
            ))
        else:
            message_id = uuid.uuid4()
            read = closed or at < now - timedelta(hours=6)
            notification = Notification.objects.create(
                organization=org,
                employee=employee,
                channel="TELEGRAM",
                notification_type="question.reply",
                title=f"Ответ на обращение №{number}",
                body=f"{REPLY_HEADER.format(number=number)}\n\n{text}",
                status="READ" if read else "SENT",
                sent_at=at + timedelta(seconds=20),
                read_at=at + timedelta(minutes=4) if read else None,
                idempotency_key=f"question.reply:{message_id}",
                related_entity_type="employee_questions",
                related_entity_id=question.id,
            )
            rows.append(QuestionMessage(
                id=message_id, organization=org, question=question, kind="HR",
                source="CRM", body=text, author_user=reviewer,
                notification=notification, created_at=at,
            ))
            if who == "W":
                rows.append(_event(
                    question, "WAITING_EMPLOYEE", at + timedelta(seconds=1),
                    reviewer, {"from": "IN_PROGRESS", "to": "WAITING_EMPLOYEE"},
                ))
        previous = who

    if closed:
        rows.append(_event(
            question, "CLOSED", closed_at, reviewer,
            {"from": "IN_PROGRESS", "to": "CLOSED", "reason": item.close_reason},
        ))
    QuestionMessage.objects.bulk_create(rows)


def _event(question, event, at, user, details) -> QuestionMessage:
    return QuestionMessage(
        organization_id=question.organization_id,
        question=question,
        kind="SYSTEM",
        source="SYSTEM" if user is None else "CRM",
        event=event,
        details=details,
        author_user=user,
        created_at=at,
    )


# --- уведомления -------------------------------------------------------------


def notifications(org, people, now) -> None:
    """Объявления, новости офиса и напоминания.

    Рассылки одной строкой модель не знает: уведомление адресовано
    сотруднику. Поэтому объявление всей организации — это столько
    строк, сколько людей, и так же оно устроено в бою.
    """
    rows: list[Notification] = []
    for notice in NOTICES:
        if notice.scope == "organization":
            targets = people
        elif notice.scope == "office":
            targets = [one for one in people if one.spec.code == notice.office]
        elif notice.scope == "picked":
            targets = people[:notice.picked]
        elif notice.scope == "sick":
            targets = [one for one in people if one.state == "sick"]
        else:
            targets = [one for one in people if one.state == "vacation"]

        sent = now - timedelta(days=notice.days_ago)
        for at, person in enumerate(targets):
            read = at * 100 // max(len(targets), 1) < notice.read_pct
            rows.append(Notification(
                organization=org,
                employee=person.employee,
                channel="IN_APP",
                notification_type=notice.kind,
                title=notice.title,
                body=notice.body,
                status="READ" if read else "SENT",
                sent_at=sent,
                read_at=sent + timedelta(hours=2) if read else None,
            ))
    Notification.objects.bulk_create(rows, batch_size=1000)


# --- документы ---------------------------------------------------------------


def drop_files(org) -> None:
    """Убрать файлы прошлого запуска — и записи, и сами байты.

    Без этого повторный посев оставлял бы в хранилище копию каждого
    документа: ключ на диске случайный, и новая запись старую не
    перезаписывает. Удаляются только свои: отбор идёт по каталогу
    `demo/`, в который витрина и кладёт.
    """
    storage = private_storage()
    rows = list(File.objects.filter(
        organization=org, storage_key__startswith=f"{FILE_PREFIX}/"
    ))
    ids = [row.id for row in rows]
    if not ids:
        return

    # Ссылки на файл защищённые: пока на него смотрит бумага, удалить его
    # нельзя. Бумаги эти — наши же: файл лежит в нашем каталоге, и
    # ссылаться на него больше некому. Снимаются по ссылке, а не по
    # сотруднику: состав витрины между запусками мог измениться, и
    # прошлогодняя бумага осталась бы висеть на файле, которого нет.
    AbsenceDocument.objects.filter(file_id__in=ids).delete()
    EmployeeDocument.objects.filter(file_id__in=ids).delete()
    Employee.objects.filter(photo_id__in=ids).update(photo=None)

    for row in rows:
        try:
            storage.delete(row.storage_key)
        except Exception:  # pragma: no cover - файла могло уже не быть
            pass
    File.objects.filter(id__in=ids).delete()


def attach_photos(org, people, reviewer) -> int:
    """Разложить фотографии из локального пакета по карточкам.

    Файл кладётся тем же сервисом, что и всё остальное: он проверит
    содержимое и положит снимок в приватное хранилище, откуда карточка
    его и отдаёт. Записать путь в базу руками значило бы завести вторую
    дорогу к хранилищу.

    Пустой пакет — обычное состояние: без ключа к фотобанку снимков нет,
    и карточка показывает инициалы. Это не поломка и не заглушка.
    """
    entries = photo_pack.pack()
    if not entries:
        return 0

    order = [
        (one.employee.employee_number, one.employee.gender == "FEMALE")
        for one in people
    ]
    by_number = {one.employee.employee_number: one for one in people}
    made = 0
    for photo, number in photo_pack.plan(entries, order):
        person = by_number.get(number)
        if person is None:
            continue
        record = _store(
            org, person.employee, photo.path.read_bytes(),
            photo.file, reviewer, mime=photo_pack.MIME,
        )
        Employee.objects.filter(id=person.employee.id).update(photo=record)
        made += 1
    return made


def papers(org, people, absence_rows, stage: date, reviewer) -> int:
    """Настоящие PDF и один скан, привязанные к своим заявкам.

    Файл кладётся штатным файловым сервисом: он проверяет размер, тип и
    содержимое, считает контрольную сумму и сам решает, куда положить.
    Записать путь в базу руками значило бы завести вторую дорогу к
    хранилищу, которая однажды разойдётся с первой.

    Периоды в тексте берутся из тех же заявок, что лежат в базе: если в
    справке стоит 10–13 сентября, столько же стоит и в заявке.
    """
    made = 0
    sick_requests = list(
        AbsenceRequest.objects.filter(
            organization=org, absence_type__code="SICK_LEAVE", request_kind="CREATE"
        ).select_related("employee").order_by("employee__employee_number")
    )
    leave_requests = list(
        AbsenceRequest.objects.filter(
            organization=org, absence_type__code="ANNUAL_LEAVE", request_kind="CREATE"
        ).select_related("employee").order_by("employee__employee_number")
    )
    cancel_requests = list(
        AbsenceRequest.objects.filter(
            organization=org, request_kind="CANCEL"
        ).select_related("employee").order_by("employee__employee_number")
    )
    extend_requests = list(
        AbsenceRequest.objects.filter(
            organization=org, request_kind="EXTEND"
        ).select_related("employee").order_by("employee__employee_number")
    )
    corrections_rows = list(
        AttendanceCorrectionRequest.objects.filter(organization=org)
        .select_related("employee").order_by("employee__employee_number")
    )

    # --- заявления на отпуск: заполненное, ожидающее, одобренное, отменённое
    for at, (request, note) in enumerate(zip(
        leave_requests[:3],
        ("Заявление подано.", "Ожидает решения руководителя.", "Согласовано."),
    )):
        made += _attach_to_request(
            org, request, reviewer,
            paper_texts.leave_application(request, note),
            f"zayavlenie-otpusk-{at + 1}.pdf",
            document_type="LEAVE_APPLICATION",
            verification="VERIFIED" if request.status == "APPROVED" else "PENDING",
        )
    for request in cancel_requests[:1]:
        made += _attach_to_request(
            org, request, reviewer,
            paper_texts.leave_cancellation(request),
            "zayavlenie-otmena-otpuska.pdf",
            document_type="LEAVE_CANCELLATION",
            verification="PENDING",
        )

    # --- больничные: заявление, справка на проверке, принятая, отклонённая
    if sick_requests:
        made += _attach_to_request(
            org, sick_requests[0], reviewer,
            paper_texts.sick_application(sick_requests[0]),
            "zayavlenie-bolnichnyy.pdf",
            document_type="SICK_APPLICATION", verification="PENDING",
        )
    for at, (request, verdict) in enumerate(zip(
        sick_requests[1:4], ("PENDING", "VERIFIED", "REJECTED")
    )):
        made += _attach_to_request(
            org, request, reviewer,
            paper_texts.medical_note(request, verdict),
            f"spravka-{at + 1}.pdf",
            document_type="SICK_NOTE", verification=verdict,
        )
    for request in extend_requests[:1]:
        made += _attach_to_request(
            org, request, reviewer,
            paper_texts.sick_extension(request),
            "prodlenie-bolnichnogo.pdf",
            document_type="SICK_EXTENSION", verification="PENDING",
        )

    # --- скан справки картинкой: проверка предпросмотра не-PDF документа
    if len(sick_requests) > 4:
        made += _attach_to_request(
            org, sick_requests[4], reviewer,
            images.scan_png(), "skan-spravki.png",
            document_type="SICK_NOTE", verification="PENDING",
            mime="image/png",
        )

    # --- заявления на исправление отметок
    for at, request in enumerate(corrections_rows[:2]):
        person = request.employee
        body = paper_texts.correction_application(request, at == 0)
        record = _store(org, person, body, f"ispravlenie-otmetki-{at + 1}.pdf", reviewer)
        EmployeeDocument.objects.update_or_create(
            organization=org, employee=person, kind="OTHER",
            title=f"Заявление на исправление отметки ({at + 1})",
            defaults={"status": "UPLOADED", "file": record},
        )
        made += 1

    # --- кадровые бумаги сотрудников-сценариев
    hr_papers = [
        ("HIRE_ORDER", "Приказ о приёме", paper_texts.hire_order),
        ("OTHER", "Дополнительное соглашение о переводе", paper_texts.transfer_agreement),
        ("OTHER", "Приказ об изменении графика", paper_texts.schedule_order),
        ("CONTRACT", "Трудовой договор", paper_texts.employment_contract),
    ]
    targets = [one for one in people if one.scenario is not None][:len(hr_papers)]
    for person, (kind, title, maker) in zip(targets, hr_papers):
        record = _store(
            org, person.employee, maker(person, stage),
            f"{kind.lower()}-{person.employee.employee_number}.pdf", reviewer,
        )
        EmployeeDocument.objects.update_or_create(
            organization=org, employee=person.employee, kind=kind, title=title,
            defaults={"status": "UPLOADED", "file": record},
        )
        made += 1

    return made


def _attach_to_request(
    org, request, reviewer, body: bytes, name: str, *,
    document_type: str, verification: str, mime: str = "application/pdf",
) -> int:
    record = _store(org, request.employee, body, name, reviewer, mime=mime)
    AbsenceDocument.objects.create(
        organization=org,
        absence_request=request,
        file=record,
        document_type=document_type,
        verification_status=verification,
        verified_by_user=reviewer if verification != "PENDING" else None,
        verified_at=request.reviewed_at if verification != "PENDING" else None,
        verification_comment=(
            "Справка принята." if verification == "VERIFIED"
            else "Демонстрационная причина: период в справке не совпадает с заявкой."
            if verification == "REJECTED" else None
        ),
    )
    AbsenceAction.objects.create(
        organization=org,
        absence_request=request,
        action="DOCUMENT_ATTACHED",
        actor_employee=request.employee,
        previous_status=request.status,
        new_status=request.status,
    )
    return 1


def _store(org, employee, body: bytes, name: str, reviewer, mime: str = "application/pdf") -> File:
    """Положить файл штатным сервисом и вернуть запись.

    `SimpleUploadedFile` здесь не подмена: сервис принимает любой
    загружаемый объект Django, и путь у файла ровно тот же, каким он
    будет у настоящей загрузки из браузера.
    """
    upload = SimpleUploadedFile(name, body, content_type=mime)
    allowed = ("application/pdf",) if mime == "application/pdf" else ("image/png", "image/jpeg")
    # Каталог у фотографий свой: у снимка и у справки разный срок
    # хранения и разные права, и разбирать одну кучу пришлось бы запросом.
    prefix = f"{FILE_PREFIX}/photos" if mime != "application/pdf" and name.endswith(".jpg") else FILE_PREFIX
    stored = store(
        upload,
        organization_id=org.id,
        employee=employee,
        allowed_types=allowed,
        max_bytes=12 * 1024 * 1024,
        prefix=prefix,
    )
    return stored.file
