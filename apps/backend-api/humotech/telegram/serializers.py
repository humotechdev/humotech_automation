"""Представление привязки Telegram в API.

Одно правило проходит через весь модуль: наружу не уходит ни открытый токен
(его нет даже в базе), ни `token_hash`, ни `initData`. Единственное место,
где токен вообще появляется в ответе, — создание приглашения, и там он
существует ровно один раз.
"""

from __future__ import annotations

from rest_framework import serializers

from humotech.telegram.models import TelegramAccount, TelegramLinkInvitation


class InvitationSerializer(serializers.ModelSerializer):
    """Приглашение так, как его видит HR.

    `token_hash` в списке полей отсутствует намеренно: HR он ничего не
    объясняет, а в журналах и снимках экрана оказывается сразу.
    """

    employee_name = serializers.SerializerMethodField()

    class Meta:
        model = TelegramLinkInvitation
        fields = (
            "id", "organization_id", "employee_id", "employee_name", "status",
            "expires_at", "used_at", "revoked_at", "reviewed_at",
            "consumed_by_telegram_user_id",
            "created_by_user_id", "reviewed_by_user_id",
            "created_at", "updated_at",
        )
        read_only_fields = fields

    def get_employee_name(self, invitation) -> str:
        employee = invitation.employee
        parts = [employee.last_name, employee.first_name, employee.middle_name]
        return " ".join(part for part in parts if part)


class AccountSerializer(serializers.ModelSerializer):
    """Привязка так, как её видит HR.

    `telegram_user_id` показывается: без него HR не может сверить, тот ли
    человек перешёл по ссылке, а именно это решение он и принимает.
    """

    class Meta:
        model = TelegramAccount
        fields = (
            "id", "organization_id", "employee_id", "status",
            "telegram_user_id", "telegram_username", "language_code",
            "connected_at", "last_interaction_at", "revoked_at",
            "created_at", "updated_at",
        )
        read_only_fields = fields


class LinkStatusSerializer(serializers.Serializer):
    """Состояние привязки сотрудника целиком: одно слово плюс подробности."""

    state = serializers.CharField(read_only=True)
    employee_id = serializers.SerializerMethodField()
    account = serializers.SerializerMethodField()
    invitation = serializers.SerializerMethodField()

    def get_employee_id(self, status) -> str:
        return str(status.employee.id)

    def get_account(self, status) -> dict | None:
        return AccountSerializer(status.account).data if status.account else None

    def get_invitation(self, status) -> dict | None:
        return (
            InvitationSerializer(status.invitation).data
            if status.invitation
            else None
        )


class IssuedInvitationSerializer(serializers.Serializer):
    """Ответ на создание ссылки — единственное место, где виден токен.

    Повторно получить его нельзя: в базе только хеш. Потерянная ссылка
    отзывается и выдаётся заново.
    """

    invitation = InvitationSerializer(read_only=True)
    token = serializers.CharField(read_only=True)
    link = serializers.CharField(read_only=True)


class InvitationCreateSerializer(serializers.Serializer):
    employee_id = serializers.UUIDField()
    replace = serializers.BooleanField(
        required=False, default=False,
        help_text=(
            "true — «отправить повторно»: действующая ссылка отзывается и "
            "тут же выдаётся новая. Прежняя перестаёт работать"
        ),
    )


class BotRecognizeSerializer(serializers.Serializer):
    """Что бот сообщает о человеке, открывшем его без ссылки."""

    telegram_user_id = serializers.IntegerField()
    telegram_chat_id = serializers.IntegerField()
    telegram_username = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )
    language_code = serializers.CharField(
        max_length=10, required=False, allow_blank=True, allow_null=True
    )


class WelcomeSerializer(serializers.Serializer):
    """Чем бот здоровается с узнанным сотрудником.

    Ровно то, что человек и так про себя знает: имя, где работает и по
    какому графику. Ни зарплаты, ни документов, ни чужих данных — бот
    здесь ничего не сообщает сверх того, что кадровик уже сказал вслух.
    """

    status = serializers.CharField(help_text="Состояние привязки: PENDING")
    full_name = serializers.CharField()
    employment_status = serializers.CharField()
    hire_date = serializers.DateField(allow_null=True)
    office_name = serializers.CharField(allow_null=True)
    department_name = serializers.CharField(allow_null=True)
    position_name = serializers.CharField(allow_null=True)
    schedule_name = serializers.CharField(allow_null=True)
    manager_name = serializers.CharField(allow_null=True)


class BotConsumeSerializer(serializers.Serializer):
    """Что бот сообщает backend о переходе по ссылке.

    `employee_id` и `organization_id` здесь отсутствуют намеренно: бот их
    не знает и знать не должен. Сотрудник определяется по токену, а токен
    выдан конкретному человеку.
    """

    token = serializers.CharField(max_length=128, trim_whitespace=True)
    telegram_user_id = serializers.IntegerField(min_value=1)
    telegram_chat_id = serializers.IntegerField()
    telegram_username = serializers.CharField(
        max_length=255, required=False, allow_null=True, allow_blank=True
    )
    language_code = serializers.CharField(
        max_length=10, required=False, allow_null=True, allow_blank=True
    )


class BotLinkAcceptSerializer(serializers.Serializer):
    """Согласие сотрудника с условиями после перехода по персональной ссылке."""
    telegram_user_id = serializers.IntegerField(min_value=1)


class MiniAppAuthSerializer(serializers.Serializer):
    """Вход в Mini App. Принимается ТОЛЬКО исходная строка Telegram.

    Ни `telegram_user_id`, ни `employee_id`, ни `organization_id` здесь нет:
    всё это вычисляется из проверенной подписи. Поле, которого не существует,
    невозможно подделать.
    """

    init_data = serializers.CharField(trim_whitespace=False)


class MiniAppSessionSerializer(serializers.Serializer):
    """Ответ на вход: токен и минимум сведений о себе.

    Кадровых подробностей здесь нет. Полный личный кабинет — следующий этап,
    и до него Mini App незачем знать больше, чем «кто я и куда я привязан».
    """

    access_token = serializers.SerializerMethodField()
    expires_in = serializers.IntegerField(read_only=True)
    employee = serializers.SerializerMethodField()

    def get_access_token(self, session) -> str:
        return session.token

    def get_employee(self, session) -> dict:
        employee = session.employee
        parts = [employee.last_name, employee.first_name, employee.middle_name]
        return {
            "id": str(employee.id),
            "full_name": " ".join(part for part in parts if part),
            "employee_number": employee.employee_number,
            "employment_status": employee.employment_status,
            "preferred_language": employee.preferred_language,
        }
