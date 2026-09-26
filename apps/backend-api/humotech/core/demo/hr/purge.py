"""Удаление строк витрины вместе со всем, что на них ссылается.

Ключи схемы — RESTRICT: строку, на которую кто-то ссылается, база не
отдаст. Поэтому удаление идёт от листьев к корню. Правило одно и простое:

  * ссылка может быть пустой → она обнуляется, а ссылающаяся строка
    остаётся. Так настоящий раздел, у которого ответственным назначили
    демонстрационного сотрудника, переживёт очистку витрины;
  * ссылка обязательна → ссылающаяся строка удаляется тем же способом.

Удаляется только то, что достижимо от переданного набора по
обязательным ссылкам, — то есть действия, записи и история самих
демонстрационных сущностей.
"""

from __future__ import annotations

from django.db import IntegrityError, models, transaction


def purge(queryset, *, depth: int = 0) -> int:
    """Удалить строки набора и всё, что без них не может существовать."""
    if depth > 12:
        raise RuntimeError("Слишком глубокая цепочка ссылок при очистке витрины")
    model = queryset.model
    ids = list(queryset.values_list("pk", flat=True))
    if not ids:
        return 0
    removed = 0
    # Скрытые обратные связи (`related_name="+"`) тоже: смена ссылается
    # на событие отметки именно так, и без них очистка упёрлась бы в ключ.
    relations = [
        one for one in model._meta.get_fields(include_hidden=True)
        if one.auto_created and not one.concrete and (one.one_to_many or one.one_to_one)
    ]
    for relation in relations:
        field = getattr(relation, "field", None)
        if relation.many_to_many or not isinstance(field, models.ForeignKey):
            continue
        dependent = relation.related_model._base_manager.filter(**{f"{field.name}__in": ids})
        if field.null:
            # Пустая ссылка допустима не всегда: проверка базы может
            # требовать её у строки определённого вида (отмена заявки без
            # исходной). Тогда строка — часть витрины и удаляется.
            try:
                with transaction.atomic():
                    dependent.update(**{field.name: None})
                continue
            except IntegrityError:
                pass
        removed += purge(dependent, depth=depth + 1)
    deleted, _ = model._base_manager.filter(pk__in=ids).delete()
    return removed + deleted
