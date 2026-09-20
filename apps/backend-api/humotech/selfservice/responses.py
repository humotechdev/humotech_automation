"""Формы ответов личного кабинета — для схемы OpenAPI.

Сами ответы собирает `presentation.py` словарями, и это правильно:
`statistics.py` считает и про JSON знать не должен. Но словарь ничего
не рассказывает генератору схемы, а по схеме собирается клиент — и без
описания у Mini App вместо типов оказывается `unknown`.

Поэтому здесь ровно то же самое, поле в поле, но словами, которые
понимает генератор. Расхождение между этим модулем и `presentation.py`
означает неверную документацию, и держатся они рядом намеренно.

Секунды остаются секундами. Ни «8ч 30м», ни «8.5»: округление и
склонение — дело клиента, а сервер, округлив однажды, теряет разницу
навсегда.
"""

from __future__ import annotations

from rest_framework import serializers


class SessionSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    day = serializers.DateField()
    started_at = serializers.DateTimeField()
    ended_at = serializers.DateTimeField(allow_null=True)
    seconds = serializers.IntegerField()
    is_open = serializers.BooleanField()
    is_preliminary = serializers.BooleanField(
        help_text=(
            "Число ещё вырастет. Без этого признака клиент показал бы "
            "время открытой сессии наравне с закрытой, и человек решил "
            "бы, что день уже посчитан"
        ),
    )
    office_name = serializers.CharField(allow_null=True)
    entry_point_name = serializers.CharField(allow_null=True)
    exit_point_name = serializers.CharField(allow_null=True)


class DaySerializer(serializers.Serializer):
    day = serializers.DateField()
    seconds = serializers.IntegerField()
    sessions_count = serializers.IntegerField()
    has_open_session = serializers.BooleanField()
    is_working_day = serializers.BooleanField()
    attended = serializers.BooleanField()
    missed = serializers.BooleanField()
    absence_code = serializers.CharField(allow_null=True)
    absence_name = serializers.CharField(allow_null=True)
    # Норма дня: 0 у выходного, null — графика на этот день нет.
    norm_seconds = serializers.IntegerField(allow_null=True)


class DayWithSessionsSerializer(DaySerializer):
    sessions = SessionSerializer(many=True)


class SummarySerializer(serializers.Serializer):
    first = serializers.DateField()
    last = serializers.DateField()
    timezone = serializers.CharField()
    seconds = serializers.IntegerField()
    completed_sessions = serializers.IntegerField()
    open_sessions = serializers.IntegerField()
    working_days = serializers.IntegerField(
        allow_null=True,
        help_text=(
            "null, а не ноль: график не назначен — значит рабочих дней "
            "не «ноль», а «неизвестно». Ноль читался бы как безупречная "
            "посещаемость"
        ),
    )
    attended_days = serializers.IntegerField(allow_null=True)
    missed_days = serializers.IntegerField(allow_null=True)
    sick_leave_days = serializers.IntegerField()
    vacation_days = serializers.IntegerField()
    other_absence_days = serializers.IntegerField()
    has_schedule = serializers.BooleanField()


class StatisticsSerializer(serializers.Serializer):
    summary = SummarySerializer()
    days = DaySerializer(
        many=True,
        help_text=(
            "Дневная разбивка идёт вместе с итогом: иначе клиенту "
            "пришлось бы просить её отдельным запросом и складывать "
            "самому — то есть считать статистику второй раз"
        ),
    )


class CurrentStatusSerializer(serializers.Serializer):
    state = serializers.CharField()
    day = serializers.DateField()
    timezone = serializers.CharField()
    seconds_today = serializers.IntegerField(
        help_text=(
            "Открытая сессия и «часы сегодня» — разные числа. В три часа "
            "ночи у зашедшего в 22:00 «сегодня» честно ноль, а в офисе "
            "он пять часов: сессия принадлежит вчерашнему дню"
        ),
    )
    open_session = SessionSerializer(allow_null=True)
    last_entry_at = serializers.DateTimeField(allow_null=True)
    last_exit_at = serializers.DateTimeField(allow_null=True)
    scheduled_start = serializers.DateTimeField(allow_null=True)
    scheduled_end = serializers.DateTimeField(allow_null=True)
    absence_name = serializers.CharField(allow_null=True)


class PeriodSummarySerializer(serializers.Serializer):
    first = serializers.DateField()
    last = serializers.DateField()
    timezone = serializers.CharField()


class HistorySerializer(serializers.Serializer):
    period = PeriodSummarySerializer()
    days = DayWithSessionsSerializer(many=True)
    total = serializers.IntegerField(
        help_text="Сколько дней в периоде вообще есть что показать",
    )
    offset = serializers.IntegerField()
    limit = serializers.IntegerField()
    has_more = serializers.BooleanField()


# --- профиль -----------------------------------------------------------------


class NamedRefSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()


class ProfileEmployeeSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    full_name = serializers.CharField()
    employee_number = serializers.CharField(allow_null=True)
    employment_status = serializers.CharField()
    preferred_language = serializers.CharField(allow_null=True)


class ProfileOfficeSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    timezone = serializers.CharField(
        help_text=(
            "Отдаётся клиенту, чтобы он не считал сутки сам, а показывал "
            "уже посчитанное сервером и знал, в каком поясе подписывать "
            "время"
        ),
    )


class ProfileAssignmentSerializer(serializers.Serializer):
    employment_type = serializers.CharField()
    work_mode = serializers.CharField()
    valid_from = serializers.DateField()


class ProfileTelegramSerializer(serializers.Serializer):
    status = serializers.CharField()
    username = serializers.CharField(allow_null=True)


class ProfileOnboardingSerializer(serializers.Serializer):
    """Состояние первичного ознакомления в ответе профиля.

    `enrolled=False` означает «человека в программу не звали»: он
    работает как прежде, и кнопок про ознакомление ему не показывают.
    Для него `completed` намеренно `true` — клиенту важно не то, прошёл
    ли он программу, а то, открыты ли ему рабочие функции.
    """

    enrolled = serializers.BooleanField()
    required = serializers.BooleanField()
    completed = serializers.BooleanField()
    status = serializers.CharField(allow_null=True)
    stage = serializers.CharField(
        help_text="SECTIONS, POLICIES, BLOCKED или DONE — какой экран показать",
    )
    sections_done = serializers.IntegerField()
    sections_total = serializers.IntegerField()
    policies_done = serializers.IntegerField()
    policies_total = serializers.IntegerField()


class ProfileSerializer(serializers.Serializer):
    """Кто я и где я числюсь.

    Ровно то, что человек и так про себя знает. Ни идентификаторов чужих
    сотрудников, ни данных руководителя, ни оклада здесь нет: экран
    существует, чтобы человек убедился, что система видит его правильно.
    """

    employee = ProfileEmployeeSerializer()
    office = ProfileOfficeSerializer()
    position = NamedRefSerializer(allow_null=True)
    department = NamedRefSerializer(allow_null=True)
    assignment = ProfileAssignmentSerializer()
    telegram = ProfileTelegramSerializer()
    onboarding = ProfileOnboardingSerializer()


# --- отметка -----------------------------------------------------------------


class ScanSessionSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    started_at = serializers.DateTimeField()
    ended_at = serializers.DateTimeField(allow_null=True)
    duration_seconds = serializers.IntegerField(allow_null=True)
    status = serializers.CharField()


class ScanResultSerializer(serializers.Serializer):
    status = serializers.CharField(
        help_text="Причина отказа или вид принятой отметки",
    )
    accepted = serializers.BooleanField()
    office_name = serializers.CharField(allow_null=True)
    point_name = serializers.CharField(allow_null=True)
    occurred_at = serializers.DateTimeField(allow_null=True)
    occurred_at_local = serializers.CharField(
        allow_null=True,
        help_text="Время отметки «ЧЧ:ММ» в часовом поясе офиса",
    )
    point_mode = serializers.CharField(
        allow_null=True, help_text="Направление точки: ENTRY, EXIT или BOTH",
    )
    distance_m = serializers.FloatField(
        allow_null=True, help_text="Расстояние до офиса, если сравнивали",
    )
    radius_m = serializers.IntegerField(
        allow_null=True, help_text="Допустимый радиус геозоны офиса",
    )
    session = ScanSessionSerializer(allow_null=True)


__all__ = [
    "CurrentStatusSerializer",
    "DaySerializer",
    "DayWithSessionsSerializer",
    "HistorySerializer",
    "ProfileSerializer",
    "ScanResultSerializer",
    "SessionSerializer",
    "StatisticsSerializer",
    "SummarySerializer",
]
