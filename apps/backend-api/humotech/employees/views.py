"""REST-интерфейс кадровых операций."""

from __future__ import annotations

import uuid
from datetime import date

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response

from humotech.core.api import UUID_PATTERN, ServiceViewSet, validated
from humotech.core.errors import ValidationFailed
from humotech.employees.attachments import EmployeeAttachmentService
from humotech.employees.onboarding import EmployeeOnboardingService
from humotech.employees.lifecycle import EmployeeLifecycleService
from humotech.employees.serializers import (
    AssignmentChangeSerializer,
    AttachedFileSerializer,
    AssignmentSerializer,
    DocumentAttachSerializer,
    EmployeeCardSerializer,
    EmployeeCreateSerializer,
    EmployeeDocumentSerializer,
    EmployeeListItemSerializer,
    EmployeeSearchItemSerializer,
    EmployeeOnboardSerializer,
    EmployeeUpdateSerializer,
    EndProbationSerializer,
    OnboardedSerializer,
    PhotoSetSerializer,
    PromoteSerializer,
    TerminateSerializer,
)
from humotech.employees.services import EmployeeService


#: Идентификатор бумаги в адресе — только UUID (pk уже ограничен
#: `ServiceViewSet.lookup_value_regex`). Кривое значение — 404 маршрута.
UUID_RE = UUID_PATTERN

#: Сдвиг страницы. Больше сотрудников в одной организации не бывает, а
#: значение за пределами bigint роняло запрос в базе (DataError → 500).
MAX_OFFSET = 1_000_000

#: Даты, на которые имеет смысл смотреть состав. `0001-01-01` ронял
#: расчёт «новичков за 30 дней» (OverflowError → 500).
AT_MIN, AT_MAX = date(1900, 1, 1), date(2100, 12, 31)


def parse_uuid_list(name: str, values: list[str]) -> list[uuid.UUID]:
    """Значения фильтра — UUID; иначе понятный 400 вместо 500."""
    parsed = []
    for raw in values:
        try:
            parsed.append(uuid.UUID(raw))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValidationFailed(
                f"Параметр «{name}» должен быть UUID", details={"field": name}
            ) from exc
    return parsed


class EmployeeViewSet(ServiceViewSet):
    service_class = EmployeeService
    read_serializer_class = EmployeeCardSerializer

    # ------------------------------------------------------------------ чтение

    #: Фильтры списка. Каждый принимает несколько значений через запятую:
    #: кадровик смотрит «два офиса и три отдела», а не по одному.
    SCOPE_PARAMS = ("office_id", "region_id", "department_id", "position_id")

    def list(self, request):
        params = self.list_params()
        params.update(self._scope(request))
        params["at"] = self._at()
        # Сдвиг — для перехода на произвольную страницу списка. Курсором
        # это невозможно: он отвечает «дальше вот этой записи», и до
        # тридцать второй страницы им идут через тридцать одну.
        raw = request.query_params.get("offset")
        if raw:
            # Свой разбор, а не общий `_positive_int`: тот ограничен
            # размером страницы, а сдвиг по определению больше — до
            # тридцать второй страницы по восемь строк это 248.
            try:
                offset = int(raw)
            except (TypeError, ValueError) as exc:
                raise ValidationFailed(
                    "Параметр «offset» должен быть числом",
                    details={"field": "offset", "value": raw},
                ) from exc
            if offset < 0 or offset > MAX_OFFSET:
                raise ValidationFailed(
                    f"Параметр «offset» должен быть от 0 до {MAX_OFFSET}",
                    details={"field": "offset", "value": raw[:30]},
                )
            params["offset"] = offset
        return self.page_response(
            self.service.list(self.actor, **params),
            serializer_class=EmployeeListItemSerializer,
        )

    @action(detail=False, methods=["get"])
    def counts(self, request):
        """Сколько сотрудников в каждом состоянии при текущих фильтрах.

        Отдельный адрес, а не поле страницы: страница приходит курсором,
        и посчитать по ней всё множество нельзя — там десять строк из
        скольких угодно.
        """
        params = self._scope(request)
        params["search"] = request.query_params.get("search") or None
        params["at"] = self._at()
        return Response(self.service.counts(self.actor, **params))

    @action(detail=False, methods=["get"])
    def highlights(self, request):
        """Новички, именинники и сотрудники без графика — одним ответом.

        Нужно правой колонке списка. Отдельный адрес по той же причине,
        что и `counts`: страница приходит курсором, и вопрос «сколько
        всего» по ней не решается.
        """
        params = self._scope(request)
        params["at"] = self._at()
        return Response(self.service.highlights(self.actor, **params))

    @action(detail=False, methods=["get"], url_path="search")
    def search(self, request):
        """Не более десяти сотрудников для глобального поиска."""
        rows = self.service.search(
            self.actor,
            query=request.query_params.get("q") or "",
            at=self._at(),
        )
        return Response({"items": EmployeeSearchItemSerializer(rows, many=True).data})

    def _scope(self, request) -> dict:
        """Фильтры списка списками значений.

        `office_id=a,b` — «в офисе a ИЛИ b». Одно значение остаётся
        одним значением: прежние ссылки и закладки продолжают работать.
        """
        found = {}
        for name in self.SCOPE_PARAMS:
            value = request.query_params.get(name)
            if not value:
                continue
            values = [part.strip() for part in str(value).split(",") if part.strip()]
            if values:
                found[name] = parse_uuid_list(name, values)
        return found

    def retrieve(self, request, pk=None):
        return self.item_response(self.service.get(self.actor, pk, at=self._at()))

    @action(detail=True, methods=["get"], url_path="assignments")
    def assignments(self, request, pk=None):
        """История переводов: где, кем и в какой период человек работал."""
        history = self.service.assignment_history(self.actor, pk)
        return Response({"items": AssignmentSerializer(history, many=True).data})

    def _at(self) -> date | None:
        """Дата, на которую смотрим состав.

        Нужна, потому что назначения хранятся периодами: «где работает
        сотрудник» — вопрос, у которого нет ответа без даты.
        """
        raw = self.request.query_params.get("at")
        if not raw:
            return None
        try:
            value = date.fromisoformat(raw)
        except ValueError as exc:
            raise ValidationFailed(
                "Параметр «at» должен быть датой в формате ГГГГ-ММ-ДД",
                details={"at": raw[:30]},
            ) from exc
        if not AT_MIN <= value <= AT_MAX:
            raise ValidationFailed(
                f"Параметр «at» должен быть между {AT_MIN} и {AT_MAX}",
                details={"at": raw},
            )
        return value

    # --------------------------------------------------------------- изменение

    def create(self, request):
        payload = validated(EmployeeCreateSerializer, request.data)
        return self.item_response(
            self.service.create(self.actor, **payload), created=True
        )

    @action(detail=False, methods=["post"])
    def onboard(self, request):
        """Приём сотрудника одной операцией.

        Отдельный адрес, а не расширение `POST /employees`: тот создаёт
        карточку и назначение, а приём — ещё график, документы и заготовку
        доступа к боту, и откатывается целиком, если не удался любой из
        шагов. Смешивать это в одном адресе значило бы, что часть вызовов
        делает половину работы молча.
        """
        payload = validated(EmployeeOnboardSerializer, request.data)
        made = EmployeeOnboardingService().onboard(self.actor, **payload)
        return Response(
            OnboardedSerializer(made).data,
            status=201 if made.created else 200,
        )

    @action(
        detail=False,
        methods=["post"],
        url_path="attachments",
        parser_classes=[MultiPartParser, FormParser],
    )
    def attachments(self, request):
        """Принять файл до создания сотрудника.

        Форма приёма показывает фотографию и размер документа ещё до
        сохранения, а значит файл должен быть на сервере раньше карточки.
        Привязка происходит в момент приёма: до него это просто файл
        организации, ни на кого не ссылающийся.
        """
        upload = request.FILES.get("file")
        if upload is None:
            raise ValidationFailed(
                "Файл не приложен", details={"field": "file"}
            )
        record = EmployeeAttachmentService().upload(
            self.actor,
            upload=upload,
            purpose=request.data.get("purpose") or "document",
        )
        return Response(AttachedFileSerializer(record).data, status=201)

    @action(detail=True, methods=["get"])
    def photo(self, request, pk=None):
        """Фотография сотрудника.

        Отдаётся отсюда, а не веб-сервером: файл лежит в приватном
        хранилище, и право на него спрашивается при каждом открытии.
        `inline` — картинку открывают, а не скачивают.
        """
        stream, record = EmployeeAttachmentService().open_photo(self.actor, pk)
        # scan_status, удаление и безопасные заголовки — одной проверкой.
        from humotech.files.serving import STAFF, file_response

        return file_response(stream, record, audience=STAFF)

    @extend_schema(
        summary="Приложить бумагу заведённому сотруднику",
        description=(
            "Строка чек-листа заполняется, а не дублируется: на "
            "сотрудника приходится одна бумага каждого вида. «Прочее» "
            "— исключение, его может быть сколько угодно."
        ),
        request=DocumentAttachSerializer,
        responses={201: EmployeeDocumentSerializer},
    )
    @action(detail=True, methods=["post"], url_path="documents")
    def document_attach(self, request, pk=None):
        payload = validated(DocumentAttachSerializer, request.data)
        row = EmployeeAttachmentService().attach_document(self.actor, pk, **payload)
        return Response(EmployeeDocumentSerializer(row).data, status=201)

    @extend_schema(
        summary="Снять файл с бумаги",
        description=(
            "Строка чек-листа остаётся и возвращается в исходное "
            "состояние: «паспорта нет» — это факт, который кадровику "
            "нужно видеть. Свободная бумага удаляется целиком."
        ),
        parameters=[
            OpenApiParameter(
                "document_id", OpenApiTypes.UUID, OpenApiParameter.PATH
            )
        ],
        responses={204: None},
    )
    @action(
        detail=True,
        methods=["delete"],
        url_path=f"documents/(?P<document_id>{UUID_RE})",
    )
    def document_detach(self, request, pk=None, document_id=None):
        EmployeeAttachmentService().detach_document(self.actor, pk, document_id)
        return Response(status=204)

    @extend_schema(
        summary="Заменить фотографию сотрудника",
        request=PhotoSetSerializer,
        responses={200: AttachedFileSerializer},
    )
    @action(detail=True, methods=["post"], url_path="photo-set")
    def photo_set(self, request, pk=None):
        payload = validated(PhotoSetSerializer, request.data)
        record = EmployeeAttachmentService().set_photo(self.actor, pk, **payload)
        return Response(AttachedFileSerializer(record).data)

    @extend_schema(
        # Тип параметра из адреса сам не выводится: путь задан регулярным
        # выражением, а не конвертером. Без этой строки генератор схемы
        # ругается, и проверка схемы падает.
        parameters=[
            OpenApiParameter(
                "document_id", OpenApiTypes.UUID, OpenApiParameter.PATH
            )
        ],
    )
    @action(
        detail=True,
        methods=["get"],
        url_path=f"documents/(?P<document_id>{UUID_RE})/download",
    )
    def document_download(self, request, pk=None, document_id=None):
        """Открыть или скачать приложенную бумагу."""
        stream, record = EmployeeAttachmentService().open_document(
            self.actor, pk, document_id
        )
        # PDF и картинки браузер показывает сам; прочее он всё равно
        # предложит сохранить. `inline` не мешает скачиванию — кнопка
        # «сохранить» есть и в просмотрщике.
        # scan_status, удаление и безопасные заголовки — одной проверкой.
        from humotech.files.serving import STAFF, file_response

        return file_response(stream, record, audience=STAFF)

    def partial_update(self, request, pk=None):
        payload = validated(EmployeeUpdateSerializer, request.data)
        return self.item_response(self.service.update(self.actor, pk, **payload))

    @action(detail=True, methods=["post"], url_path="change-assignment")
    def change_assignment(self, request, pk=None):
        """Перевод в другой офис, отдел или на другую должность.

        Отдельное действие, а не правка карточки: перевод создаёт НОВЫЙ период
        и закрывает прежний, а PATCH по смыслу означал бы правку одной строки.
        """
        payload = validated(AssignmentChangeSerializer, request.data)
        assignment = self.service.change_assignment(self.actor, pk, **payload)
        return self.item_response(
            assignment, created=True, serializer_class=AssignmentSerializer
        )

    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        return self.item_response(self.service.deactivate(self.actor, pk))

    @action(detail=True, methods=["post"])
    def reactivate(self, request, pk=None):
        return self.item_response(self.service.reactivate(self.actor, pk))

    @extend_schema(
        summary="Принять стажёра в штат",
        description=(
            "Статус меняется со «Стажировки» на «Работает». Должность "
            "можно пересмотреть по итогам стажировки — тогда она "
            "меняется до смены статуса, чтобы уведомление назвало ту, "
            "на которую человека приняли."
        ),
        request=PromoteSerializer,
        responses={200: EmployeeCardSerializer},
    )
    @action(detail=True, methods=["post"], url_path="promote")
    def promote(self, request, pk=None):
        payload = validated(PromoteSerializer, request.data)
        return self.item_response(
            EmployeeLifecycleService().promote_to_staff(
                self.actor, pk,
                position_id=payload.get("position_id"),
                effective_from=payload.get("effective_from"),
            )
        )

    @extend_schema(
        summary="Завершить стажировку",
        description=(
            "Расставание по итогам испытательного срока. Это увольнение "
            "с причиной «Не прошёл стажировку»: отдельного статуса для "
            "такого случая нет и быть не должно."
        ),
        request=EndProbationSerializer,
        responses={200: EmployeeCardSerializer},
    )
    @action(detail=True, methods=["post"], url_path="end-probation")
    def end_probation(self, request, pk=None):
        payload = validated(EndProbationSerializer, request.data)
        return self.item_response(
            EmployeeLifecycleService().end_probation(
                self.actor, pk,
                last_day=payload.get("last_day"),
                reason=payload.get("reason"),
            )
        )

    @action(detail=True, methods=["post"])
    def terminate(self, request, pk=None):
        """Увольнение. Запись и вся история сохраняются, открытые периоды
        закрываются датой увольнения."""
        payload = validated(TerminateSerializer, request.data)
        return self.item_response(
            self.service.terminate(
                self.actor, pk,
                termination_date=payload["termination_date"],
                reason=payload.get("reason"),
            )
        )
