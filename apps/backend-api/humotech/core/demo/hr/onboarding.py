"""Ознакомления: разделы, восемь материалов с версиями и назначения.

Три документа уже есть на стенде (правила распорядка, политика данных,
кодекс) — они становятся материалами витрины, а не дублируются: им
назначается раздел. Остальные пять заводятся с версиями. «Пожарная
безопасность» выпущена в новой версии пять дней назад — у части
сотрудников она ждёт повторного подтверждения.

Группы участников (завершили, новая версия, просрочили, не начали, ждут
Telegram, в процессе) получаются из подтверждений и сроков — ни одного
записанного итога. Статус-витрину пересчитывает сама система.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from humotech.core.demo.hr import catalog as cat
from humotech.core.demo.hr.people import Person, did, rng
from humotech.onboarding import progress as progress_module
from humotech.onboarding.models import (
    EmployeeOnboarding,
    EmployeePolicyAcceptance,
    EmployeeSectionAcknowledgement,
    OnboardingProgram,
    PolicyCategory,
    PolicyDocument,
    PolicyDocumentVersion,
)


def build(org, people: list[Person], today: date, now: datetime, tz, reviewer) -> dict:
    program = OnboardingProgram.objects.filter(organization=org, is_active=True, archived_at__isnull=True).first()
    if program is None:
        return {"skipped": "нет программы ознакомления — выполните seed_onboarding"}
    sections = list(progress_module.active_sections(program.id))

    def at(day: date, hour: int = 10, minute: int = 0) -> datetime:
        return datetime.combine(day, time(hour, minute), tzinfo=tz)

    # Разделы: ответственный — человек с нужной должностью, иначе руководитель отдела.
    categories = {}
    for position, (code, title, description, owner_title) in enumerate(cat.CATEGORIES, start=1):
        owner = _owner(people, owner_title)
        row = PolicyCategory.objects.create(
            id=did("category", code), organization=org, title=title, description=description,
            owner_employee=owner.employee if owner else None, position=position, created_by_user=reviewer,
        )
        PolicyCategory.objects.filter(id=row.id).update(created_at=at(today - timedelta(days=120)),
                                                        updated_at=at(today - timedelta(days=10 * position)))
        categories[code] = row

    # Материалы: свои с версиями и три уже существующих.
    live: dict[str, PolicyDocumentVersion] = {}
    earlier: dict[str, PolicyDocumentVersion] = {}
    for position, (key, category, title, description, versions, adopt) in enumerate(cat.MATERIALS, start=1):
        if adopt:
            document = PolicyDocument.objects.filter(organization=org, title=adopt, archived_at__isnull=True).first()
            if document is None:
                continue
            PolicyDocument.objects.filter(id=document.id).update(category=categories[category], position=position)
            current = PolicyDocumentVersion.objects.filter(document=document, status="PUBLISHED").first()
            if current:
                live[key] = current
            continue
        document = PolicyDocument.objects.create(
            id=did("material", key), organization=org, code=f"{cat.MARK}-{key}", title=title,
            description=description, is_mandatory=True, position=position, category=categories[category],
            created_by_user=reviewer,
        )
        for index, number in enumerate(versions):
            newest = index == len(versions) - 1
            published = at(today - timedelta(days=5 if key == "FIRE" and newest else 150 - index * 60 - position))
            version = PolicyDocumentVersion.objects.create(
                id=did("material-version", key, number), organization=org, document=document, version=number,
                summary=f"{title}: основные положения. Прочитайте и подтвердите ознакомление.",
                body=f"{description}\n\nПолный текст материала доступен в разделе документов компании.",
                agree_label="Ознакомлен(а)", status="PUBLISHED" if newest else "ARCHIVED",
                published_at=published, published_by_user=reviewer,
            )
            PolicyDocumentVersion.objects.filter(id=version.id).update(created_at=published - timedelta(days=2))
            if newest:
                live[key] = version
            else:
                earlier[key] = version
        PolicyDocument.objects.filter(id=document.id).update(created_at=at(today - timedelta(days=160)))

    # Группы участников. 25 без живого Telegram: 12 когда-то прошли
    # ознакомление (привязка отозвана позже), 13 ждут подключения.
    active = [p for p in people if p.active]
    linked = [p for p in active if p.telegram == "ACTIVE"]
    groups: dict[str, list[Person]] = {"done": [], "renewal": [], "overdue": [], "fresh": [], "progress": [], "waiting": []}
    for person in active:
        if person.telegram == "REVOKED":
            groups["done"].append(person)
        elif person.telegram == "NONE":
            groups["waiting"].append(person)
    newest = sorted(linked, key=lambda p: p.hire, reverse=True)
    fresh = newest[:6]
    groups["fresh"] = fresh
    rest = [p for p in linked if p not in fresh]
    pick = rng("onboarding-groups")
    pick.shuffle(rest)
    groups["renewal"] = rest[:10]
    groups["overdue"] = rest[10:13]
    groups["progress"] = rest[13:19]
    groups["done"] += rest[19:]

    rows, acks, accepts = [], [], []
    for group, members in groups.items():
        for person in members:
            r = rng("onboarding", person.number)
            enrolled = at(max(person.hire, today - timedelta(days=200)) + timedelta(days=r.randint(0, 3)), 9)
            if group == "fresh":
                enrolled = at(today - timedelta(days=r.randint(0, 1)), 9, 30)
            if group == "waiting":
                # Ждут подключения к Telegram: назначено недавно, срок впереди.
                enrolled = at(today - timedelta(days=r.randint(1, 5)), 9)
            if group in ("overdue", "progress"):
                enrolled = at(today - timedelta(days=(20 if group == "overdue" else 6) + r.randint(0, 4)), 9)
            if enrolled > now:
                enrolled = now - timedelta(hours=2)
            due = enrolled.date() + timedelta(days=14)
            onboarding = EmployeeOnboarding(
                id=did("onboarding", person.number), organization=org, employee=person.employee, program=program,
                status="NOT_STARTED", invited_at=enrolled, due_date=due, created_by_user=reviewer,
            )
            started = enrolled + timedelta(hours=r.randint(1, 30))
            if group in ("done", "renewal"):
                read = _read(org, onboarding, sections, started, acks)
                moment = read + timedelta(minutes=10)
                for key, version in live.items():
                    if key == "FIRE" and key in earlier:
                        old = earlier[key]
                        when = max(moment, old.published_at + timedelta(days=r.randint(1, 5)))
                        accepts.append(_accept(org, person, old, when))
                        if group == "renewal":
                            continue
                        when = max(moment, version.published_at + timedelta(hours=r.randint(2, 90)))
                        when = min(when, now - timedelta(minutes=20))
                    else:
                        when = max(moment, version.published_at + timedelta(days=r.randint(1, 4)))
                        when = min(when, now - timedelta(minutes=20))
                    accepts.append(_accept(org, person, version, when))
                    moment = max(moment, when)
                onboarding.started_at = started
                onboarding.info_completed_at = read
                onboarding.completed_at = moment if group == "done" else read + timedelta(hours=1)
                onboarding.last_reminder_at = at(today - timedelta(days=3), 11) if group == "renewal" else None
            elif group in ("overdue", "progress"):
                onboarding.started_at = started
                share = 0.5 if group == "overdue" else 0.8
                _read(org, onboarding, sections[: max(1, int(len(sections) * share))], started, acks)
                onboarding.last_reminder_at = at(today - timedelta(days=2), 11)
            # «fresh» и «waiting» ещё не начинали.
            rows.append(onboarding)
            EmployeeOnboarding.objects.bulk_create([onboarding])
            EmployeeOnboarding.objects.filter(id=onboarding.id).update(created_at=enrolled, updated_at=enrolled)

    # Новая версия: срок на повторное подтверждение — две недели от выпуска.
    fire = live.get("FIRE")
    if fire is not None:
        for person in groups["renewal"]:
            EmployeeOnboarding.objects.filter(employee=person.employee).update(
                due_date=fire.published_at.astimezone(tz).date() + timedelta(days=14))

    # Просрочившие: срок прошёл, ознакомление не завершено.
    for person in groups["overdue"]:
        EmployeeOnboarding.objects.filter(employee=person.employee).update(due_date=today - timedelta(days=2 + person.order % 4))

    EmployeeSectionAcknowledgement.objects.bulk_create(acks, batch_size=1000)
    EmployeePolicyAcceptance.objects.bulk_create(accepts, batch_size=1000)
    progress_module.resync(org.id, now=now)
    return {name: len(members) for name, members in groups.items()} | {"materials": len(live)}


def _owner(people: list[Person], title: str):
    for person in people:
        if not person.active:
            continue
        spec = next(d for d in cat.DEPARTMENTS if d.code == person.department)
        if person.head and spec.head == title:
            return person
    admin = [p for p in people if p.active and p.department == "ADMIN" and not p.head]
    return admin[0] if admin else None


def _read(org, onboarding, sections, started: datetime, acks: list) -> datetime:
    moment = started
    for section in sections:
        moment = moment + timedelta(minutes=2)
        acks.append(EmployeeSectionAcknowledgement(
            id=did("ack", onboarding.id, section.id), organization=org, onboarding=onboarding, section=section,
            section_version=section.version, acknowledged_at=moment,
        ))
    return moment


def _accept(org, person: Person, version, when: datetime) -> EmployeePolicyAcceptance:
    return EmployeePolicyAcceptance(
        id=did("accept", person.number, version.id), organization=org, employee=person.employee, version=version,
        decision="ACCEPTED", decided_at=when,
    )
