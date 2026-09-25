"""База знаний глазами кадровика: документы, FAQ и очередь индексации.

Своей логики здесь почти нет и быть не должно: черновики, публикация,
версии и архивация уже описаны в `ai_assistant/use_cases/crm.py`, и
второй такой набор правил разошёлся бы с первым в первый же месяц.
Этот слой добавляет ровно три вещи, которых там нет:

  * постраничные списки — сценарии отдают отдельные записи, а кадровику
    нужен перечень;
  * отказ, когда считать эмбеддинги нечем. Индексация и активация FAQ
    без работающего провайдера не откладываются «на потом»: строка,
    помеченная действующей, но не попадающая в поиск, — это молчаливая
    поломка, которую заметят через месяц по жалобе сотрудника;
  * прямое ведение FAQ. В сценариях есть только «сделать FAQ из
    неизвестного вопроса», а кадровику нужно заводить их и самому.

Права берутся те же, что и везде: `knowledge.read`, `knowledge.write`,
`knowledge.publish`, `knowledge.index`. Новых не заводится.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager

from django.db.models import Count, OuterRef, Q, Subquery

from humotech.ai_assistant.availability import (
    embeddings_available,
    require_embeddings_available,
)
from humotech.ai_assistant.errors import PublishingError
from humotech.ai_assistant.services.chunking import hash_text
from humotech.ai_assistant.use_cases.crm import KnowledgeAdminUseCases
from humotech.core.enums import (
    FAQ_ENTRY_STATUSES,
    INDEX_JOB_STATUSES,
    KNOWLEDGE_SOURCE_STATUSES,
)
from humotech.core.errors import Conflict, NotFound, ValidationFailed
from humotech.core.pagination import Page, paginate
from humotech.core.rbac import Actor, snapshot
from humotech.core.service import BaseService
from humotech.core.validation import clean_text
from humotech.knowledge.models import FaqEntry, KnowledgeIndexJob, KnowledgeSource

FAQ_FIELDS = (
    "canonical_question", "approved_answer", "language", "status", "priority",
)


@contextmanager
def _readable_refusal():
    """`PublishingError` наружу как понятный отказ, а не как пятисотка.

    `PublishingError` — обычное `Exception`, не `DomainError`: обработчик
    его не узнаёт, DRF тоже, и «черновик архивировать нельзя» приходило
    в браузер пятисоткой без текста. Правило при этом верное — переводим
    только форму ответа, сам запрет остаётся там, где написан.
    """
    try:
        yield
    except PublishingError as exc:
        raise Conflict(str(exc)) from exc


class KnowledgeService(BaseService):
    """Документы базы знаний, FAQ и задания индексации."""

    def __init__(self) -> None:
        super().__init__()
        self.use_cases = KnowledgeAdminUseCases()
        # Один слой прав на оба набора: у `AccessControl` есть кэш
        # разрешений, и второй экземпляр перечитывал бы роли заново на
        # каждую проверку. Журнал — по той же причине один.
        self.use_cases.access = self.access
        self.use_cases.audit = self.audit

    # ------------------------------------------------------------- документы

    def list_sources(
        self,
        actor: Actor,
        *,
        status: str | None = None,
        language: str | None = None,
        office_id: uuid.UUID | None = None,
        region_id: uuid.UUID | None = None,
        search: str | None = None,
        all_versions: bool = False,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> Page:
        """Документы. По умолчанию — по одной строке на документ.

        Версия — отдельная строка в `knowledge_sources`, и без свёртки
        документ, переизданный трижды, занимал бы в списке три места,
        а «10 документов» означало бы «10 строк таблицы». История версий
        живёт в своей вкладке; в списке документ один.
        """
        self.access.require(actor, "knowledge.read")
        queryset = self._sources(
            actor,
            status=status,
            language=language,
            office_id=office_id,
            region_id=region_id,
            search=search,
            all_versions=all_versions,
        )
        return paginate(queryset, limit=limit, cursor=cursor)

    def count_sources(self, actor: Actor, **filters) -> dict[str, int]:
        """Сколько документов в каждом статусе — по всему набору.

        Считает то же самое, что показывает список: одна строка на
        документ. Иначе число рядом с вкладкой и длина списка под ней
        расходились бы на каждом переизданном документе.

        Фильтр статуса сюда не передаётся: число рядом с «Черновики» не
        должно меняться от того, какая вкладка открыта.
        """
        filters.pop("status", None)
        self.access.require(actor, "knowledge.read")
        rows = self._sources(actor, status=None, **filters)
        totals = {name: 0 for name in KNOWLEDGE_SOURCE_STATUSES}
        for row in rows.values("status").annotate(number=Count("id")):
            totals[row["status"]] = row["number"]
        totals["total"] = sum(totals.values())
        return totals

    def versions(self, actor: Actor, source_id: uuid.UUID) -> list[KnowledgeSource]:
        """История версий документа, начиная с самой новой.

        Линейка задаётся тройкой «организация + заголовок + язык» — той
        же, на которой стоит частичный уникальный индекс действующей
        версии. `parent_source_id` эту связь дополняет, но не заменяет:
        версия могла быть заведена и без указания родителя.

        Документ сначала проверяется на принадлежность организации: без
        этого историю соседей можно было бы прочитать по одному
        идентификатору.
        """
        self.access.require(actor, "knowledge.read")
        source = self.use_cases._require_source(actor, source_id)
        rows = self.use_cases.version_history(actor, source.title, source.language)
        return list(
            KnowledgeSource.objects.filter(id__in=[row.id for row in rows])
            .select_related("office", "region", "created_by_user")
            .order_by("-version", "-created_at")
        )

    def capability(self, actor: Actor) -> dict:
        """Можно ли сейчас индексировать и включать записи в поиск.

        Ровно один признак и причина словом. Ни ключа, ни имени модели,
        ни других настроек провайдера отсюда не уходит: интерфейсу нужно
        знать «нельзя и почему», а не чем именно не настроено.
        """
        self.access.require(actor, "knowledge.read")
        from humotech.ai_assistant.config import ai_settings

        available = embeddings_available()
        reason = None
        if not available:
            reason = (
                "ai_disabled"
                if not ai_settings.ai_assistant_enabled
                else "provider_not_configured"
            )
        return {"embeddings_available": available, "reason": reason}

    def _sources(
        self,
        actor: Actor,
        *,
        status: str | None,
        language: str | None = None,
        office_id: uuid.UUID | None = None,
        region_id: uuid.UUID | None = None,
        search: str | None = None,
        all_versions: bool = False,
    ):
        queryset = KnowledgeSource.objects.filter(
            organization_id=actor.organization_id
        ).select_related("office", "region", "department", "created_by_user")

        if not all_versions:
            # Самая новая версия каждой линейки. Подзапрос НЕ сужается
            # фильтрами снаружи: иначе «самой новой» оказалась бы самая
            # новая среди подходящих, и документ с черновиком поверх
            # архива показался бы архивным.
            newest = (
                KnowledgeSource.objects.filter(
                    organization_id=actor.organization_id,
                    title=OuterRef("title"),
                    language=OuterRef("language"),
                )
                .order_by("-version", "-created_at", "-id")
                .values("id")[:1]
            )
            queryset = queryset.filter(id=Subquery(newest))

        if status is not None:
            wanted = [
                _known(part, "status", KNOWLEDGE_SOURCE_STATUSES)
                for part in status.split(",")
                if part
            ]
            if wanted:
                queryset = queryset.filter(status__in=wanted)
        if language:
            queryset = queryset.filter(language=language.strip())
        if office_id is not None:
            self.access.require_office(actor, office_id)
            queryset = queryset.filter(office_id=office_id)
        if region_id is not None:
            self.access.require_region(actor, region_id)
            queryset = queryset.filter(region_id=region_id)
        if search:
            needle = search.strip()
            queryset = queryset.filter(
                Q(title__icontains=needle) | Q(content__icontains=needle)
            )
        return queryset

    def get_source(self, actor: Actor, source_id: uuid.UUID) -> KnowledgeSource:
        self.access.require(actor, "knowledge.read")
        return self.use_cases._require_source(actor, source_id)

    def create_source(self, actor: Actor, **payload) -> KnowledgeSource:
        with _readable_refusal(), self.atomic():
            return self.use_cases.create_draft(actor, **payload)

    def update_source(
        self, actor: Actor, source_id: uuid.UUID, **fields
    ) -> KnowledgeSource:
        clean = {name: value for name, value in fields.items() if value is not None}
        if not clean:
            return self.get_source(actor, source_id)
        with _readable_refusal(), self.atomic():
            return self.use_cases.update_draft(actor, source_id, **clean)

    def start_indexing(
        self, actor: Actor, source_id: uuid.UUID
    ) -> KnowledgeIndexJob:
        """Поставить документ в очередь на индексацию.

        Отказ, когда провайдер недоступен, — намеренно ДО постановки в
        очередь. Задание, которое некому выполнить, выглядит принятым и
        висит в QUEUED: кадровик будет ждать, а причина не показана нигде.
        """
        require_embeddings_available()
        with _readable_refusal(), self.atomic():
            return self.use_cases.start_indexing(actor, source_id)

    def index_status(self, actor: Actor, source_id: uuid.UUID):
        return self.use_cases.index_status(actor, source_id)

    def publish(self, actor: Actor, source_id: uuid.UUID) -> KnowledgeSource:
        """Опубликовать документ: он начинает участвовать в ответах.

        Документ без посчитанных кусков в поиск не попадёт, поэтому
        публикация без индексации — это тихо неработающая публикация.
        """
        source = self.use_cases._require_source(actor, source_id)
        if not source.chunks.filter(embedding__isnull=False).exists():
            raise Conflict(
                "Документ не проиндексирован: в ответах он не появится. "
                "Сначала запустите индексацию",
                details={"source_id": str(source_id), "status": source.status},
            )
        with _readable_refusal(), self.atomic():
            self.use_cases.publish(actor, source_id)
        return self.use_cases._require_source(actor, source_id)

    def archive(self, actor: Actor, source_id: uuid.UUID) -> KnowledgeSource:
        """Снять документ с публикации.

        Архивировать можно только ДЕЙСТВУЮЩУЮ версию: у черновика нет
        публикации, которую снимают, и «в архив» на нём означало бы
        удаление — а удаления здесь нет и быть не должно.
        """
        with _readable_refusal(), self.atomic():
            self.use_cases.archive(actor, source_id)
        return self.use_cases._require_source(actor, source_id)

    # ----------------------------------------------------------- очередь работ

    def list_index_jobs(
        self,
        actor: Actor,
        *,
        status: str | None = None,
        source_id: uuid.UUID | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> Page:
        self.access.require(actor, "knowledge.read")
        # Задание само принадлежит организации: фильтр через источник
        # был бы соединением там, где есть свой индекс.
        queryset = KnowledgeIndexJob.objects.filter(
            organization_id=actor.organization_id
        ).select_related("source")
        if status is not None:
            queryset = queryset.filter(
                status=_known(status, "status", INDEX_JOB_STATUSES)
            )
        if source_id is not None:
            self.use_cases._require_source(actor, source_id)
            queryset = queryset.filter(source_id=source_id)
        return paginate(queryset, limit=limit, cursor=cursor)

    def get_index_job(self, actor: Actor, job_id: uuid.UUID) -> KnowledgeIndexJob:
        self.access.require(actor, "knowledge.read")
        job = (
            KnowledgeIndexJob.objects.select_related("source")
            .filter(id=job_id, organization_id=actor.organization_id)
            .first()
        )
        if job is None:
            raise NotFound("Задание индексации не найдено")
        return job

    # ------------------------------------------------------------------- FAQ

    def list_faq(
        self,
        actor: Actor,
        *,
        status: str | None = None,
        language: str | None = None,
        office_id: uuid.UUID | None = None,
        region_id: uuid.UUID | None = None,
        source_id: uuid.UUID | None = None,
        search: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> Page:
        self.access.require(actor, "knowledge.read")
        return paginate(
            self._faq(
                actor,
                status=status,
                language=language,
                office_id=office_id,
                region_id=region_id,
                source_id=source_id,
                search=search,
            ),
            limit=limit,
            cursor=cursor,
        )

    def count_faq(self, actor: Actor, **filters) -> dict[str, int]:
        """Сколько FAQ в каждом статусе — по всему доступному набору.

        Как и у документов, фильтр статуса сюда не передаётся: число
        рядом с вкладкой не должно зависеть от открытой вкладки.
        """
        filters.pop("status", None)
        self.access.require(actor, "knowledge.read")
        rows = self._faq(actor, status=None, **filters)
        totals = {name: 0 for name in FAQ_ENTRY_STATUSES}
        for row in rows.values("status").annotate(number=Count("id")):
            totals[row["status"]] = row["number"]
        totals["total"] = sum(totals.values())
        return totals

    def _faq(
        self,
        actor: Actor,
        *,
        status: str | None,
        language: str | None = None,
        office_id: uuid.UUID | None = None,
        region_id: uuid.UUID | None = None,
        source_id: uuid.UUID | None = None,
        search: str | None = None,
    ):
        queryset = FaqEntry.objects.filter(
            organization_id=actor.organization_id
        ).select_related("office", "region", "source", "created_by_user")
        if status is not None:
            wanted = [
                _known(part, "status", FAQ_ENTRY_STATUSES)
                for part in status.split(",")
                if part
            ]
            if wanted:
                queryset = queryset.filter(status__in=wanted)
        if language:
            queryset = queryset.filter(language=language.strip())
        if office_id is not None:
            self.access.require_office(actor, office_id)
            queryset = queryset.filter(office_id=office_id)
        if region_id is not None:
            self.access.require_region(actor, region_id)
            queryset = queryset.filter(region_id=region_id)
        if source_id is not None:
            # Документ сверяется по организации: иначе по чужому
            # идентификатору можно было бы проверить, есть ли у соседей
            # FAQ по этому документу.
            self.use_cases._require_source(actor, source_id)
            queryset = queryset.filter(source_id=source_id)
        if search:
            needle = search.strip()
            queryset = queryset.filter(
                Q(canonical_question__icontains=needle)
                | Q(approved_answer__icontains=needle)
            )
        return queryset

    def get_faq(self, actor: Actor, faq_id: uuid.UUID) -> FaqEntry:
        self.access.require(actor, "knowledge.read")
        return self._require_faq(actor, faq_id)

    def create_faq(
        self,
        actor: Actor,
        *,
        canonical_question: str,
        approved_answer: str,
        language: str,
        source_id: uuid.UUID | None = None,
        office_id: uuid.UUID | None = None,
        region_id: uuid.UUID | None = None,
        priority: int = 0,
    ) -> FaqEntry:
        """Завести FAQ вручную. Всегда черновиком.

        Сразу действующим он быть не может: в поиск попадает только
        запись с посчитанным эмбеддингом, а считает его воркер.
        """
        self.access.require(actor, "knowledge.write")
        question = clean_text(
            canonical_question, field="canonical_question", required=True
        )
        answer = clean_text(
            approved_answer, field="approved_answer", required=True
        )
        if office_id is not None and region_id is not None:
            raise ValidationFailed(
                "FAQ нельзя привязать сразу к офису и к региону: "
                "уровень области действия должен быть однозначным",
                details={"field": "office_id"},
            )
        # Своя организация, своя видимость; FAQ на всю организацию — только
        # тому, кому видна вся организация.
        self.use_cases._require_area(
            actor, office_id=office_id, region_id=region_id
        )
        if source_id is not None:
            self.use_cases._require_source(actor, source_id)

        with self.atomic():
            faq = FaqEntry.objects.create(
                organization_id=actor.organization_id,
                canonical_question=question,
                approved_answer=answer,
                language=language,
                source_id=source_id,
                office_id=office_id,
                region_id=region_id,
                status="DRAFT",
                priority=priority,
                created_by_user_id=actor.user_id,
                content_hash=hash_text(question + answer),
            )
            self.audit.record(
                actor,
                action="knowledge.faq.create",
                entity_type="faq_entries",
                entity_id=faq.id,
                after=snapshot(faq, FAQ_FIELDS),
            )
        return faq

    def update_faq(
        self,
        actor: Actor,
        faq_id: uuid.UUID,
        *,
        canonical_question: str | None = None,
        approved_answer: str | None = None,
        priority: int | None = None,
    ) -> FaqEntry:
        """Правка текста сбрасывает запись в черновик.

        Иначе в поиске остался бы старый эмбеддинг при новом ответе:
        сотрудник спросил бы одно, а получил ответ на другое.
        """
        self.access.require(actor, "knowledge.write")
        faq = self._require_faq_in_scope(actor, faq_id)
        before = snapshot(faq, FAQ_FIELDS)
        fields: list[str] = []

        if canonical_question is not None:
            faq.canonical_question = clean_text(
                canonical_question, field="canonical_question", required=True
            )
            fields.append("canonical_question")
        if approved_answer is not None:
            faq.approved_answer = clean_text(
                approved_answer, field="approved_answer", required=True
            )
            fields.append("approved_answer")
        if priority is not None:
            faq.priority = priority
            fields.append("priority")

        if not fields:
            return faq

        text_changed = {"canonical_question", "approved_answer"} & set(fields)
        if text_changed:
            faq.content_hash = hash_text(
                faq.canonical_question + faq.approved_answer
            )
            faq.question_embedding = None
            fields += ["content_hash", "question_embedding"]
            if faq.status == "ACTIVE":
                faq.status = "DRAFT"
                fields.append("status")

        with self.atomic():
            faq.save(update_fields=[*fields, "updated_at"])
            self.audit.record(
                actor,
                action="knowledge.faq.update",
                entity_type="faq_entries",
                entity_id=faq.id,
                before=before,
                after=snapshot(faq, FAQ_FIELDS),
            )
        return faq

    def set_faq_status(
        self, actor: Actor, faq_id: uuid.UUID, *, status: str
    ) -> FaqEntry:
        """Включить FAQ в поиск или убрать из него."""
        self.access.require(actor, "knowledge.publish")
        target = _known(status, "status", FAQ_ENTRY_STATUSES)
        faq = self._require_faq_in_scope(actor, faq_id)

        if target == "ACTIVE" and faq.question_embedding is None:
            # Без эмбеддинга запись действующей быть не может: поиск
            # отбирает по `question_embedding IS NOT NULL`, и ACTIVE без
            # него означал бы «включено, но не работает».
            raise Conflict(
                "У FAQ нет посчитанного эмбеддинга: в поиск он не попадёт. "
                "Дождитесь индексации",
                details={"faq_id": str(faq_id)},
            )
        if faq.status == target:
            return faq

        before = snapshot(faq, FAQ_FIELDS)
        with self.atomic():
            faq.status = target
            faq.save(update_fields=["status", "updated_at"])
            self.audit.record(
                actor,
                action="knowledge.faq.status",
                entity_type="faq_entries",
                entity_id=faq.id,
                before=before,
                after=snapshot(faq, FAQ_FIELDS),
            )
        return faq

    def _require_faq_in_scope(self, actor: Actor, faq_id: uuid.UUID) -> FaqEntry:
        """FAQ своей организации, и его офис или регион виден актору.

        Для правки и включения в поиск: иначе пользователь с правами на
        один офис менял ответ, который получают сотрудники соседнего.
        """
        faq = self._require_faq(actor, faq_id)
        self.use_cases._require_area(
            actor, office_id=faq.office_id, region_id=faq.region_id
        )
        return faq

    def _require_faq(self, actor: Actor, faq_id: uuid.UUID) -> FaqEntry:
        faq = (
            FaqEntry.objects.select_related(
                "office", "region", "source", "created_by_user"
            )
            .filter(id=faq_id, organization_id=actor.organization_id)
            .first()
        )
        if faq is None:
            raise NotFound("FAQ не найден")
        return faq


def _known(value: str, field: str, allowed: tuple[str, ...]) -> str:
    if value not in allowed:
        raise ValidationFailed(
            f"Неизвестное значение параметра «{field}»",
            details={"field": field, "value": value, "allowed": list(allowed)},
        )
    return value


__all__ = ["KnowledgeService"]
