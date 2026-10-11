"""Django Admin как аварийный интерфейс.

Это НЕ основная CRM: та будет отдельным React-приложением поверх REST API,
и все обычные операции идут через него — там работают доменные права,
область видимости и журнал аудита.

Админка нужна для другого: посмотреть и поправить данные, когда что-то
пошло не так и обычный путь недоступен. Отсюда её устройство:

  * вход только для `SUPER_ADMIN` (см. `accounts.User.is_staff`);
  * всё открывается на чтение, а изменение разрешено лишь там, где оно
    осмысленно в аварийной ситуации;
  * удаление выключено везде. Историю сотрудников и отметок нельзя
    удалять из интерфейса — на уровне базы это и так запрещено
    внешними ключами, и обходить их через админку тем более незачем.

И главное: правка отсюда попадает в тот же журнал, что и правка через
API. Аварийный вход, не оставляющий следов, — это дыра, а не удобство:
роль, выданную через админку, иначе нельзя было бы отличить от роли,
которая была всегда. Действие помечается префиксом `admin.`, чтобы
происхождение записи читалось сразу.
"""

from __future__ import annotations

from django.contrib import admin

from humotech.core.clientip import client_ip
from humotech.core.rbac import Actor, AuditTrail, snapshot


def _has_field(model, name: str) -> bool:
    try:
        model._meta.get_field(name)
    except Exception:
        return False
    return True


class OrganizationScopedAdmin(admin.ModelAdmin):
    """Админка видит и правит только организацию вошедшего.

    `is_staff` даёт SUPER_ADMIN любой организации, а наборы записей
    админки раньше не фильтровались: администратор организации A читал и
    правил пользователей, роли и сотрудников организации B — вплоть до
    выдачи себе роли в B (аудит 11.10, M-7). Теперь:

      * списки и карточки — только строки своей организации (у самой
        организации — только своя запись);
      * выпадающие списки связей — тоже только своя организация, иначе
        чужую запись подставили бы через форму;
      * системная запись без организации (общая роль) открывается на
        чтение, но не правится: правка задела бы все организации.
    """

    def _organization_id(self, request):
        return getattr(request.user, "organization_id", None)

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        mine = self._organization_id(request)
        model = queryset.model
        if model._meta.db_table == "organizations":
            return queryset.filter(pk=mine)
        if _has_field(model, "organization"):
            field = model._meta.get_field("organization")
            if field.null:
                from django.db.models import Q

                return queryset.filter(Q(organization_id=mine) | Q(organization__isnull=True))
            return queryset.filter(organization_id=mine)
        return queryset

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        related = db_field.related_model
        mine = self._organization_id(request)
        if related._meta.db_table == "organizations":
            kwargs["queryset"] = related.objects.filter(pk=mine)
        elif _has_field(related, "organization"):
            from django.db.models import Q

            kwargs["queryset"] = related.objects.filter(
                Q(organization_id=mine) | Q(organization__isnull=True)
                if related._meta.get_field("organization").null
                else Q(organization_id=mine)
            )
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        related = db_field.related_model
        if _has_field(related, "organization"):
            kwargs["queryset"] = related.objects.filter(
                organization_id=self._organization_id(request))
        return super().formfield_for_manytomany(db_field, request, **kwargs)

    def has_change_permission(self, request, obj=None) -> bool:
        if obj is not None and _has_field(type(obj), "organization")                 and getattr(obj, "organization_id", None) is None:
            return False
        return super().has_change_permission(request, obj)


class ReadOnlyAdmin(OrganizationScopedAdmin):
    """Только просмотр и поиск. Ничего не создаёт и не удаляет."""

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False


class OperationalAdmin(OrganizationScopedAdmin):
    """Просмотр и правка, но без удаления. Каждая правка — в журнал.

    Удаление закрыто намеренно: у этих таблиц есть история, и внешние ключи
    с `ON DELETE RESTRICT` всё равно не дадут снести строку со связями —
    кнопка в интерфейсе только создавала бы ложное ожидание.
    """

    def has_delete_permission(self, request, obj=None) -> bool:
        return False

    def save_model(self, request, obj, form, change) -> None:
        """Записать изменение в тот же журнал, что и правку через API.

        Снимок «до» читается из базы ДО сохранения: после `super()` в
        объекте уже новые значения, и разница исчезла бы.

        Двойной записи не возникает: админка работает с ORM напрямую и
        сервисов не вызывает — а именно сервисы пишут журнал на обычном
        пути. Если когда-нибудь какой-то `ModelAdmin` начнёт звать
        сервис, он не должен наследоваться отсюда.
        """
        before = _previous_values(obj, form) if change else None
        super().save_model(request, obj, form, change)
        _record_admin_change(request, obj, form, change=change, before=before)


def _changed_names(obj, form) -> tuple[str, ...]:
    """Имена изменённых полей в том виде, в каком их читать с объекта.

    Для ссылки берётся `attname` (`organization_id`), а не само поле:
    иначе в журнал попало бы человекочитаемое имя связанной записи, по
    которому потом не найти строку.
    """
    names = []
    for field_name in form.changed_data:
        try:
            field = obj._meta.get_field(field_name)
        except Exception:
            # Поле формы без поля модели — записывать по нему нечего.
            continue
        names.append(field.attname if field.is_relation else field.name)
    return tuple(names)


def _previous_values(obj, form) -> dict | None:
    previous = type(obj).objects.filter(pk=obj.pk).first()
    if previous is None:
        return None
    return snapshot(previous, _changed_names(obj, form))


def _record_admin_change(request, obj, form, *, change: bool, before) -> None:
    changed = _changed_names(obj, form)
    if change and not changed:
        # Форма сохранена без единой правки. Запись об этом сделала бы
        # журнал журналом нажатий, а не журналом изменений.
        return

    organization_id = getattr(obj, "organization_id", None)
    if organization_id is None and obj._meta.db_table == "organizations":
        organization_id = obj.pk

    AuditTrail().record(
        Actor.from_user(request.user),
        action=f"admin.{obj._meta.db_table}.{'update' if change else 'create'}",
        entity_type=obj._meta.db_table,
        entity_id=obj.pk,
        before=before,
        after=snapshot(obj, changed),
        # У системной роли своей организации нет: тогда запись ложится
        # в журнал того, кто её правил.
        organization_id=organization_id,
        ip_address=client_ip(request),
        user_agent=request.META.get("HTTP_USER_AGENT"),
    )


# Группы Django в HUMOTECH не используются (права — роли и области), а
# стандартная админка групп не знает об организациях. Лишний интерфейс
# управления доступом убираем (аудит 11.10, M-7).
try:
    from django.contrib.auth.models import Group

    admin.site.unregister(Group)
except admin.sites.NotRegistered:
    pass

admin.site.site_header = "HUMOTECH — техническая панель"
admin.site.site_title = "HUMOTECH"
admin.site.index_title = "Аварийное управление данными"
