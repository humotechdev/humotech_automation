"""Шаги ознакомления глазами сотрудника. Сюда ходит бот.

Рабочие функции ознакомление не закрывает: сотрудник отмечается,
подаёт заявки и пишет в HR с первого дня. Эти адреса — про сам процесс,
а не про допуск к остальному.

Порядок шагов сервер решает сам. Бот сообщает «подтверждаю такую-то
карточку», а не «покажи следующую»; какая следующая — ответ сервера.
Кнопка в Telegram живёт в чате вечно, и порядок, держащийся на ней,
порядком быть перестаёт.
"""

from __future__ import annotations

from django.http import FileResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.response import Response

from humotech.core.api import validated
from humotech.core.errors import NotFound
from humotech.files.storage import is_viewable, open_stored
from humotech.onboarding.flow import OnboardingFlow
from humotech.onboarding.models import PolicyDocumentVersion
from humotech.onboarding.presentation import state_json
from humotech.onboarding.serializers import (
    AcknowledgeSerializer,
    BeginSerializer,
    PolicyDecisionSerializer,
    OnboardingStateSerializer,
    PolicyTextSerializer,
    SectionSerializer,
)
from humotech.selfservice.views import EmployeeSelfView


class OnboardingSelfView(EmployeeSelfView):
    """Общее основание: те же два входа, что и у остальных экранов."""

    @property
    def flow(self) -> OnboardingFlow:
        return OnboardingFlow()

    @property
    def employee_id(self):
        return self.context.employee.id


@extend_schema(tags=["Ознакомление сотрудника"])
class OnboardingStateView(OnboardingSelfView):
    """Где я остановился."""

    @extend_schema(
        operation_id="me_onboarding_state",
        summary="Состояние ознакомления",
        responses={200: OnboardingStateSerializer},
    )
    def get(self, request):
        return Response(state_json(self.flow.state(self.employee_id)))


@extend_schema(tags=["Ознакомление сотрудника"])
class OnboardingStartView(OnboardingSelfView):
    """«Начать ознакомление».

    Повтор безвреден и намеренно ничего не сбрасывает: человек,
    вернувшийся через неделю, начал не сегодня.
    """

    @extend_schema(
        operation_id="me_onboarding_start",
        summary="Начать ознакомление",
        request=BeginSerializer,
        responses={200: OnboardingStateSerializer},
    )
    def post(self, request):
        data = validated(BeginSerializer, request.data or {})
        progress = self.flow.begin(
            self.employee_id, message_id=data.get("message_id")
        )
        return Response(state_json(progress))


@extend_schema(tags=["Ознакомление сотрудника"])
class OnboardingSectionView(OnboardingSelfView):
    """Одна карточка по номеру — для «← Назад» и перечитывания из меню.

    Уже подтверждённая возвращается с датой подтверждения: по ней бот
    показывает «✓ Ознакомление подтверждено» вместо кнопки, и второй раз
    согласия не спрашивает.
    """

    @extend_schema(
        operation_id="me_onboarding_section",
        summary="Раздел ознакомления",
        parameters=[
            OpenApiParameter("position", OpenApiTypes.INT, OpenApiParameter.PATH),
        ],
        responses={200: SectionSerializer},
    )
    def get(self, request, position: int):
        from humotech.onboarding.presentation import section_json

        progress, state = self.flow.section(self.employee_id, position)
        return Response(section_json(state, total=progress.sections_total))


@extend_schema(tags=["Ознакомление сотрудника"])
class OnboardingAcknowledgeView(OnboardingSelfView):
    """«Я ознакомился».

    Идемпотентно: двойное нажатие и повторное открытие старого сообщения
    приходят сюда же и дают тот же ответ. Прыжок вперёд отклоняется —
    иначе достаточно было бы пролистать чат вверх.
    """

    @extend_schema(
        operation_id="me_onboarding_acknowledge",
        summary="Подтвердить раздел",
        request=AcknowledgeSerializer,
        responses={200: OnboardingStateSerializer},
    )
    def post(self, request):
        data = validated(AcknowledgeSerializer, request.data)
        progress = self.flow.acknowledge(
            self.employee_id,
            data["section_id"],
            telegram_user_id=self.context.account.telegram_user_id,
            message_id=data.get("message_id"),
        )
        return Response(state_json(progress))


@extend_schema(tags=["Ознакомление сотрудника"])
class OnboardingDecisionView(OnboardingSelfView):
    """Согласие или отказ по обязательному документу.

    Решение принимается только по ДЕЙСТВУЮЩЕЙ редакции: кнопка под
    старым сообщением после выпуска новой версии ничего не подтвердит.
    """

    @extend_schema(
        operation_id="me_onboarding_decide",
        summary="Решение по документу",
        request=PolicyDecisionSerializer,
        responses={200: OnboardingStateSerializer},
    )
    def post(self, request):
        data = validated(PolicyDecisionSerializer, request.data)
        progress = self.flow.decide(
            self.employee_id,
            data["version_id"],
            data["decision"],
            telegram_user_id=self.context.account.telegram_user_id,
        )
        return Response(state_json(progress))


@extend_schema(tags=["Ознакомление сотрудника"])
class PolicyTextView(OnboardingSelfView):
    """Полный текст редакции.

    Отдельным запросом, а не вместе с состоянием: правовой текст длинный,
    и таскать его на каждое обновление экрана незачем — в этот момент
    его никто не читает.
    """

    @extend_schema(
        operation_id="me_policy_text",
        summary="Полный текст документа",
        parameters=[
            OpenApiParameter("version_id", OpenApiTypes.UUID, OpenApiParameter.PATH),
        ],
        responses={200: PolicyTextSerializer},
    )
    def get(self, request, version_id):
        version = self._version(version_id)
        return Response({
            "version_id": str(version.id),
            "title": version.document.title,
            "version": version.version,
            "body": version.body,
            "has_file": bool(version.file_id),
            "published_at": (
                version.published_at.isoformat() if version.published_at else None
            ),
        })

    def _version(self, version_id) -> PolicyDocumentVersion:
        """Редакция своей организации. Чужая отвечает как несуществующая.

        Архивные редакции читать МОЖНО: человек имеет право перечитать
        то, с чем он когда-то согласился. Соглашаться с ними уже нельзя —
        это проверяет `OnboardingFlow.decide`.
        """
        version = PolicyDocumentVersion.objects.select_related("document", "file").filter(
            id=version_id,
            organization_id=self.context.organization_id,
            status__in=("PUBLISHED", "ARCHIVED"),
        ).first()
        if version is None:
            raise NotFound("Документ не найден")
        return version


@extend_schema(tags=["Ознакомление сотрудника"])
class PolicyFileView(PolicyTextView):
    """Утверждённый PDF редакции.

    Отдаётся отсюда, а не веб-сервером: файл лежит в приватном
    хранилище, и право на него спрашивается при каждом открытии.
    """

    @extend_schema(
        operation_id="me_policy_file",
        summary="Файл документа",
        parameters=[
            OpenApiParameter("version_id", OpenApiTypes.UUID, OpenApiParameter.PATH),
        ],
        responses={(200, "application/octet-stream"): OpenApiTypes.BINARY},
    )
    def get(self, request, version_id):
        version = self._version(version_id)
        if version.file_id is None:
            raise NotFound("К документу не приложен файл")
        record = version.file
        if not is_viewable(record):
            # Файл удалён или не прошёл проверку. Отдавать его нельзя,
            # а молчать — значит оставить человека перед кнопкой,
            # которая ничего не делает.
            raise NotFound("Файл документа недоступен")
        return FileResponse(
            open_stored(record),
            as_attachment=False,
            filename=record.original_filename,
            content_type=record.mime_type,
        )


__all__ = [
    "OnboardingAcknowledgeView",
    "OnboardingDecisionView",
    "OnboardingSectionView",
    "OnboardingStartView",
    "OnboardingStateView",
    "PolicyFileView",
    "PolicyTextView",
]
