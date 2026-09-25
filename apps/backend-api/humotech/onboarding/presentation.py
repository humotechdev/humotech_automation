"""Как ознакомление выглядит в ответах API.

Отдельный модуль по той же причине, что и `selfservice/presentation.py`:
`progress.py` считает и про JSON знать не должен, а виду ответа полезно
быть в одном месте — бот и CRM разбирают одни и те же поля, и разъехаться
им негде.

Текстов кнопок бот здесь не придумывает: подпись приходит с сервера
(`button_label`, `agree_label`), потому что она часть содержимого. Под
правилами распорядка стоит «С правилами ознакомился», а не «Я
ознакомился», и решает это тот, кто пишет текст, а не тот, кто рисует
клавиатуру.
"""

from __future__ import annotations

from humotech.onboarding.progress import PolicyState, Progress, SectionState


def section_json(state: SectionState, *, total: int) -> dict:
    section = state.section
    return {
        "id": str(section.id),
        "position": section.position,
        "total": total,
        "title": section.title,
        "body": section.body,
        "button_label": section.button_label,
        "version": section.version,
        "acknowledged_at": (
            state.acknowledged_at.isoformat() if state.acknowledged_at else None
        ),
        "acknowledged_version": state.acknowledged_version,
    }


def policy_json(state: PolicyState, *, index: int, total: int) -> dict:
    """Документ для бота. Полного текста здесь НЕТ намеренно.

    Он приходит отдельным запросом — тем, который человек делает
    кнопкой «Открыть полный документ». Иначе каждое обновление экрана
    таскало бы по несколько килобайт правового текста, который в этот
    момент никто не читает.
    """
    version = state.version
    return {
        "document_id": str(state.document.id),
        "code": state.document.code,
        "title": state.document.title,
        "description": state.document.description,
        "index": index,
        "total": total,
        "version_id": str(version.id) if version else None,
        "version": version.version if version else None,
        "summary": version.summary if version else None,
        "agree_label": version.agree_label if version else None,
        "has_body": bool(version and version.body),
        "has_file": bool(version and version.file_id),
        "published_at": (
            version.published_at.isoformat()
            if version and version.published_at else None
        ),
        "decision": state.decision,
        "decided_at": state.decided_at.isoformat() if state.decided_at else None,
    }


def state_json(progress: Progress) -> dict:
    """Полное состояние для бота: счётчики, текущий шаг и список документов.

    Текущий шаг сервер выбирает сам. Бот не спрашивает «покажи седьмую
    карточку» — кнопка в Telegram живёт в чате вечно, и порядок,
    держащийся на ней, перестаёт быть порядком.
    """
    required = progress.required_policies
    current_section = None
    following = progress.next_section
    if following is not None:
        found = progress.section_at(following.position)
        if found is not None:
            current_section = section_json(found, total=progress.sections_total)

    current_policy = None
    upcoming = progress.next_policy
    if upcoming is not None:
        position = required.index(upcoming) + 1
        current_policy = policy_json(
            upcoming, index=position, total=progress.policies_total
        )

    return {
        "status": progress.status,
        "stage": progress.stage,
        "completed": progress.completed,
        "info_completed": progress.info_completed,
        "has_declined": progress.has_declined,
        "sections_done": progress.sections_done,
        "sections_total": progress.sections_total,
        "policies_done": progress.policies_done,
        "policies_total": progress.policies_total,
        "started_at": (
            progress.onboarding.started_at.isoformat()
            if progress.onboarding.started_at else None
        ),
        "completed_at": (
            progress.onboarding.completed_at.isoformat()
            if progress.onboarding.completed_at else None
        ),
        "section": current_section,
        "policy": current_policy,
        # Весь список — для раздела «Правила и документы». Он открыт и до
        # завершения: человек имеет право перечитать то, с чем согласился,
        # и то, с чем ещё не согласился.
        "policies": [
            policy_json(one, index=number, total=progress.policies_total)
            for number, one in enumerate(required, start=1)
        ],
        "sections": [
            section_json(one, total=progress.sections_total)
            for one in progress.sections
        ],
    }


def row_json(row) -> dict:
    """Строка списка «Ознакомление» для CRM.

    Счётчики берутся из пересчёта, а не из колонки `status`: колонка —
    витрина, и после публикации новой редакции документа она успевает
    устареть раньше, чем кадровик откроет страницу.
    """
    progress = row.progress
    onboarding = progress.onboarding
    invitation = row.invitation
    return {
        "employee_id": str(row.employee.id),
        "full_name": " ".join(
            one for one in
            [row.employee.last_name, row.employee.first_name,
             row.employee.middle_name]
            if one
        ),
        "employee_number": row.employee.employee_number,
        "office_name": row.office_name,
        "department_name": row.department_name,
        "position_name": row.position_name,
        "telegram_state": row.telegram_state,
        "status": progress.status,
        "stage": progress.stage,
        "completed": progress.completed,
        "sections_done": progress.sections_done,
        "sections_total": progress.sections_total,
        "policies_done": progress.policies_done,
        "policies_total": progress.policies_total,
        "invited_at": onboarding.invited_at,
        "started_at": onboarding.started_at,
        "completed_at": onboarding.completed_at,
        "last_reminder_at": onboarding.last_reminder_at,
        "invitation_status": invitation.status if invitation else None,
        "invitation_expires_at": invitation.expires_at if invitation else None,
        "enrolled_at": onboarding.created_at,
        "due_date": onboarding.due_date,
        "overdue": "overdue" in row.reasons,
        "reasons": list(row.reasons),
        "group": row.group,
        "materials": list(row.materials),
    }


__all__ = ["policy_json", "row_json", "section_json", "state_json"]
