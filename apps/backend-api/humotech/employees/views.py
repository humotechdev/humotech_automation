"""REST-интерфейс кадровых операций."""

from __future__ import annotations

from datetime import date

from django.http import FileResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response

from humotech.core.api import ServiceViewSet, validated
from humotech.core.errors import ValidationFailed
from humotech.employees.attachments import EmployeeAttachmentService
from humotech.employees.onboarding import EmployeeOnboardingService
from humotech.employees.serializers import (
    AssignmentChangeSerializer,
    AttachedFileSerializer,
    AssignmentSerializer,
    EmployeeCardSerializer,
    EmployeeCreateSerializer,
    EmployeeListItemSerializer,
    EmployeeSearchItemSerializer,
    EmployeeOnboardSerializer,
    EmployeeUpdateSerializer,
    OnboardedSerializer,
    TerminateSerializer,
)
from humotech.employees.services import EmployeeService


class EmployeeViewSet(ServiceViewSet):
    service_class = EmployeeService
    read_serializer_class = EmployeeCardSerializer

    # ------------------------------------------------------------------ чтение

    #: Фильтры списка, которые приходят из строки запроса как есть.
    SCOPE_PARAMS = ("office_id", "region_id", "department_id")

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
            if offset < 0:
                raise ValidationFailed(
                    "Параметр «offset» не может быть отрицательным",
                    details={"field": "offset", "value": raw},
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
        found = {}
        for name in self.SCOPE_PARAMS:
            value = request.query_params.get(name)
            if value:
                found[name] = value
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
            return date.fromisoformat(raw)
        except ValueError as exc:
            raise ValidationFailed(
                "Параметр «at» должен быть датой в формате ГГГГ-ММ-ДД",
                details={"at": raw},
            ) from exc

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
        return FileResponse(
            stream,
            as_attachment=False,
            filename=record.original_filename,
            content_type=record.mime_type,
        )

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
        url_path="documents/(?P<document_id>[^/.]+)/download",
    )
    def document_download(self, request, pk=None, document_id=None):
        """Открыть или скачать приложенную бумагу."""
        stream, record = EmployeeAttachmentService().open_document(
            self.actor, pk, document_id
        )
        # PDF и картинки браузер показывает сам; прочее он всё равно
        # предложит сохранить. `inline` не мешает скачиванию — кнопка
        # «сохранить» есть и в просмотрщике.
        return FileResponse(
            stream,
            as_attachment=False,
            filename=record.original_filename,
            content_type=record.mime_type,
        )

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
