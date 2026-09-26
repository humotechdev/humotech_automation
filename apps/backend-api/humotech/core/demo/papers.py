"""Тексты демонстрационных бумаг.

Каждая бумага собирается из ТЕХ ЖЕ записей, что лежат в базе: период в
справке берётся у заявки, фамилия — у сотрудника. Иначе документ и
карточка рассказывали бы разное, и проверить по нему было бы нечего.

Ничего настоящего в этих бумагах нет: ни подписей врачей, ни печатей
клиник, ни номеров государственных систем. Номер справки — `DEMO-MED-…`,
клиника — «Тестовая клиника», и на каждой странице стоит водяной знак,
прямо говорящий, что документ демонстрационный.

Место подписи оставлено пустым нарочно: процесс предполагает печать и
подпись от руки, и рисовать её здесь значило бы показать то, чего в
процессе нет.
"""

from __future__ import annotations

from datetime import date, datetime

from humotech.core.demo import pdf

CLINIC = "Тестовая клиника «ДЕМО»"
DISCLAIMER = "Документ создан для демонстрации интерфейса. Юридической силы не имеет."


def _day(moment: datetime | date | None) -> str:
    if moment is None:
        return "—"
    if isinstance(moment, datetime):
        moment = moment.date()
    return moment.strftime("%d.%m.%Y")


def _span(request) -> str:
    return f"{_day(request.requested_start_at)} — {_day(request.requested_end_at)}"


def _who(employee) -> str:
    parts = [employee.last_name, employee.first_name, employee.middle_name]
    return " ".join(part for part in parts if part)


def _number(request, prefix: str) -> str:
    """Фиктивный номер. Виден сразу: начинается с DEMO."""
    return f"{prefix}-{str(request.id)[:8].upper()}"


def leave_application(request, note: str) -> bytes:
    return pdf.build(pdf.Paper(
        title="Заявление на ежегодный оплачиваемый отпуск",
        fields=[
            ("Сотрудник", _who(request.employee)),
            ("Табельный номер", request.employee.employee_number),
            ("Период отпуска", _span(request)),
            ("Состояние заявки", _status(request.status)),
            ("Номер заявления", _number(request, "ДЕМО-ОТП")),
        ],
        lines=[
            "Прошу предоставить ежегодный оплачиваемый отпуск за указанный период.",
            f"Отметка кадровой службы: {note}",
            DISCLAIMER,
        ],
    ))


def leave_cancellation(request) -> bytes:
    return pdf.build(pdf.Paper(
        title="Заявление об отмене отпуска",
        fields=[
            ("Сотрудник", _who(request.employee)),
            ("Табельный номер", request.employee.employee_number),
            ("Отменяемый период", _span(request)),
            ("Состояние заявки", _status(request.status)),
            ("Номер заявления", _number(request, "ДЕМО-ОТМ")),
        ],
        lines=[
            "Прошу отменить ранее согласованный отпуск за указанный период.",
            "Причина: производственная необходимость.",
            DISCLAIMER,
        ],
    ))


def sick_application(request) -> bytes:
    return pdf.build(pdf.Paper(
        title="Заявление о временной нетрудоспособности",
        fields=[
            ("Сотрудник", _who(request.employee)),
            ("Табельный номер", request.employee.employee_number),
            ("Период", _span(request)),
            ("Справка", "будет приложена позже"),
            ("Номер заявления", _number(request, "ДЕМО-БЛ")),
        ],
        lines=[
            "Сообщаю о временной нетрудоспособности за указанный период.",
            "Медицинскую справку обязуюсь приложить после выписки.",
            DISCLAIMER,
        ],
    ))


def medical_note(request, verdict: str) -> bytes:
    """Медицинская справка.

    Ни диагноза, ни подписи врача, ни печати: в демонстрационном образце
    этих данных быть не должно вовсе, а не «в обезличенном виде».
    """
    verdicts = {
        "PENDING": "Ожидает проверки кадровой службой",
        "VERIFIED": "Проверена и принята",
        "REJECTED": "Отклонена: период в справке не совпал с заявкой",
    }
    return pdf.build(pdf.Paper(
        title="Медицинская справка о временной нетрудоспособности",
        fields=[
            ("Пациент", _who(request.employee)),
            ("Табельный номер", request.employee.employee_number),
            ("Период нетрудоспособности", _span(request)),
            ("Медицинское учреждение", CLINIC),
            ("Номер справки", _number(request, "DEMO-MED-2026")),
            ("Состояние", verdicts.get(verdict, verdict)),
        ],
        lines=[
            "Справка выдана для представления по месту работы.",
            "Диагноз в демонстрационном образце не указывается.",
            "Подпись врача и печать учреждения отсутствуют: документ демонстрационный.",
            DISCLAIMER,
        ],
        signature="Подпись врача: ДЕМО",
    ))


def sick_extension(request) -> bytes:
    return pdf.build(pdf.Paper(
        title="Заявление о продлении больничного",
        fields=[
            ("Сотрудник", _who(request.employee)),
            ("Табельный номер", request.employee.employee_number),
            ("Период продления", _span(request)),
            ("Медицинское учреждение", CLINIC),
            ("Номер", _number(request, "DEMO-MED-2026")),
        ],
        lines=[
            "Прошу продлить период временной нетрудоспособности.",
            "Основание: заключение тестовой клиники «ДЕМО».",
            DISCLAIMER,
        ],
    ))


def correction_application(request, missing_entry: bool) -> bytes:
    what = "входа" if missing_entry else "выхода"
    return pdf.build(pdf.Paper(
        title=f"Заявление на исправление отметки {what}",
        fields=[
            ("Сотрудник", _who(request.employee)),
            ("Табельный номер", request.employee.employee_number),
            ("Дата смены", _day(request.requested_entry_at)),
            ("Запрошенный вход", _clock(request.requested_entry_at)),
            ("Запрошенный выход", _clock(request.requested_exit_at)),
            ("Состояние", _status(request.status)),
        ],
        lines=[
            f"Прошу восстановить пропущенную отметку {what} за указанную смену.",
            f"Причина: {request.reason}",
            DISCLAIMER,
        ],
    ))


def hire_order(person, stage: date) -> bytes:
    employee = person.employee
    return pdf.build(pdf.Paper(
        title="Приказ о приёме на работу",
        fields=[
            ("Сотрудник", _who(employee)),
            ("Табельный номер", employee.employee_number),
            ("Дата приёма", _day(employee.hire_date)),
            ("Офис", person.office.name),
            ("График", person.schedule.name),
            ("Номер приказа", f"ДЕМО-ПР-{employee.employee_number[-3:]}"),
        ],
        lines=[
            "Принять на работу на условиях, указанных в трудовом договоре.",
            "Основание: заявление сотрудника и решение кадровой службы.",
            DISCLAIMER,
        ],
    ))


def transfer_agreement(person, stage: date) -> bytes:
    employee = person.employee
    return pdf.build(pdf.Paper(
        title="Дополнительное соглашение о переводе",
        fields=[
            ("Сотрудник", _who(employee)),
            ("Табельный номер", employee.employee_number),
            ("Новый офис", person.office.name),
            ("Дата перевода", _day(person.offices[-1][0])),
            ("Номер соглашения", f"ДЕМО-ДС-{employee.employee_number[-3:]}"),
        ],
        lines=[
            "Стороны договорились об изменении места работы сотрудника.",
            "Остальные условия трудового договора остаются без изменений.",
            DISCLAIMER,
        ],
    ))


def schedule_order(person, stage: date) -> bytes:
    employee = person.employee
    return pdf.build(pdf.Paper(
        title="Приказ об изменении режима рабочего времени",
        fields=[
            ("Сотрудник", _who(employee)),
            ("Табельный номер", employee.employee_number),
            ("Новый график", person.schedule.name),
            ("Действует с", _day(stage)),
            ("Номер приказа", f"ДЕМО-ГР-{employee.employee_number[-3:]}"),
        ],
        lines=[
            "Установить сотруднику указанный режим рабочего времени.",
            "Ознакомить сотрудника с приказом под подпись.",
            DISCLAIMER,
        ],
    ))


def employment_contract(person, stage: date) -> bytes:
    employee = person.employee
    return pdf.build(pdf.Paper(
        title="Трудовой договор (демонстрационный образец)",
        fields=[
            ("Работник", _who(employee)),
            ("Табельный номер", employee.employee_number),
            ("Дата начала работы", _day(employee.hire_date)),
            ("Место работы", person.office.name),
            ("Режим рабочего времени", person.schedule.name),
            ("Номер договора", f"ДЕМО-ТД-{employee.employee_number[-3:]}"),
        ],
        lines=[
            "Работодатель предоставляет работнику работу по указанной должности.",
            "Работник обязуется выполнять трудовую функцию лично.",
            "Договор составлен в двух экземплярах, по одному для каждой стороны.",
            DISCLAIMER,
        ],
    ))


def _status(status: str) -> str:
    return {
        "DRAFT": "Черновик",
        "SUBMITTED": "Отправлено, ожидает решения",
        "IN_REVIEW": "На рассмотрении",
        "APPROVED": "Одобрено",
        "REJECTED": "Отклонено",
        "CANCELLED": "Отменено",
    }.get(status, status)


def _clock(moment) -> str:
    if moment is None:
        return "—"
    return moment.strftime("%d.%m.%Y %H:%M")
