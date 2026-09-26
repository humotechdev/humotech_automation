"""Заявки: отпуска, больничные, командировки и исправления отметок.

Больничный подтверждается по правилам системы и никак иначе: справка
принята, подписанное заявление отмечено, фактические даты проставил HR
(действие `PERIOD_SET`). Только тогда заявка `APPROVED` и появляется
строка отсутствия. Неподтверждённый больничный строки отсутствия не
имеет — в табель и отчёт отсутствий он не попадает.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from django.core.files.uploadedfile import SimpleUploadedFile

from humotech.absences.models import AbsenceAction, AbsenceDocument, AbsenceRequest, AbsenceType, EmployeeAbsence
from humotech.absences.application_pdf import APPLICATION_DOCUMENT
from humotech.attendance.models import AttendanceCorrectionRequest, AttendanceSession
from humotech.core.demo import papers
from humotech.core.demo.hr.people import Person, did
from humotech.core.timeframes import range_bounds
from humotech.files.storage import store

FILE_PREFIX = "hrdemo"


def absence_types(org) -> dict[str, AbsenceType]:
    """Виды отсутствий. Недостающие заводятся витриной и уходят вместе с ней."""
    types = {one.code: one for one in AbsenceType.objects.filter(organization=org)}
    wanted = (
        ("ANNUAL_LEAVE", "Ежегодный отпуск", False, True),
        ("SICK_LEAVE", "Больничный", True, True),
        ("BUSINESS_TRIP", "Командировка", False, True),
    )
    for code, name, document, paid in wanted:
        if code not in types:
            types[code] = AbsenceType.objects.create(
                id=did("absence-type", code), organization=org, code=code, name=name,
                is_paid=paid, requires_approval=True, requires_document=document,
            )
    return types


def build(org, people: list[Person], types, today: date, now: datetime, tz, reviewer) -> tuple[set, set, dict]:
    """Возвращает дни подтверждённых отсутствий, дни без отметок и счётчики."""
    absent: set[tuple] = set()
    quiet: set[tuple] = set()
    counts = {"requests": 0, "absences": 0}
    by_role = lambda role: [p for p in people if role in p.roles]  # noqa: E731

    def span(first: date, last: date):
        start, end = range_bounds(first, last, tz)
        return start, end

    def request(person, kind, key, first, last, status, *, comment, submitted_days=6, trail=(), review=None,
                parent=None, request_kind="CREATE", received=False):
        start, end = span(first, last)
        submitted = datetime.combine(first - timedelta(days=submitted_days), time(10, 15), tzinfo=tz)
        if submitted > now:
            submitted = now - timedelta(hours=3)
        decided = submitted + timedelta(days=1, hours=2)
        if decided > now:
            decided = now - timedelta(minutes=40)
        row = AbsenceRequest.objects.create(
            id=did("request", person.number, key), organization=org, employee=person.employee,
            absence_type=types[kind], request_kind=request_kind, parent_request=parent,
            requested_start_at=start, requested_end_at=end, employee_comment=comment, status=status,
            submitted_at=submitted, reviewed_by_user=reviewer if status in ("APPROVED", "REJECTED") else None,
            reviewed_at=decided if status in ("APPROVED", "REJECTED") else None, review_comment=review,
            application_received_at=(submitted + timedelta(hours=20)) if received else None,
        )
        AbsenceRequest.objects.filter(id=row.id).update(created_at=submitted - timedelta(minutes=3))
        actions = [("CREATED", None, "DRAFT", person, None, submitted - timedelta(minutes=3), None),
                   ("SUBMITTED", "DRAFT", "SUBMITTED", person, None, submitted, None)]
        moment = submitted
        for step in trail:
            moment = moment + timedelta(hours=3)
            action, was, becomes, comment_text = step
            actions.append((action, was, becomes, None, reviewer, min(moment, decided if status == "APPROVED" else now), comment_text))
        if status in ("APPROVED", "REJECTED"):
            actions.append((status, "IN_REVIEW", status, None, reviewer, decided, None))
        if status == "CANCELLED":
            actions.append(("CANCELLED", "SUBMITTED", "CANCELLED", person, None, submitted + timedelta(days=2), None))
        rows = []
        for at, (action, was, becomes, employee, user, when, text) in enumerate(actions):
            rows.append(AbsenceAction(
                id=did("action", row.id, at), organization=org, absence_request=row, action=action,
                actor_employee=employee.employee if employee else None, actor_user=user,
                previous_status=was, new_status=becomes, comment=text,
            ))
        AbsenceAction.objects.bulk_create(rows)
        for at, (_, _, _, _, _, when, _) in enumerate(actions):
            AbsenceAction.objects.filter(id=did("action", row.id, at)).update(created_at=when)
        counts["requests"] += 1
        return row

    def absence(person, row, first, last):
        start, end = span(first, last)
        status = "COMPLETED" if last < today else "PLANNED" if first > today else "ACTIVE"
        EmployeeAbsence.objects.create(
            id=did("absence", row.id), organization=org, employee=person.employee, absence_type=row.absence_type,
            origin_request=row, start_at=start, end_at=end, start_date=first, end_date=last, status=status,
            completed_at=end if status == "COMPLETED" else None,
        )
        day = first
        while day <= last:
            absent.add((person.employee.id, day))
            day += timedelta(days=1)
        counts["absences"] += 1

    def paper(row, body: bytes, name: str, doc_type: str, verification: str, comment=None):
        upload = SimpleUploadedFile(name, body, content_type="application/pdf")
        stored = store(upload, organization_id=org.id, employee=row.employee, allowed_types=("application/pdf",),
                       max_bytes=12 * 1024 * 1024, prefix=FILE_PREFIX)
        AbsenceDocument.objects.create(
            id=did("document", row.id, doc_type), organization=org, absence_request=row, file=stored.file,
            document_type=doc_type, verification_status=verification,
            verified_by_user=reviewer if verification != "PENDING" else None,
            verified_at=(row.submitted_at + timedelta(hours=26)) if verification != "PENDING" else None,
            verification_comment=comment,
        )

    # Отпуска, которые идут сегодня: одобрены по правилам.
    for at, person in enumerate(by_role("vacation")):
        first, last = today - timedelta(days=1 + at * 2), today + timedelta(days=5 + at)
        row = request(person, "ANNUAL_LEAVE", "vacation", first, last, "APPROVED", comment="Ежегодный отпуск по графику.",
                      submitted_days=18, trail=(("TAKEN_IN_REVIEW", "SUBMITTED", "IN_REVIEW", None),),
                      review="Согласовано. Хорошего отдыха!")
        absence(person, row, first, last)

    # Больничные, подтверждённые целиком: справка, заявление, даты от HR.
    for at, person in enumerate(by_role("sick")):
        first, last = today - timedelta(days=2 + at), today + timedelta(days=1 + at)
        row = request(person, "SICK_LEAVE", "sick", first, last, "APPROVED", comment="Температура, открыт больничный.",
                      submitted_days=0, received=True,
                      trail=(("DOCUMENT_ATTACHED", "SUBMITTED", "SUBMITTED", None),
                             ("TAKEN_IN_REVIEW", "SUBMITTED", "IN_REVIEW", None),
                             ("DOCUMENT_VERIFIED", "IN_REVIEW", "IN_REVIEW", "Справка принята."),
                             ("PERIOD_SET", "IN_REVIEW", "IN_REVIEW", "Даты перенесены из справки.")),
                      review="Больничный подтверждён.")
        paper(row, papers.medical_note(row, "Нетрудоспособен"), "spravka.pdf", "MEDICAL_CERTIFICATE", "VERIFIED", "Справка принята.")
        paper(row, papers.sick_application(row), "zayavlenie.pdf", APPLICATION_DOCUMENT, "VERIFIED")
        absence(person, row, first, last)

    # Командировки, которые идут сейчас.
    for at, person in enumerate(by_role("trip")):
        first, last = today - timedelta(days=1), today + timedelta(days=2 + 2 * at)
        row = request(person, "BUSINESS_TRIP", "trip", first, last, "APPROVED",
                      comment=("Встреча с партнёрами в Самарканде." if at == 0 else "Выставка в Алматы."),
                      submitted_days=9, trail=(("TAKEN_IN_REVIEW", "SUBMITTED", "IN_REVIEW", None),),
                      review="Командировка согласована.")
        absence(person, row, first, last)

    # Больничный ждёт справку: даты — ориентир сотрудника, в табель не идут.
    for person in by_role("waiting_cert"):
        first, last = today - timedelta(days=1), today + timedelta(days=3)
        request(person, "SICK_LEAVE", "sick-waiting", first, last, "SUBMITTED",
                comment="Заболел, справку загружу после приёма врача.", submitted_days=0)
        _quiet(quiet, person, first, today)

    # Больничный на проверке HR: справка загружена, ещё не проверена.
    for person in by_role("hr_review"):
        first, last = today - timedelta(days=2), today + timedelta(days=2)
        row = request(person, "SICK_LEAVE", "sick-review", first, last, "IN_REVIEW",
                      comment="Справка из поликлиники приложена.", submitted_days=0, received=True,
                      trail=(("DOCUMENT_ATTACHED", "SUBMITTED", "SUBMITTED", None),
                             ("TAKEN_IN_REVIEW", "SUBMITTED", "IN_REVIEW", None)))
        paper(row, papers.medical_note(row, "Нетрудоспособен"), "spravka.pdf", "MEDICAL_CERTIFICATE", "PENDING")
        _quiet(quiet, person, first, today)

    # Справка отклонена — нужны исправления, и причина написана словами.
    reason = "На справке нет печати клиники и не указан период. Загрузите, пожалуйста, справку с печатью."
    for person in by_role("needs_fix"):
        first, last = today - timedelta(days=9), today - timedelta(days=6)
        row = request(person, "SICK_LEAVE", "sick-fix", first, last, "IN_REVIEW",
                      comment="Был на больничном, справку прикладываю.", submitted_days=0,
                      trail=(("DOCUMENT_ATTACHED", "SUBMITTED", "SUBMITTED", None),
                             ("TAKEN_IN_REVIEW", "SUBMITTED", "IN_REVIEW", None),
                             ("DOCUMENT_REJECTED", "IN_REVIEW", "IN_REVIEW", reason)),
                      review=reason)
        paper(row, papers.medical_note(row, "Нетрудоспособен"), "spravka.pdf", "MEDICAL_CERTIFICATE", "REJECTED", reason)
        _quiet(quiet, person, first, last)

    # Очередь отпусков и командировок.
    for at, person in enumerate(by_role("vacation_pending")):
        first = today + timedelta(days=12 + 8 * at)
        request(person, "ANNUAL_LEAVE", "vacation-pending", first, first + timedelta(days=9), "SUBMITTED",
                comment="Прошу отпуск: семейная поездка.", submitted_days=10 + at * 5)
    for person in by_role("vacation_clarify"):
        first = today + timedelta(days=15)
        note = "Уточните, пожалуйста: вы берёте все 14 дней подряд или делите отпуск на две части?"
        request(person, "ANNUAL_LEAVE", "vacation-clarify", first, first + timedelta(days=13), "IN_REVIEW",
                comment="Отпуск в октябре.", submitted_days=13,
                trail=(("TAKEN_IN_REVIEW", "SUBMITTED", "IN_REVIEW", None), ("COMMENTED", "IN_REVIEW", "IN_REVIEW", note)),
                review=note)
    for person in by_role("vacation_cancelled"):
        first = today + timedelta(days=25)
        request(person, "ANNUAL_LEAVE", "vacation-cancelled", first, first + timedelta(days=6), "CANCELLED",
                comment="Отпуск на неделю.", submitted_days=20)
    for person in by_role("trip_pending"):
        first = today + timedelta(days=6)
        request(person, "BUSINESS_TRIP", "trip-pending", first, first + timedelta(days=2), "SUBMITTED",
                comment="Обучение в головном офисе партнёра в Бухаре.", submitted_days=4)
    return absent, quiet, counts


def _quiet(quiet: set, person: Person, first: date, last: date) -> None:
    day = first
    while day <= last:
        quiet.add((person.employee.id, day))
        day += timedelta(days=1)


def corrections(org, people: list[Person], today: date, now: datetime, reviewer) -> int:
    """Исправления отметок к настоящим закрытым сменам."""
    wanted = [
        ("SUBMITTED", "Забыл отметиться на входе — пришёл в 08:55, был на планёрке.", "entry"),
        ("IN_REVIEW", "Отметка выхода не прошла: телефон разрядился, ушёл в 18:20.", "exit"),
        ("APPROVED", "Вход не сохранился, был на объекте клиента с 09:00.", "entry"),
        ("REJECTED", "Прошу засчитать выход в 20:00.", "exit"),
    ]
    made = 0
    for at, person in enumerate(p for p in people if "correction" in p.roles):
        status, reason, which = wanted[at]
        session = (AttendanceSession.objects.filter(employee=person.employee, status="CLOSED",
                                                    started_at__lt=now - timedelta(days=2))
                   .order_by("-started_at").first())
        if session is None:
            continue
        entry = session.started_at - timedelta(minutes=20) if which == "entry" else session.started_at
        exit_at = session.ended_at + timedelta(minutes=35) if which == "exit" else session.ended_at
        submitted = session.started_at + timedelta(hours=10)
        decided = status in ("APPROVED", "REJECTED")
        AttendanceCorrectionRequest.objects.create(
            id=did("correction", person.number), organization=org, employee=person.employee,
            attendance_session=session, requested_entry_at=entry, requested_exit_at=exit_at, reason=reason,
            status=status, submitted_at=submitted,
            reviewed_by_user=reviewer if decided else None,
            reviewed_at=submitted + timedelta(hours=5) if decided else None,
            review_comment=("Подтверждено по журналу охраны." if status == "APPROVED"
                            else "По журналу охраны выход в 18:40 — засчитать 20:00 нельзя." if status == "REJECTED"
                            else None),
        )
        made += 1
    return made
