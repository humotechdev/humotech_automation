"""Три вещи, которые Django ORM делает не так, как делал SQLAlchemy.

Проверяются до переноса сервисов: каждая из них меняет форму кода во всех
сервисах сразу, и обнаружить её после написания сорока тестов дороже,
чем до написания первого.
"""

from __future__ import annotations

import pytest
from django.db import IntegrityError, transaction

from humotech.organizations.models import Organization
from humotech.rbac.models import Permission, Role, RolePermission

pytestmark = pytest.mark.django_db


def _organization(code: str = "SMOKE") -> Organization:
    return Organization.objects.create(
        code=code, name="Проверка", default_timezone="Asia/Dushanbe",
        status="ACTIVE",
    )


# --- 1. Значения, которые проставляет база ---------------------------------

def test_db_defaults_are_available_on_the_created_object():
    """`id` и `created_at` генерирует PostgreSQL, а не Python.

    Постраничный вывод строит курсор из `(created_at, id)` только что
    созданного объекта. Если Django не возвращает эти значения, курсор
    построить не из чего — и выяснить это надо до, а не после сервисов.
    """
    org = _organization()
    assert org.id is not None, "первичный ключ не вернулся из базы"
    assert org.created_at is not None, "время создания не вернулось из базы"
    assert org.knowledge_revision == 1, "значение по умолчанию не вернулось"


def test_rows_of_one_transaction_share_created_at():
    """`now()`, а не `statement_timestamp()`.

    На равенстве времени у пачки строк держится ключ постраничного вывода:
    именно поэтому ключ — пара `(created_at, id)`, а не одно время.
    """
    with transaction.atomic():
        first = _organization("BATCH1")
        second = _organization("BATCH2")

    first.refresh_from_db()
    second.refresh_from_db()
    assert first.created_at == second.created_at, (
        "время создания разошлось внутри одной транзакции — "
        "значит, в схеме statement_timestamp(), а не now()"
    )


# --- 2. Транзакция и перевод ошибки целостности ----------------------------

def test_integrity_error_outside_atomic_leaves_connection_usable():
    """Ловить `IntegrityError` надо СНАРУЖИ `atomic()`, а не внутри.

    Внутри блока после ошибки любой следующий запрос даёт
    `TransactionManagementError`: блок уже помечен на откат. Именно поэтому
    `BaseService.atomic` оборачивает `transaction.atomic()` снаружи.
    """
    _organization("DUP")

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            _organization("dup")  # тот же код в другом регистре

    # соединение живо: точка отката сняла только неудавшуюся операцию
    assert Organization.objects.filter(code="DUP").count() == 1
    _organization("AFTER")
    assert Organization.objects.filter(code="AFTER").exists()


def test_query_inside_broken_atomic_block_fails_loudly():
    """Обратная проверка: внутри сломанного блока работать нельзя.

    Тест закрепляет причину, по которой `try` стоит снаружи: иначе перевод
    ошибки в понятное сообщение сам упал бы на попытке что-то прочитать.
    """
    from django.db.transaction import TransactionManagementError

    _organization("INNER")
    with pytest.raises(TransactionManagementError):
        with transaction.atomic():
            try:
                _organization("inner")
            except IntegrityError:
                # запрос в уже сломанном блоке
                Organization.objects.count()


# --- 3. Составной первичный ключ -------------------------------------------

def test_composite_primary_key_create_and_get():
    """`role_permissions` живёт на паре (роль, разрешение) без суррогатного id.

    Возможность появилась только в Django 5.2 и ограничений у неё хватает,
    поэтому базовые операции проверяются отдельно: на этой таблице держится
    вся выборка разрешений.
    """
    role = Role.objects.create(code="TEST_ROLE", name="Тестовая роль")
    permission = Permission.objects.create(
        code="test.permission", name="Тестовое разрешение"
    )

    link = RolePermission.objects.create(role=role, permission=permission)
    assert link.pk == (role.id, permission.id)

    found = RolePermission.objects.get(role=role, permission=permission)
    assert found.permission_id == permission.id
    assert found.created_at is not None

    # связка уникальна: повтор нарушает первичный ключ
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            RolePermission.objects.create(role=role, permission=permission)

    # выборка разрешений роли — то, ради чего таблица существует
    codes = set(
        Permission.objects.filter(role_links__role=role).values_list(
            "code", flat=True
        )
    )
    assert codes == {"test.permission"}
