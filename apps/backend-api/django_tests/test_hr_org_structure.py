"""Регионы и офисы: права, изоляция организаций, списки, аудит.

Перенос `tests/integration/test_hr_org_structure.py` на Django ORM. Сценарии
сохранены один в один — меняется способ обращения к базе, а не то, что
проверяется. Всё выполняется на настоящем PostgreSQL: часть правил живёт
в самой схеме — уникальные ключи и внешние ключи с RESTRICT.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from django.db import IntegrityError, connection, transaction

from humotech.audit.models import AuditLog
from humotech.core.errors import (
    Conflict,
    NotFound,
    PermissionDenied,
    ValidationFailed,
)
from humotech.offices.models import Office
from humotech.offices.services import OfficeService
from humotech.regions.models import Region
from humotech.regions.services import RegionService

pytestmark = pytest.mark.django_db


# --- 1. HR создаёт регион и офис -------------------------------------------

def test_hr_creates_region_and_office(organization, hr_actor):
    regions = RegionService()
    offices = OfficeService()

    region = regions.create(
        hr_actor, code="sughd", name="Согд", timezone="Asia/Dushanbe"
    )
    assert region.code == "SUGHD", "код справочника приводится к верхнему регистру"
    assert region.status == "ACTIVE"

    office = offices.create(
        hr_actor,
        region_id=region.id,
        code="khujand-1",
        name="Офис Худжанд",
        address="ул. Ленина, 10",
        timezone="Asia/Dushanbe",
        opened_at=date(2025, 1, 1),
    )
    assert office.region_id == region.id
    assert office.organization_id == organization.id
    assert office.status == "ACTIVE"

    assert offices.get(hr_actor, office.id).name == "Офис Худжанд"
    assert regions.get(hr_actor, region.id).name == "Согд"


# --- 18. Часовой пояс офиса ------------------------------------------------

def test_office_timezone_is_stored_and_usable(region, hr_actor):
    """Пояс сохраняется как есть и годится для перевода UTC в местное время."""
    from datetime import datetime, timezone as tz
    from zoneinfo import ZoneInfo

    office = OfficeService().create(
        hr_actor, region_id=region.id, code="TASH", name="Офис",
        address="адрес", timezone="Asia/Tashkent",
    )
    assert office.timezone == "Asia/Tashkent"

    # значение из базы, а не из объекта в памяти
    with connection.cursor() as cursor:
        cursor.execute("SELECT timezone FROM offices WHERE id = %s", [office.id])
        stored = cursor.fetchone()[0]
    assert stored == "Asia/Tashkent"

    moment = datetime(2025, 6, 1, 4, 0, tzinfo=tz.utc)
    assert moment.astimezone(ZoneInfo(stored)).hour == 9, (
        "04:00 UTC в Ташкенте — это 09:00 по местному времени"
    )


def test_unknown_timezone_is_rejected(region, hr_actor):
    with pytest.raises(ValidationFailed, match="часовой пояс"):
        OfficeService().create(
            hr_actor, region_id=region.id, code="BAD", name="Офис",
            address="адрес",
            timezone="Asia/Dushambe",  # опечатка в названии зоны
        )


# --- 12. Без разрешения — отказ --------------------------------------------

def test_user_without_permission_is_refused(region, nobody_actor):
    with pytest.raises(PermissionDenied, match="regions.read"):
        RegionService().list(nobody_actor)
    with pytest.raises(PermissionDenied, match="regions.manage"):
        RegionService().create(nobody_actor, code="X", name="X")


def test_granted_user_succeeds_where_ungranted_fails(organization, make_actor):
    """Отказ должен наступать из-за отсутствия права, а не из-за пустой базы.

    Без этой проверки тест на отказ прошёл бы и в том случае, если таблица
    разрешений просто не заполнена — то есть не проверял бы ничего.
    """
    granted = make_actor(organization, permissions=("regions.read", "regions.manage"))
    denied = make_actor(organization, permissions=("offices.read",))

    RegionService().create(granted, code="OK", name="Годный")
    with pytest.raises(PermissionDenied):
        RegionService().create(denied, code="NO", name="Нет")


# --- 13. Только чтение не меняет данные ------------------------------------

def test_readonly_user_cannot_change_anything(region, office, readonly_actor):
    regions = RegionService()
    offices = OfficeService()

    # чтение работает
    assert regions.get(readonly_actor, region.id).id == region.id
    assert offices.get(readonly_actor, office.id).id == office.id

    with pytest.raises(PermissionDenied, match="regions.manage"):
        regions.update(readonly_actor, region.id, name="Переименован")
    with pytest.raises(PermissionDenied, match="regions.manage"):
        regions.deactivate(readonly_actor, region.id)
    with pytest.raises(PermissionDenied, match="offices.manage"):
        offices.update(readonly_actor, office.id, name="Переименован")
    with pytest.raises(PermissionDenied, match="offices.manage"):
        offices.deactivate(readonly_actor, office.id)

    region.refresh_from_db()
    assert region.name == "Душанбе"


# --- 14. Данные соседней организации недоступны ----------------------------

def test_other_organization_data_is_invisible(
    hr_actor, foreign_region, foreign_office, foreign_actor, region, office
):
    regions = RegionService()
    offices = OfficeService()

    # чужой объект отвечает так же, как несуществующий
    with pytest.raises(NotFound):
        regions.get(hr_actor, foreign_region.id)
    with pytest.raises(NotFound):
        offices.get(hr_actor, foreign_office.id)
    with pytest.raises(NotFound):
        offices.update(hr_actor, foreign_office.id, name="Захвачено")

    # и в обратную сторону
    with pytest.raises(NotFound):
        regions.get(foreign_actor, region.id)
    with pytest.raises(NotFound):
        offices.get(foreign_actor, office.id)

    # список не показывает чужие строки
    visible = {item.id for item in regions.list(hr_actor).items}
    assert foreign_region.id not in visible
    assert region.id in visible


# --- 11. Офис другой организации нельзя использовать -----------------------

def test_office_cannot_be_created_in_foreign_region(hr_actor, foreign_region):
    with pytest.raises(NotFound, match="Регион не найден"):
        OfficeService().create(
            hr_actor, region_id=foreign_region.id, code="X", name="X",
            address="адрес", timezone="Asia/Dushanbe",
        )


# --- область видимости внутри своей организации ----------------------------

def test_regional_hr_sees_only_own_region(
    organization, region, other_region, office, other_office, make_actor
):
    actor = make_actor(
        organization,
        permissions=("regions.read", "offices.read", "offices.manage"),
        region=region,
    )
    offices = OfficeService()

    visible = {item.id for item in offices.list(actor).items}
    assert visible == {office.id}

    with pytest.raises(PermissionDenied, match="области видимости"):
        offices.get(actor, other_office.id)

    # регион своего офиса виден, чужой — нет
    region_ids = {item.id for item in RegionService().list(actor).items}
    assert region_ids == {region.id}


def test_office_scoped_user_sees_region_of_that_office(
    organization, region, other_region, office, make_actor
):
    """Администратору офиса нужен регион его офиса: карточка офиса на него
    ссылается, и невидимый регион сделал бы её нечитаемой."""
    actor = make_actor(
        organization, permissions=("regions.read", "offices.read"), office=office
    )
    region_ids = {item.id for item in RegionService().list(actor).items}
    assert region_ids == {region.id}
    assert other_region.id not in region_ids


def test_empty_scope_gives_nothing_not_everything(
    organization, region, office, other_region, make_actor
):
    """Пустая область — это «ничего», а не «всё».

    Различие тонкое и опасное: `visible_office_ids` возвращает None для
    доступа ко всей организации и пустое множество для отсутствия доступа.
    Проверка вида `if visible:` считает ложью и то и другое — и молча выдаёт
    всю организацию тому, кто не должен видеть ни одного офиса.

    Случай не выдуманный: региональному HR выдали регион, в котором офисов
    ещё нет. Разрешения у него при этом полноценные.
    """
    assert not Office.objects.filter(region=other_region).exists(), (
        "предпосылка теста: во втором регионе нет офисов"
    )

    actor = make_actor(
        organization,
        permissions=("regions.read", "offices.read"),
        region=other_region,
    )
    service = RegionService()
    assert "offices.read" in service.access.permissions(actor), (
        "разрешения на месте — пустой оказывается именно область"
    )
    assert OfficeService().list(actor).items == []
    assert {item.id for item in service.list(actor).items} == {other_region.id}
    with pytest.raises(PermissionDenied):
        OfficeService().get(actor, office.id)


def test_expired_scope_leaves_no_permissions_at_all(
    organization, office, make_actor, now
):
    """Истёкшая роль перестаёт давать и разрешения, и область."""
    actor = make_actor(
        organization,
        permissions=("regions.read", "offices.read"),
        office=office,
        valid_from=now - timedelta(days=10),
        valid_to=now - timedelta(days=1),
    )
    with pytest.raises(PermissionDenied, match="offices.read"):
        OfficeService().list(actor)


# --- 17. Повторяющиеся уникальные данные -----------------------------------

def test_duplicate_region_code_gives_controlled_error(organization, hr_actor):
    regions = RegionService()
    regions.create(hr_actor, code="DUP", name="Первый")

    with pytest.raises(Conflict) as info:
        regions.create(hr_actor, code="DUP", name="Второй")

    assert info.value.code == "conflict"
    assert "кодом" in info.value.message
    assert info.value.details["constraint"] == "uq_regions_org_code"
    # соединение остаётся рабочим: ошибка снята точкой отката,
    # а не всей транзакцией
    assert regions.list(hr_actor).items


def test_duplicate_office_code_gives_controlled_error(region, hr_actor):
    offices = OfficeService()
    payload = dict(region_id=region.id, name="Офис", address="адрес",
                   timezone="Asia/Dushanbe")
    offices.create(hr_actor, code="SAME", **payload)

    with pytest.raises(Conflict) as info:
        offices.create(hr_actor, code="SAME", **payload)
    assert info.value.details["constraint"] == "uq_offices_org_code"


def test_same_code_in_two_organizations_is_allowed(
    hr_actor, foreign_actor, region, foreign_region
):
    """Уникальность кода — внутри организации, а не глобально."""
    offices = OfficeService()
    offices.create(hr_actor, region_id=region.id, code="HQ", name="Офис",
                   address="адрес", timezone="Asia/Dushanbe")
    offices.create(foreign_actor, region_id=foreign_region.id, code="HQ",
                   name="Офис", address="адрес", timezone="Asia/Tashkent")


# --- деактивация не трогает историю ----------------------------------------

def test_office_deactivation_keeps_employees_and_history(
    office, employee, hr_actor
):
    from humotech.employees.models import Employee, EmployeeAssignment

    offices = OfficeService()
    result = offices.deactivate(hr_actor, office.id)
    assert result.status == "INACTIVE"

    assert Employee.objects.filter(id=employee.id).exists()
    assignments = list(EmployeeAssignment.objects.filter(employee=employee))
    assert len(assignments) == 1
    assert assignments[0].office_id == office.id

    assert offices.reactivate(hr_actor, office.id).status == "ACTIVE"


def test_office_with_history_cannot_be_physically_deleted(office, employee):
    """Физическое удаление офиса запрещает сама база: ON DELETE RESTRICT.

    Сервис удаления не предоставляет вовсе; этот тест закрепляет, что и в обход
    сервиса, и в обход ORM историю снести не получится.
    """
    with pytest.raises(IntegrityError):
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("DELETE FROM offices WHERE id = %s", [office.id])


def test_region_with_offices_cannot_be_physically_deleted(region, office):
    with pytest.raises(IntegrityError):
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("DELETE FROM regions WHERE id = %s", [region.id])


def test_repeated_deactivation_is_a_conflict(office, hr_actor):
    offices = OfficeService()
    offices.deactivate(hr_actor, office.id)
    with pytest.raises(Conflict, match="уже в статусе"):
        offices.deactivate(hr_actor, office.id)


def test_closed_office_cannot_be_reactivated(office, hr_actor):
    """CLOSED — это «закрыт насовсем», отдельный от INACTIVE статус."""
    offices = OfficeService()
    offices.close(hr_actor, office.id, closed_at=date(2025, 5, 1))
    with pytest.raises(Conflict, match="закрыт"):
        offices.reactivate(hr_actor, office.id)


def test_new_office_cannot_be_added_to_inactive_region(region, hr_actor):
    regions = RegionService()
    regions.deactivate(hr_actor, region.id)
    with pytest.raises(Conflict, match="Регион не активен"):
        OfficeService().create(
            hr_actor, region_id=region.id, code="NEW", name="Новый",
            address="адрес", timezone="Asia/Dushanbe",
        )


# --- 5. Поиск, фильтры, пагинация ------------------------------------------

def test_search_filter_and_keyset_pagination(region, hr_actor):
    offices = OfficeService()
    for index in range(7):
        offices.create(
            hr_actor, region_id=region.id, code=f"OF{index}",
            name=("Склад" if index % 2 else "Офис") + f" {index}",
            address=f"ул. Тестовая, {index}",
            timezone="Asia/Dushanbe",
        )
    offices.deactivate(hr_actor, offices.list(hr_actor, search="Офис 0").items[0].id)

    # поиск по названию
    found = offices.list(hr_actor, search="Склад")
    assert {item.name for item in found.items} == {"Склад 1", "Склад 3", "Склад 5"}

    # поиск по адресу
    assert offices.list(hr_actor, search="Тестовая, 4").size == 1

    # фильтр по статусу
    assert offices.list(hr_actor, status="INACTIVE").size == 1
    assert offices.list(hr_actor, status="ACTIVE").size == 6

    # фильтр по региону
    assert offices.list(hr_actor, region_id=region.id).size == 7

    # обход страницами: все строки ровно по одному разу
    seen: list = []
    cursor = None
    pages = 0
    while True:
        page = offices.list(hr_actor, limit=3, cursor=cursor)
        seen.extend(item.id for item in page.items)
        pages += 1
        if not page.next_cursor:
            break
        cursor = page.next_cursor
        assert pages < 10, "постраничный обход не сходится"

    assert len(seen) == 7
    assert len(set(seen)) == 7, "строка показана дважды"
    assert pages == 3


def test_rows_created_in_one_transaction_are_all_returned(region, hr_actor):
    """У записей одной транзакции `created_at` совпадает.

    Курсор по одному только времени потерял бы часть строк; ключ из пары
    (created_at, id) этого не допускает.
    """
    offices = OfficeService()
    with transaction.atomic():
        for index in range(5):
            offices.create(hr_actor, region_id=region.id, code=f"BATCH{index}",
                           name=f"Офис {index}", address="адрес",
                           timezone="Asia/Dushanbe")

    stamps = {item.created_at for item in offices.list(hr_actor).items}
    assert len(stamps) == 1, "предпосылка теста: время создания одинаковое"

    seen, cursor = [], None
    while True:
        page = offices.list(hr_actor, limit=2, cursor=cursor)
        seen.extend(item.id for item in page.items)
        if not page.next_cursor:
            break
        cursor = page.next_cursor
    assert len(set(seen)) == 5


def test_broken_cursor_is_a_validation_error(hr_actor):
    with pytest.raises(ValidationFailed, match="курсор"):
        OfficeService().list(hr_actor, cursor="не-курсор")


def test_region_list_counts_offices_without_extra_queries(
    region, other_region, office, hr_actor, django_assert_num_queries
):
    """Число офисов приходит тем же запросом, что и сами регионы."""
    service = RegionService()
    service.access.permissions(hr_actor)  # прогреваем кэш прав отдельно

    with django_assert_num_queries(2):  # область видимости + сама страница
        items = {item.code: item.offices_count for item in
                 service.list(hr_actor).items}
    assert items == {"DUSHANBE": 1, "KHUJAND": 0}


# --- 15. Аудит --------------------------------------------------------------

def test_every_change_is_written_to_audit_log(organization, region, hr_actor):
    regions = RegionService()
    offices = OfficeService()

    created = regions.create(hr_actor, code="AUD", name="Аудит")
    regions.update(hr_actor, created.id, name="Аудит 2")
    regions.deactivate(hr_actor, created.id)
    office = offices.create(hr_actor, region_id=region.id, code="AUDOF",
                            name="Офис", address="адрес",
                            timezone="Asia/Dushanbe")
    offices.update(hr_actor, office.id, address="Новый адрес")
    offices.deactivate(hr_actor, office.id)

    entries = list(
        AuditLog.objects.filter(organization=organization).order_by("occurred_at")
    )
    assert [entry.action for entry in entries] == [
        "region.create", "region.update", "region.deactivate",
        "office.create", "office.update", "office.deactivate",
    ]

    for entry in entries:
        assert entry.organization_id == organization.id
        assert entry.actor_user_id == hr_actor.user_id
        assert entry.entity_type in ("regions", "offices")
        assert entry.entity_id is not None
        assert entry.occurred_at is not None

    update_entry = entries[1]
    assert update_entry.old_values["name"] == "Аудит"
    assert update_entry.new_values["name"] == "Аудит 2"

    address_entry = entries[4]
    assert address_entry.old_values["address"] == "адрес"
    assert address_entry.new_values["address"] == "Новый адрес"


def test_audit_never_stores_secrets(hr_actor):
    """Фильтр секретов работает на составе полей, а не на добросовестности
    вызывающего: сервис может передать лишнее по ошибке."""
    from humotech.core.rbac import AuditTrail

    entry = AuditTrail().record(
        hr_actor, action="test.secret", entity_type="users",
        entity_id=hr_actor.user_id,
        after={"email": "hr@humotech.tj", "password_hash": "$argon2id$v=19$...",
               "api_key": "sk-test", "static_token_hash": "abc"},
    )
    assert entry.new_values == {"email": "hr@humotech.tj"}
