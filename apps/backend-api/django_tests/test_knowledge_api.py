"""База знаний и вопросы через HTTP при выключенном ассистенте.

Главное, что здесь проверяется, — честность отказа. Индексация и
включение FAQ в поиск требуют эмбеддинга, а посчитать его нечем: ключа
нет, рубильник выключен. Приемлемых способов сделать вид, что
получилось, не существует — случайный вектор ищется как настоящий и
отличить его потом будет нечем. Поэтому оба действия отказывают
отдельным кодом, а всё остальное — ведение документов, FAQ, чтение
очереди и работа с вопросами — работает как обычно.

Второе — изоляция. Разрешение `knowledge.write` отвечает на вопрос
«что можно делать», но не «с чьими данными»: до этих проверок правка
и публикация чужой базы знаний проходили по одному идентификатору.

Ни один тест не обращается к провайдеру. Настройки подменяются
фикстурой, платного вызова не происходит нигде.
"""

from __future__ import annotations

from datetime import date

import pytest

from humotech.ai_assistant.config import AiSettings
from humotech.ai_assistant.models import UnansweredQuestion
from humotech.audit.models import AuditLog
from humotech.employees.models import Employee, EmployeeAssignment
from humotech.knowledge.models import (
    FaqEntry,
    KnowledgeChunk,
    KnowledgeIndexJob,
    KnowledgeSource,
)
from humotech.notifications.models import Notification
from humotech.questions.models import EmployeeQuestion

pytestmark = pytest.mark.django_db

API = "/api/v1"
READER = ("knowledge.read", "questions.read", "employees.read")
EDITOR = (
    "knowledge.read", "knowledge.write", "knowledge.index",
    "knowledge.publish", "questions.read", "questions.answer",
    "employees.read",
)

#: Размерность вектора в схеме. Значение подставляется только там, где
#: проверяется поведение «эмбеддинг уже есть», и настоящим не является.
VECTOR_SIZE = 1536


@pytest.fixture()
def author(make_user, organization):
    """Автор документов. Колонка `created_by_user_id` не пустует никогда:
    у каждого регламента есть человек, который его завёл."""
    return make_user(organization, permissions=EDITOR)


@pytest.fixture()
def editor(api_client, make_user, organization):
    api_client.force_authenticate(
        user=make_user(organization, permissions=EDITOR)
    )
    return api_client


@pytest.fixture()
def ai_on(monkeypatch):
    """Рубильник включён, ключ фиктивный.

    Настоящий провайдер при этом не создаётся ни разу: проверяется
    только то, что при включённом ассистенте отказа не будет.
    """
    monkeypatch.setattr(
        "humotech.ai_assistant.availability.ai_settings",
        AiSettings(
            ai_assistant_enabled=True,
            openai_api_key="test-key-not-real",
            openai_embedding_model="test-embedding-model",
        ),
    )


def make_source(organization, author, **kwargs) -> KnowledgeSource:
    defaults = {
        "title": "Порядок оформления отпуска",
        "source_type": "POLICY",
        "language": "ru",
        "content": "Заявление подаётся за две недели.",
        "content_hash": "0" * 64,
        "status": "DRAFT",
    }
    defaults.update(kwargs)
    return KnowledgeSource.objects.create(
        organization=organization, created_by_user=author, **defaults
    )


def make_faq(organization, author, **kwargs) -> FaqEntry:
    defaults = {
        "canonical_question": "Как оформить отпуск?",
        "approved_answer": "Подайте заявление за две недели.",
        "language": "ru",
        "content_hash": "1" * 64,
        "status": "DRAFT",
    }
    defaults.update(kwargs)
    return FaqEntry.objects.create(
        organization=organization, created_by_user=author, **defaults
    )


def index_source(source) -> KnowledgeChunk:
    """Один готовый кусок с вектором — «документ проиндексирован».

    Вектор здесь не эмбеддинг и им не притворяется: он нужен ровно
    затем, чтобы отличить проиндексированный документ от чистого.
    """
    return KnowledgeChunk.objects.create(
        organization=source.organization,
        source=source,
        chunk_index=0,
        chunk_text=source.content,
        token_count=8,
        content_hash="2" * 64,
        embedding=[0.0] * VECTOR_SIZE,
    )


# --- отказ при выключенном ассистенте ----------------------------------------


class TestWithoutTheAssistant:
    def test_documents_are_still_managed(self, editor, organization):
        """Ведение базы знаний от ассистента не зависит.

        Регламенты пишут люди, и запретить их заводить, пока выключен
        ассистент, значило бы связать две несвязанные вещи.
        """
        response = editor.post(
            f"{API}/knowledge/sources/",
            {"title": "Правила пропусков", "source_type": "INSTRUCTION",
             "language": "ru", "content": "Пропуск выдаётся в первый день."},
            format="json",
        )

        assert response.status_code == 201
        assert response.json()["status"] == "DRAFT"

    def test_indexing_is_refused_with_its_own_code(self, editor, organization, author):
        source = make_source(organization, author)

        response = editor.post(f"{API}/knowledge/sources/{source.id}/index/")

        assert response.status_code == 503
        assert response.json()["error"]["code"] == "ai_disabled"

    def test_the_refused_job_is_not_queued(self, editor, organization, author):
        """Задание, которое некому выполнить, хуже отказа.

        Оно выглядит принятым и висит в QUEUED, а причина не показана
        нигде — кадровик будет ждать индексации, которой не будет.
        """
        source = make_source(organization, author)

        editor.post(f"{API}/knowledge/sources/{source.id}/index/")

        assert KnowledgeIndexJob.objects.filter(source=source).count() == 0

    def test_status_says_why_indexing_is_unavailable(
        self, editor, organization, author
    ):
        source = make_source(organization, author)

        body = editor.get(
            f"{API}/knowledge/sources/{source.id}/index-status/"
        ).json()

        assert body["embeddings_available"] is False
        assert body["chunks_indexed"] == 0

    def test_missing_key_is_told_apart_from_the_switch(
        self, editor, organization, monkeypatch, author
    ):
        """«Выключено» и «не настроено» чинят разные люди.

        Первое — тот, кто принимал решение; второе — тот, у кого есть
        ключ. Один код на оба случая отправлял бы обоих не туда.
        """
        monkeypatch.setattr(
            "humotech.ai_assistant.availability.ai_settings",
            AiSettings(ai_assistant_enabled=True, openai_api_key=""),
        )
        source = make_source(organization, author)

        response = editor.post(f"{API}/knowledge/sources/{source.id}/index/")

        assert response.json()["error"]["code"] == "provider_not_configured"

    def test_indexing_starts_once_the_assistant_is_on(
        self, editor, organization, ai_on, author
    ):
        source = make_source(organization, author)

        response = editor.post(f"{API}/knowledge/sources/{source.id}/index/")

        assert response.status_code == 200
        assert KnowledgeIndexJob.objects.filter(source=source).exists()


# --- изоляция организаций ----------------------------------------------------


class TestIsolation:
    def test_a_foreign_document_cannot_be_edited(
        self, api_client, make_user, organization, other_organization, author
    ):
        """Настоящая находка аудита.

        `knowledge.write` отвечает «что можно делать», но не «с чьими
        данными»: раньше правка чужого документа проходила по одному
        идентификатору.
        """
        source = make_source(organization, author, title="Наш регламент")
        api_client.force_authenticate(
            user=make_user(other_organization, permissions=EDITOR)
        )

        response = api_client.patch(
            f"{API}/knowledge/sources/{source.id}/",
            {"title": "Подменено"},
            format="json",
        )

        assert response.status_code == 404
        source.refresh_from_db()
        assert source.title == "Наш регламент"

    def test_a_foreign_document_cannot_be_published(
        self, api_client, make_user, organization, other_organization, author
    ):
        source = make_source(organization, author)
        index_source(source)
        api_client.force_authenticate(
            user=make_user(other_organization, permissions=EDITOR)
        )

        response = api_client.post(
            f"{API}/knowledge/sources/{source.id}/publish/"
        )

        assert response.status_code == 404
        source.refresh_from_db()
        assert source.status == "DRAFT"

    def test_a_foreign_document_cannot_be_archived(
        self, api_client, make_user, organization, other_organization, author
    ):
        source = make_source(organization, author, status="ACTIVE")
        api_client.force_authenticate(
            user=make_user(other_organization, permissions=EDITOR)
        )

        assert api_client.post(
            f"{API}/knowledge/sources/{source.id}/archive/"
        ).status_code == 404

    def test_a_foreign_document_cannot_become_our_new_version(
        self, api_client, make_user, organization, other_organization, author
    ):
        """Через родителя — тот же обход, только в два шага.

        Содержимое соседней организации попало бы в нашу базу знаний
        как «версия 2».
        """
        source = make_source(organization, author)
        api_client.force_authenticate(
            user=make_user(other_organization, permissions=EDITOR)
        )

        response = api_client.post(
            f"{API}/knowledge/sources/",
            {"title": "Своё", "source_type": "POLICY", "language": "ru",
             "content": "Текст", "parent_source_id": str(source.id)},
            format="json",
        )

        assert response.status_code == 404

    def test_a_foreign_document_is_not_listed(
        self, api_client, make_user, organization, other_organization, author
    ):
        outsider = make_user(other_organization, permissions=EDITOR)
        make_source(organization, author)
        mine = make_source(other_organization, outsider, title="Соседский")
        api_client.force_authenticate(
            user=make_user(other_organization, permissions=EDITOR)
        )

        body = api_client.get(f"{API}/knowledge/sources/").json()

        assert [item["id"] for item in body["items"]] == [str(mine.id)]

    def test_a_foreign_faq_is_not_reachable(
        self, api_client, make_user, organization, other_organization, author
    ):
        faq = make_faq(organization, author)
        api_client.force_authenticate(
            user=make_user(other_organization, permissions=EDITOR)
        )

        assert api_client.get(
            f"{API}/knowledge/faq/{faq.id}/"
        ).status_code == 404

    def test_a_foreign_index_job_is_not_reachable(
        self, api_client, make_user, organization, other_organization, author
    ):
        source = make_source(organization, author)
        job = KnowledgeIndexJob.objects.create(
            organization=organization, source=source, status="QUEUED"
        )
        api_client.force_authenticate(
            user=make_user(other_organization, permissions=EDITOR)
        )

        assert api_client.get(
            f"{API}/knowledge/index-jobs/{job.id}/"
        ).status_code == 404


# --- права -------------------------------------------------------------------


class TestPermissions:
    def test_reading_needs_knowledge_read(
        self, api_client, make_user, organization
    ):
        api_client.force_authenticate(
            user=make_user(organization, permissions=("employees.read",))
        )

        assert api_client.get(
            f"{API}/knowledge/sources/"
        ).status_code == 403
        assert api_client.get(f"{API}/knowledge/faq/").status_code == 403
        assert api_client.get(
            f"{API}/knowledge/index-jobs/"
        ).status_code == 403

    def test_a_reader_cannot_edit(
        self, api_client, make_user, organization, author
    ):
        source = make_source(organization, author)
        api_client.force_authenticate(
            user=make_user(organization, permissions=READER)
        )

        assert api_client.patch(
            f"{API}/knowledge/sources/{source.id}/",
            {"title": "Правка"}, format="json",
        ).status_code == 403
        assert api_client.post(
            f"{API}/knowledge/sources/",
            {"title": "Новый", "source_type": "POLICY", "language": "ru",
             "content": "Текст"},
            format="json",
        ).status_code == 403

    def test_publishing_needs_its_own_permission(
        self, api_client, make_user, organization, author
    ):
        """Писать и утверждать — разные решения.

        Иначе любой, кто правит черновики, мог бы сам их и утвердить.
        """
        source = make_source(organization, author)
        index_source(source)
        api_client.force_authenticate(
            user=make_user(
                organization,
                permissions=("knowledge.read", "knowledge.write"),
            )
        )

        assert api_client.post(
            f"{API}/knowledge/sources/{source.id}/publish/"
        ).status_code == 403


# --- публикация --------------------------------------------------------------


class TestPublishing:
    def test_an_unindexed_document_is_not_published(
        self, editor, organization, author
    ):
        """Иначе публикация тихо не работает.

        В поиск документ отбирается по посчитанным кускам, и без них
        «опубликован» означало бы «опубликован, но не отвечает».
        """
        source = make_source(organization, author)

        response = editor.post(f"{API}/knowledge/sources/{source.id}/publish/")

        assert response.status_code == 409
        source.refresh_from_db()
        assert source.status == "DRAFT"

    def test_an_indexed_document_is_published(self, editor, organization, author):
        source = make_source(organization, author)
        index_source(source)

        response = editor.post(f"{API}/knowledge/sources/{source.id}/publish/")

        assert response.status_code == 200
        source.refresh_from_db()
        assert source.status == "ACTIVE"

    def test_publishing_is_recorded(self, editor, organization, author):
        source = make_source(organization, author)
        index_source(source)

        editor.post(f"{API}/knowledge/sources/{source.id}/publish/")

        assert AuditLog.objects.filter(
            entity_type="knowledge_sources", entity_id=source.id,
            action="knowledge.publish",
        ).exists()


# --- FAQ ---------------------------------------------------------------------


class TestFaq:
    def test_a_new_faq_is_always_a_draft(self, editor, organization):
        response = editor.post(
            f"{API}/knowledge/faq/",
            {"canonical_question": "Когда зарплата?",
             "approved_answer": "Пятого и двадцатого.", "language": "ru"},
            format="json",
        )

        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "DRAFT"
        assert body["indexed"] is False

    def test_a_faq_without_an_embedding_is_not_activated(
        self, editor, organization, author
    ):
        """Поиск отбирает по `question_embedding IS NOT NULL`.

        Запись без него, помеченная действующей, — это включённая
        кнопка, за которой ничего нет.
        """
        faq = make_faq(organization, author)

        response = editor.post(f"{API}/knowledge/faq/{faq.id}/activate/")

        assert response.status_code == 409
        faq.refresh_from_db()
        assert faq.status == "DRAFT"

    def test_a_faq_with_an_embedding_is_activated(self, editor, organization, author):
        faq = make_faq(
            organization, author, question_embedding=[0.0] * VECTOR_SIZE
        )

        response = editor.post(f"{API}/knowledge/faq/{faq.id}/activate/")

        assert response.status_code == 200
        faq.refresh_from_db()
        assert faq.status == "ACTIVE"

    def test_editing_the_text_takes_the_faq_out_of_search(
        self, editor, organization, author
    ):
        """Старый эмбеддинг при новом ответе — худший исход.

        Сотрудник спросил бы одно, а получил ответ на другое, и понять
        это по интерфейсу было бы нельзя.
        """
        faq = make_faq(
            organization,
            author,
            status="ACTIVE",
            question_embedding=[0.0] * VECTOR_SIZE,
        )

        editor.patch(
            f"{API}/knowledge/faq/{faq.id}/",
            {"approved_answer": "Теперь десятого и двадцать пятого."},
            format="json",
        )

        faq.refresh_from_db()
        assert faq.status == "DRAFT"
        assert faq.question_embedding is None

    def test_changing_only_the_priority_keeps_the_faq_in_search(
        self, editor, organization, author
    ):
        """Порядок вывода смысла ответа не меняет."""
        faq = make_faq(
            organization,
            author,
            status="ACTIVE",
            question_embedding=[0.0] * VECTOR_SIZE,
        )

        editor.patch(
            f"{API}/knowledge/faq/{faq.id}/", {"priority": 5}, format="json"
        )

        faq.refresh_from_db()
        assert faq.status == "ACTIVE"
        assert faq.question_embedding is not None

    def test_office_and_region_together_are_refused(
        self, editor, organization, office, region
    ):
        response = editor.post(
            f"{API}/knowledge/faq/",
            {"canonical_question": "Вопрос", "approved_answer": "Ответ",
             "language": "ru", "office_id": str(office.id),
             "region_id": str(region.id)},
            format="json",
        )

        assert response.status_code == 400


# --- вопросы -----------------------------------------------------------------


@pytest.fixture()
def escalation(organization, employee) -> EmployeeQuestion:
    return EmployeeQuestion.objects.create(
        organization=organization,
        employee=employee,
        question_text="Сколько дней отпуска мне осталось?",
        status="ESCALATED_TO_HR",
    )


class TestEscalations:
    def test_waiting_questions_are_the_default_list(
        self, editor, organization, employee, escalation
    ):
        """Список всех вопросов за год не отвечает ни на один вопрос."""
        EmployeeQuestion.objects.create(
            organization=organization, employee=employee,
            question_text="Старый", status="CLOSED",
        )

        body = editor.get(f"{API}/knowledge/escalations/").json()

        assert [item["id"] for item in body["items"]] == [str(escalation.id)]

    def test_the_answer_reaches_the_employee(self, editor, escalation):
        """Ответ, не дошедший до человека, для него не ответ.

        Уведомление ставится в очередь той же транзакцией, поэтому
        «ответили, но не отправили» не бывает.
        """
        response = editor.post(
            f"{API}/knowledge/escalations/{escalation.id}/answer/",
            {"answer": "Осталось 14 дней."},
            format="json",
        )

        assert response.status_code == 200
        escalation.refresh_from_db()
        assert escalation.status == "HR_ANSWERED"
        assert escalation.hr_answer_text == "Осталось 14 дней."

        message = Notification.objects.get(employee=escalation.employee)
        assert message.body == "Осталось 14 дней."
        assert message.status == "PENDING"
        assert message.related_entity_id == escalation.id

    def test_answering_twice_does_not_send_a_second_message(
        self, editor, escalation
    ):
        editor.post(
            f"{API}/knowledge/escalations/{escalation.id}/answer/",
            {"answer": "Осталось 14 дней."}, format="json",
        )
        response = editor.post(
            f"{API}/knowledge/escalations/{escalation.id}/answer/",
            {"answer": "Ещё раз."}, format="json",
        )

        assert response.status_code == 409
        assert Notification.objects.count() == 1

    def test_answering_takes_the_question(self, editor, escalation):
        """Разбирался с ним именно этот человек — значит, оно его."""
        editor.post(
            f"{API}/knowledge/escalations/{escalation.id}/answer/",
            {"answer": "Ответ."}, format="json",
        )

        escalation.refresh_from_db()
        assert escalation.assigned_to_user_id is not None

    def test_the_answer_is_recorded(self, editor, escalation):
        editor.post(
            f"{API}/knowledge/escalations/{escalation.id}/answer/",
            {"answer": "Ответ."}, format="json",
        )

        entry = AuditLog.objects.get(
            entity_type="employee_questions", entity_id=escalation.id
        )
        assert entry.action == "question.escalation.answer"
        assert entry.old_values["status"] == "ESCALATED_TO_HR"
        assert entry.new_values["status"] == "HR_ANSWERED"

    def test_another_office_does_not_see_the_question(
        self, api_client, make_user, organization, other_office, escalation
    ):
        other = Employee.objects.create(
            organization=organization, employee_number="EMP-0002",
            first_name="Пётр", last_name="Петров",
            hire_date=date(2024, 2, 1), employment_status="ACTIVE",
        )
        EmployeeAssignment.objects.create(
            organization=organization, employee=other, office=other_office,
            employment_type="FULL_TIME", work_mode="ONSITE",
            is_primary=True, valid_from=date(2024, 2, 1),
        )
        theirs = EmployeeQuestion.objects.create(
            organization=organization, employee=other,
            question_text="Чужой вопрос", status="ESCALATED_TO_HR",
        )
        api_client.force_authenticate(
            user=make_user(
                organization, permissions=EDITOR, office=other_office
            )
        )

        body = api_client.get(f"{API}/knowledge/escalations/").json()

        assert [item["id"] for item in body["items"]] == [str(theirs.id)]
        assert api_client.get(
            f"{API}/knowledge/escalations/{escalation.id}/"
        ).status_code == 403

    def test_answering_needs_questions_answer(
        self, api_client, make_user, organization, escalation
    ):
        api_client.force_authenticate(
            user=make_user(organization, permissions=READER)
        )

        assert api_client.post(
            f"{API}/knowledge/escalations/{escalation.id}/answer/",
            {"answer": "Ответ."}, format="json",
        ).status_code == 403


class TestUnansweredQuestions:
    @pytest.fixture()
    def unanswered(self, organization) -> UnansweredQuestion:
        return UnansweredQuestion.objects.create(
            organization=organization,
            normalized_hash="3" * 64,
            question_text="Как получить справку 2-НДФЛ?",
            language="ru",
            status="NEW",
        )

    def test_clusters_are_listed(self, editor, unanswered):
        body = editor.get(f"{API}/knowledge/unanswered-questions/").json()

        assert [item["id"] for item in body["items"]] == [str(unanswered.id)]

    def test_a_foreign_cluster_is_not_reachable(
        self, api_client, make_user, other_organization, unanswered
    ):
        api_client.force_authenticate(
            user=make_user(other_organization, permissions=EDITOR)
        )

        assert api_client.get(
            f"{API}/knowledge/unanswered-questions/{unanswered.id}/"
        ).status_code == 404

    def test_a_cluster_becomes_a_draft_faq(self, editor, unanswered):
        """Сразу действующим он быть не может: эмбеддинга ещё нет."""
        response = editor.post(
            f"{API}/knowledge/unanswered-questions/{unanswered.id}/make-faq/",
            {"canonical_question": "Как получить справку 2-НДФЛ?",
             "approved_answer": "Закажите в личном кабинете.",
             "language": "ru"},
            format="json",
        )

        assert response.status_code == 201
        assert response.json()["status"] == "DRAFT"
        unanswered.refresh_from_db()
        assert unanswered.status == "ANSWERED"

    def test_assigning_across_organizations_is_refused(
        self, editor, unanswered, make_user, other_organization
    ):
        """Назначенный «наружу» вопрос молча зависает.

        Пользователь другой организации не увидит ни вопроса, ни базы
        знаний, а в списке он будет выглядеть взятым в работу.
        """
        outsider = make_user(other_organization, permissions=EDITOR)

        response = editor.post(
            f"{API}/knowledge/unanswered-questions/{unanswered.id}/assign/",
            {"to_user_id": str(outsider.id)},
            format="json",
        )

        assert response.status_code == 404
        unanswered.refresh_from_db()
        assert unanswered.assigned_to_user_id is None
