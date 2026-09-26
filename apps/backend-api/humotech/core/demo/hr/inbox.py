"""Обращения сотрудников и статьи базы знаний, которые к ним подходят.

Обращения собираются штатным помощником витрины (`queues._question`): он
пишет переписку, события истории и строки доставки ответов так же, как
их пишет сама система. Ответы HR помечены отправленными — в очередь
отправки они не попадают.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta

from humotech.core.demo.catalog import DemoQuestion
from humotech.core.demo.hr import catalog as cat
from humotech.core.demo.hr.people import Person, did
from humotech.core.demo.queues import _question
from humotech.knowledge.models import KnowledgeSource
from humotech.questions.inbox import next_number

Q = DemoQuestion

QUESTIONS: tuple[DemoQuestion, ...] = (
    # --- новые ---
    Q("Перенос отпуска на октябрь", "VACATION", "NORMAL", "NEW", 25,
      (("E", "Здравствуйте! Отпуск согласован с 6 октября. Можно перенести его на 20 октября? "
             "Семейные обстоятельства."),),
      who=0, unread=True, draft="READY", confidence=0.9, sources=("vacation",),
      answer="Да, даты можно изменить до начала отпуска: откройте заявку в боте и нажмите «Изменить даты». "
             "Новая дата придёт HR на согласование."),
    Q("Справка с места работы для банка", "DOCUMENTS", "NORMAL", "NEW", 70,
      (("E", "Нужна справка с места работы для банка, с указанием должности и стажа. Когда можно получить?"),),
      who=1, unread=True, draft="READY", confidence=0.87, sources=("certificate",),
      answer="Справку подготовим за один рабочий день. Заберите её на ресепшене главного офиса или получите скан в боте."),
    Q("Не открывается бот после смены телефона", "TELEGRAM", "HIGH", "NEW", 140,
      (("E", "Сменил телефон, и бот пишет, что доступа нет. Как восстановить?"),),
      who=2, unread=True, draft="LOW_CONFIDENCE", confidence=0.61, sources=("telegram",),
      answer="Попросите HR выдать новую ссылку-приглашение: старая привязка будет отключена."),
    Q("Вопрос по графику в Рамадан", "SCHEDULE", "LOW", "NEW", 300,
      (("E", "Будет ли сокращённый график в следующем месяце? Хочу заранее спланировать встречи."),),
      who=3),
    # --- в работе ---
    Q("Больничный: какую справку нужно", "SICK_LEAVE", "HIGH", "IN_PROGRESS", 45,
      (("E", "Я на больничном с понедельника. Какая справка нужна — из поликлиники или из частной клиники?"),
       ("H", "Подойдёт справка из любой лицензированной клиники, но обязательно с печатью и периодом."),
       ("E", "Понял. А заявление нужно приносить лично?"),
       ("H", "Да, подписанное заявление принесите в первый рабочий день — HR отметит его в системе.")),
      who=4, assigned=True, sources=("sick",)),
    Q("Командировка в Бухару: суточные", "OTHER", "NORMAL", "IN_PROGRESS", 180,
      (("E", "Еду в командировку в Бухару на три дня. Как оформляются суточные?"),
       ("H", "Суточные начисляются автоматически после согласования командировки. Сохраните билеты и чеки."),
       ("E", "А гостиницу бронирую сам или через офис?")),
      who=5, assigned=True, sources=("trip",)),
    Q("Не пришла премия за август", "SALARY", "HIGH", "IN_PROGRESS", 240,
      (("E", "В расчётном листе за август нет квартальной премии. Можно проверить?"),
       ("H", "Проверяем с бухгалтерией, ответим до конца дня.")),
      who=6, assigned=True),
    Q("Ошибка отметки входа", "ATTENDANCE", "URGENT", "IN_PROGRESS", 35,
      (("E", "Утром отметился у главного входа, но в системе стоит «Нет отметки»."),
       ("H", "Видим сбой сканера у главного входа в 08:50. Подайте, пожалуйста, заявку на исправление — подтвердим."),
       ("E", "Подал заявку, спасибо!")),
      who=7, assigned=True, sources=("marks",)),
    # --- ждут сотрудника ---
    Q("Перевод в офис Юнусабад", "OTHER", "NORMAL", "WAITING_EMPLOYEE", 400,
      (("E", "Хочу перейти работать в офис Юнусабад — он ближе к дому. Это возможно?"),
       ("W", "Да, это возможно. Уточните, пожалуйста, с какой даты и согласовали ли вы перевод с руководителем?")),
      who=8, assigned=True),
    Q("Справка 2-НДФЛ", "DOCUMENTS", "LOW", "WAITING_EMPLOYEE", 900,
      (("E", "Нужна справка о доходах за полгода."),
       ("W", "Подскажите, пожалуйста, за какие месяцы и в каком виде нужна справка — бумажная или скан?")),
      who=9, assigned=True),
    Q("Отпуск за свой счёт на один день", "VACATION", "NORMAL", "WAITING_EMPLOYEE", 1300,
      (("E", "Можно взять один день за свой счёт в следующую пятницу?"),
       ("H", "Можно. Нужна заявка в боте — вид «Отпуск за свой счёт»."),
       ("E", "Не вижу такого вида в боте."),
       ("W", "Пришлите, пожалуйста, скриншот экрана с видами заявок — проверим, почему пункт не отображается.")),
      who=10, assigned=True, sources=("vacation",)),
    # --- закрытые ---
    Q("Как оформить отпуск", "VACATION", "NORMAL", "CLOSED", 2900,
      (("E", "Как оформить ежегодный отпуск?"),
       ("H", "В боте: «Мои заявки» → «Отпуск», укажите даты. Подать нужно не позднее чем за 14 дней.")),
      who=11, assigned=True, close_reason="Вопрос решён", sources=("vacation",)),
    Q("Смена офиса для отметки", "ATTENDANCE", "NORMAL", "CLOSED", 4300,
      (("E", "Иногда работаю в Юнусабаде. Можно там отмечаться?"),
       ("H", "Да, отметка засчитывается в любом офисе компании. Отдельно ничего оформлять не нужно."),
       ("E", "Отлично, спасибо!")),
      who=12, assigned=True, close_reason="Вопрос решён"),
    Q("Доступ к Telegram после отпуска", "TELEGRAM", "NORMAL", "CLOSED", 5800,
      (("E", "После отпуска бот просит заново подтвердить документы. Это нормально?"),
       ("H", "Да, пока вас не было, вышла новая версия документа «Пожарная безопасность». Подтвердите её в боте.")),
      who=13, assigned=True, close_reason="Вопрос решён", sources=("telegram",)),
)


def build(org, people: list[Person], now: datetime, reviewer) -> int:
    sources = {}
    for key, kind, title, content in cat.ARTICLES:
        published = now - timedelta(days=40 + len(sources) * 6)
        row = KnowledgeSource.objects.create(
            id=did("knowledge", key), organization=org, title=title, source_type=kind, language="ru",
            content=content, content_hash=hashlib.sha256(content.encode()).hexdigest(), status="ACTIVE",
            created_by_user=reviewer, approved_by_user=reviewer, approved_at=published, published_at=published,
            meta={"demo": cat.MARK},
        )
        KnowledgeSource.objects.filter(id=row.id).update(created_at=published, updated_at=published)
        sources[key] = row

    pool = [p for p in people if p.status == "ACTIVE" and p.telegram == "ACTIVE" and not p.head and not p.roles]
    first = next_number(org.id)
    for at, item in enumerate(QUESTIONS):
        person = pool[(item.who * 7) % len(pool)]
        _question(org, person.employee, item, first + at, now, reviewer, sources)
    return len(QUESTIONS)
