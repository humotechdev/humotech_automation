"""Отделение личных вопросов от вопросов о правилах компании.

Личные вопросы («сколько я отработал», «одобрен ли мой больничный») НЕЛЬЗЯ
обрабатывать общим RAG: ответ на них лежит не в базе знаний, а в таблицах
сотрудника, и получать его нужно авторизованным SQL-запросом по employee_id.
Пропустив такой вопрос в RAG, мы в лучшем случае получим бессмысленный ответ,
в худшем — модель придумает цифру.

Классификатор детерминированный, без обращения к модели: маршрут должен быть
предсказуемым и бесплатным. Сначала распознаётся конкретное НАМЕРЕНИЕ (какой
именно запрос к данным нужен), затем — общий признак «вопрос о себе».
"""

from __future__ import annotations

import re
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum

from src.modules.ai_assistant.services.safety import normalize_question


class PersonalIntent(StrEnum):
    """Конкретный запрос к личным данным сотрудника."""

    ARRIVAL_TODAY = "ARRIVAL_TODAY"
    DEPARTURE_TODAY = "DEPARTURE_TODAY"
    IN_OFFICE_NOW = "IN_OFFICE_NOW"
    DURATION_TODAY = "DURATION_TODAY"
    HOURS_WEEK = "HOURS_WEEK"
    HOURS_MONTH = "HOURS_MONTH"
    ABSENCE_DAYS = "ABSENCE_DAYS"
    SICK_LEAVE_STATUS = "SICK_LEAVE_STATUS"
    SICK_LEAVE_DATES = "SICK_LEAVE_DATES"
    VACATION_STATUS = "VACATION_STATUS"
    VACATION_DATES = "VACATION_DATES"
    LEAVE_BALANCE = "LEAVE_BALANCE"


# Порядок важен: более узкие намерения проверяются раньше общих.
# Тексты уже нормализованы (нижний регистр, без пунктуации), но «ё» сохраняется,
# поэтому варианты с «е» и «ё» перечислены явно.
_INTENT_PATTERNS: tuple[tuple[PersonalIntent, re.Pattern[str]], ...] = (
    (
        PersonalIntent.IN_OFFICE_NOW,
        re.compile(r"(нахожусь|на месте|в офисе|на работе).*(сейчас|ли я|я ли)"
                   r"|(сейчас).*(в офисе|на работе|на месте)"),
    ),
    (
        PersonalIntent.DURATION_TODAY,
        re.compile(r"(сколько|скок).*(времени|часов|часа|час)\b.*(сегодня|в офисе)"
                   r"|(провел|провёл|пробыл).*(офисе|на работе)"),
    ),
    (
        PersonalIntent.ARRIVAL_TODAY,
        re.compile(r"(когда|во сколько|в котором часу).*(пришел|пришёл|прише|приход|"
                   r"отметил|начал)"),
    ),
    (
        PersonalIntent.DEPARTURE_TODAY,
        re.compile(r"(когда|во сколько|в котором часу).*(ушел|ушёл|уход|закончил)"),
    ),
    (
        PersonalIntent.HOURS_WEEK,
        re.compile(r"(час|отработ|наработ).*(недел)|(недел).*(час|отработ)"),
    ),
    (
        PersonalIntent.HOURS_MONTH,
        re.compile(r"(час|отработ|наработ).*(месяц)|(месяц).*(час|отработ)"),
    ),
    (
        PersonalIntent.ABSENCE_DAYS,
        re.compile(r"(отсутствовал|отсутствия|не был|пропустил)"
                   r"|(какие|сколько).*(дн).*(отсутств)"),
    ),
    (
        PersonalIntent.SICK_LEAVE_STATUS,
        re.compile(r"(больничн).*(статус|одобрен|подтвержд|принят|согласован)"
                   r"|(статус|одобрен|подтвержд).*(больничн)"),
    ),
    (
        PersonalIntent.SICK_LEAVE_DATES,
        re.compile(r"(больничн).*(дат|когда|период|до какого|с какого)"
                   r"|(дат|когда|период).*(больничн)"),
    ),
    (
        PersonalIntent.LEAVE_BALANCE,
        re.compile(r"(остаток|осталось|остались|баланс).*(отпуск|дн)"
                   r"|(отпуск).*(остаток|осталось|баланс)"),
    ),
    (
        PersonalIntent.VACATION_STATUS,
        re.compile(r"(отпуск).*(статус|одобрен|подтвержд|принят|согласован)"
                   r"|(статус|одобрен|подтвержд).*(отпуск)"),
    ),
    (
        PersonalIntent.VACATION_DATES,
        re.compile(r"(отпуск).*(дат|когда|период|до какого|с какого)"
                   r"|(дат|когда|период).*(отпуск)"),
    ),
)

# Темы, ответ на которые лежит в личных данных сотрудника.
_PERSONAL_TOPICS = (
    "час", "отработ", "переработ",
    "отметк", "пришел", "пришёл", "ушел", "ушёл", "приход", "уход",
    "опозда", "прогул",
    "больничн", "отпуск", "отгул", "командировк",
    "смена", "график",
    "статистик", "табел",
    "зарплат", "аванс", "выплат",
    "остаток", "баланс",
    "заявк",
)

# Признаки того, что вопрос именно о СЕБЕ, а не о правиле вообще.
_PERSONAL_MARKERS = (
    "я", "мне", "меня", "мной", "мой", "моя", "мое", "моё", "мои",
    "моего", "моей", "моих", "моем", "моём",
    "осталось", "остаток", "сегодня", "вчера",
    "получилось", "набралось", "вышло", "накопилось",
)

_WORD = re.compile(r"\w+", re.UNICODE)


@dataclass(frozen=True)
class RoutingDecision:
    is_personal: bool
    intent: PersonalIntent | None = None
    matched_topic: str | None = None
    matched_marker: str | None = None


class PersonalDataQueryRouter:
    """Решает, куда отправить вопрос: в RAG или в сервис личных данных."""

    def detect_intent(self, normalized: str) -> PersonalIntent | None:
        for intent, pattern in _INTENT_PATTERNS:
            if pattern.search(normalized):
                return intent
        return None

    def classify(self, question: str) -> RoutingDecision:
        normalized = normalize_question(question)
        words = set(_WORD.findall(normalized))

        topic = next((t for t in _PERSONAL_TOPICS if t in normalized), None)
        marker = next(
            (
                m
                for m in _PERSONAL_MARKERS
                if m in words or (len(m) > 4 and m in normalized)
            ),
            None,
        )

        # Намерение засчитывается только вместе с признаком «о себе»:
        # «сколько дней отпуска положено» — это правило компании, а не данные.
        intent = self.detect_intent(normalized) if marker else None
        if intent is not None:
            return RoutingDecision(
                is_personal=True, intent=intent,
                matched_topic=topic, matched_marker=marker,
            )

        if topic is None:
            return RoutingDecision(is_personal=False)
        if marker is None:
            # тема личная, но вопрос задан вообще: «как оформить отпуск» —
            # это правило компании, ему место в RAG
            return RoutingDecision(is_personal=False, matched_topic=topic)

        # о себе, но какой именно срез данных нужен — непонятно
        return RoutingDecision(
            is_personal=True, intent=None,
            matched_topic=topic, matched_marker=marker,
        )


@dataclass(frozen=True)
class PersonalDataAnswer:
    available: bool
    text: str
    intent: PersonalIntent | None = None
    # структурированные значения для интерфейса; в LLM не передаются
    data: dict = field(default_factory=dict)


class PersonalDataQueryService(ABC):
    """Интерфейс сервиса личных HR-данных.

    Реализация обязана выполнять авторизованный запрос строго по employee_id
    вызывающего сотрудника и НИКОГДА не отдавать данные другого человека.
    Результат этого сервиса в LLM не передаётся.
    """

    @abstractmethod
    def answer(
        self, *, employee_id: uuid.UUID, question: str, language: str
    ) -> PersonalDataAnswer:
        ...

    @abstractmethod
    def is_available(self) -> bool:
        ...


class NotImplementedPersonalDataQueryService(PersonalDataQueryService):
    """Безопасная заглушка. Оставлена для окружений без базы.

    Выдуманных данных не возвращает принципиально: ответ «сервис пока
    не подключён» честен, а придуманное число часов — нет.
    """

    def __init__(self, message: str) -> None:
        self._message = message

    def answer(
        self, *, employee_id: uuid.UUID, question: str, language: str
    ) -> PersonalDataAnswer:
        return PersonalDataAnswer(available=False, text=self._message)

    def is_available(self) -> bool:
        return False
