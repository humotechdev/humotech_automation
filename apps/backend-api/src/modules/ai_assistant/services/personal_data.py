"""Отделение личных вопросов от вопросов о правилах компании.

Личные вопросы («сколько я отработал», «одобрен ли мой больничный») НЕЛЬЗЯ
обрабатывать общим RAG: ответ на них лежит не в базе знаний, а в таблицах
сотрудника, и получать его нужно авторизованным SQL-запросом по employee_id.
Пропустив такой вопрос в RAG, мы в лучшем случае получим бессмысленный ответ,
в худшем — модель придумает цифру.

Классификатор детерминированный, без обращения к модели: решение о маршруте
должно быть предсказуемым и бесплатным.
"""

from __future__ import annotations

import re
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass

from src.modules.ai_assistant.services.safety import normalize_question

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
)

_WORD = re.compile(r"\w+", re.UNICODE)


@dataclass(frozen=True)
class RoutingDecision:
    is_personal: bool
    matched_topic: str | None = None
    matched_marker: str | None = None


class PersonalDataQueryRouter:
    """Решает, куда отправить вопрос: в RAG или в сервис личных данных."""

    def classify(self, question: str) -> RoutingDecision:
        normalized = normalize_question(question)
        words = set(_WORD.findall(normalized))

        topic = next(
            (t for t in _PERSONAL_TOPICS if t in normalized),
            None,
        )
        if topic is None:
            return RoutingDecision(is_personal=False)

        marker = next(
            (m for m in _PERSONAL_MARKERS if m in words or (len(m) > 4 and m in normalized)),
            None,
        )
        if marker is None:
            # тема личная, но вопрос задан вообще: «как оформить отпуск» —
            # это правило компании, ему место в RAG
            return RoutingDecision(is_personal=False, matched_topic=topic)

        return RoutingDecision(
            is_personal=True, matched_topic=topic, matched_marker=marker
        )


@dataclass(frozen=True)
class PersonalDataAnswer:
    available: bool
    text: str


class PersonalDataQueryService(ABC):
    """Интерфейс сервиса личных HR-данных.

    Реализация обязана выполнять авторизованный запрос строго по employee_id
    вызывающего сотрудника и НИКОГДА не отдавать данные другого человека.
    В LLM результат этого сервиса не передаётся.
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
    """Безопасная заглушка на время, пока сервис не реализован.

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
