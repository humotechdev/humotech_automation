"""База знаний глазами кадровой CRM: список, версии, счётчики, изоляция.

Что здесь проверяется и почему именно это.

**Документ и версия — разные вещи.** Каждая версия живёт отдельной
строкой `knowledge_sources`. В списке кадровика документ должен быть
один, иначе «10 документов» означает «10 строк таблицы», а переизданный
трижды регламент занимает три места. Счётчики считают по тому же
правилу — иначе число рядом с вкладкой и длина списка под ней
разъезжаются на первом же переиздании.

**Чужое остаётся чужим.** Разрешение отвечает «что можно делать», но не
«с чьими данными». Проверяются четыре пути: карточка, история версий,
чужой документ как родитель новой версии и чужой документ как источник
FAQ.

**Выключенный ассистент — это состояние, а не поломка.** Признак
доступности отдаётся отдельным полем с причиной словом; ни ключа, ни
имени модели наружу не идёт. Ни один тест не обращается к провайдеру.
"""

from __future__ import annotations

import pytest

from humotech.ai_assistant.config import AiSettings
from humotech.knowledge.models import FaqEntry, KnowledgeSource

pytestmark = pytest.mark.django_db

API = "/api/v1"
EDITOR = (
    "knowledge.read", "knowledge.write", "knowledge.index",
    "knowledge.publish",
)


@pytest.fixture()
def author(make_user, organization):
    return make_user(organization, permissions=EDITOR)


@pytest.fixture()
def editor(api_client, make_user, organization):
    user = make_user(organization, permissions=EDITOR)
    api_client.force_authenticate(user=user)
    api_client.humotech_user = user
    return api_client


def make_source(organization, author, **kwargs) -> KnowledgeSource:
    defaults = {
        "title": "Тестовый документ — проверка списка",
        "source_type": "POLICY",
        "language": "ru",
        "content": "Проверочный текст.",
        "content_hash": "0" * 64,
        "status": "DRAFT",
        "version": 1,
    }
    defaults.update(kwargs)
    return KnowledgeSource.objects.create(
        organization=organization, created_by_user=author, **defaults
    )


# --- документ против версии --------------------------------------------------


class TestDocumentIsNotAVersion:
    def test_three_versions_are_one_row(self, editor, organization, author):
        first = make_source(organization, author, status="ARCHIVED", version=1)
        second = make_source(
            organization, author, status="ARCHIVED", version=2, parent_source=first
        )
        newest = make_source(
            organization, author, status="DRAFT", version=3, parent_source=second
        )

        body = editor.get(f"{API}/knowledge/sources/").json()

        assert [item["id"] for item in body["items"]] == [str(newest.id)]
        assert body["items"][0]["version"] == 3

    def test_counts_use_the_same_rule_as_the_list(
        self, editor, organization, author
    ):
        """Иначе «8 черновиков» не сходится с восемью строками под ним."""
        old = make_source(organization, author, status="ARCHIVED", version=1)
        make_source(
            organization, author, status="DRAFT", version=2, parent_source=old
        )
        make_source(organization, author, title="Второй тестовый", status="DRAFT")

        counts = editor.get(f"{API}/knowledge/sources/counts/").json()

        assert counts["total"] == 2
        assert counts["DRAFT"] == 2
        # Архивная версия — часть истории первого документа, а не
        # отдельный архивный документ.
        assert counts["ARCHIVED"] == 0

    def test_a_draft_over_an_archive_is_a_draft(self, editor, organization, author):
        """Статус документа — статус его САМОЙ НОВОЙ версии.

        Если брать самую новую среди подходящих под фильтр, документ
        с черновиком поверх архива показался бы и там, и там.
        """
        old = make_source(organization, author, status="ARCHIVED", version=1)
        make_source(
            organization, author, status="DRAFT", version=2, parent_source=old
        )

        archived = editor.get(
            f"{API}/knowledge/sources/", {"status": "ARCHIVED"}
        ).json()
        drafts = editor.get(f"{API}/knowledge/sources/", {"status": "DRAFT"}).json()

        assert archived["items"] == []
        assert len(drafts["items"]) == 1

    def test_every_version_is_reachable_on_request(
        self, editor, organization, author
    ):
        old = make_source(organization, author, status="ARCHIVED", version=1)
        make_source(
            organization, author, status="DRAFT", version=2, parent_source=old
        )

        body = editor.get(
            f"{API}/knowledge/sources/", {"all_versions": "true"}
        ).json()

        assert len(body["items"]) == 2

    def test_the_list_carries_the_author_and_the_scope(
        self, editor, organization, author, office
    ):
        make_source(organization, author, office=office)

        row = editor.get(f"{API}/knowledge/sources/").json()["items"][0]

        assert row["created_by"] == author.email
        assert row["office_name"] == office.name
        # Текста в списке нет: он приходит с карточкой.
        assert "content" not in row

    def test_the_card_carries_the_text(self, editor, organization, author):
        source = make_source(organization, author)

        card = editor.get(f"{API}/knowledge/sources/{source.id}/").json()

        assert card["content"] == "Проверочный текст."


# --- история версий ----------------------------------------------------------


class TestVersionHistory:
    def test_history_is_newest_first(self, editor, organization, author):
        first = make_source(organization, author, status="ARCHIVED", version=1)
        second = make_source(
            organization, author, status="DRAFT", version=2, parent_source=first
        )

        rows = editor.get(f"{API}/knowledge/sources/{first.id}/versions/").json()

        assert [row["version"] for row in rows] == [2, 1]
        assert rows[0]["id"] == str(second.id)

    def test_history_is_the_same_from_any_version(
        self, editor, organization, author
    ):
        """Открыли старую версию — история та же: линейка одна."""
        first = make_source(organization, author, status="ARCHIVED", version=1)
        second = make_source(
            organization, author, status="DRAFT", version=2, parent_source=first
        )

        from_old = editor.get(f"{API}/knowledge/sources/{first.id}/versions/").json()
        from_new = editor.get(f"{API}/knowledge/sources/{second.id}/versions/").json()

        assert [row["id"] for row in from_old] == [row["id"] for row in from_new]

    def test_another_language_is_another_document(
        self, editor, organization, author
    ):
        """Линейка — это заголовок ПЛЮС язык, как и уникальность активной."""
        russian = make_source(organization, author, language="ru")
        make_source(organization, author, language="en")

        rows = editor.get(f"{API}/knowledge/sources/{russian.id}/versions/").json()

        assert len(rows) == 1


# --- изоляция ----------------------------------------------------------------


class TestIsolation:
    def test_a_foreign_document_has_no_history(
        self, api_client, make_user, organization, other_organization, author
    ):
        source = make_source(organization, author)
        api_client.force_authenticate(
            user=make_user(other_organization, permissions=EDITOR)
        )

        response = api_client.get(f"{API}/knowledge/sources/{source.id}/versions/")

        assert response.status_code == 404

    def test_foreign_documents_are_not_counted(
        self, api_client, make_user, organization, other_organization, author
    ):
        make_source(organization, author)
        api_client.force_authenticate(
            user=make_user(other_organization, permissions=EDITOR)
        )

        counts = api_client.get(f"{API}/knowledge/sources/counts/").json()

        assert counts["total"] == 0

    def test_a_faq_cannot_point_at_a_foreign_document(
        self, api_client, make_user, organization, other_organization, author
    ):
        source = make_source(organization, author)
        api_client.force_authenticate(
            user=make_user(other_organization, permissions=EDITOR)
        )

        response = api_client.post(
            f"{API}/knowledge/faq/",
            {"canonical_question": "Проверка?", "approved_answer": "Ответ.",
             "language": "ru", "source_id": str(source.id)},
            format="json",
        )

        assert response.status_code == 404
        assert not FaqEntry.objects.filter(source_id=source.id).exists()

    def test_filtering_faq_by_a_foreign_document_is_refused(
        self, api_client, make_user, organization, other_organization, author
    ):
        """Иначе по чужому идентификатору читается, есть ли у соседей FAQ."""
        source = make_source(organization, author)
        api_client.force_authenticate(
            user=make_user(other_organization, permissions=EDITOR)
        )

        response = api_client.get(
            f"{API}/knowledge/faq/", {"source_id": str(source.id)}
        )

        assert response.status_code == 404


# --- признак доступности -----------------------------------------------------


class TestCapability:
    def test_the_switch_is_named_as_the_reason(self, editor):
        body = editor.get(f"{API}/knowledge/sources/capability/").json()

        assert body["embeddings_available"] is False
        assert body["reason"] == "ai_disabled"

    def test_a_missing_key_is_a_different_reason(self, editor, monkeypatch):
        """«Выключено» чинит один человек, «не настроено» — другой."""
        monkeypatch.setattr(
            "humotech.ai_assistant.availability.ai_settings",
            AiSettings(ai_assistant_enabled=True, openai_api_key=""),
        )
        monkeypatch.setattr(
            "humotech.ai_assistant.config.ai_settings",
            AiSettings(ai_assistant_enabled=True, openai_api_key=""),
        )

        body = editor.get(f"{API}/knowledge/sources/capability/").json()

        assert body["reason"] == "provider_not_configured"

    def test_no_provider_settings_leak(self, editor):
        body = editor.get(f"{API}/knowledge/sources/capability/").json()

        assert set(body) == {"embeddings_available", "reason"}

    def test_reading_capability_needs_the_permission(
        self, api_client, make_user, organization
    ):
        api_client.force_authenticate(
            user=make_user(organization, permissions=("employees.read",))
        )

        response = api_client.get(f"{API}/knowledge/sources/capability/")

        assert response.status_code == 403


# --- FAQ ---------------------------------------------------------------------


class TestFaqQueue:
    def make(self, organization, author, **kwargs) -> FaqEntry:
        defaults = {
            "canonical_question": "Тестовый вопрос?",
            "approved_answer": "Тестовый ответ.",
            "language": "ru",
            "content_hash": "1" * 64,
            "status": "DRAFT",
        }
        defaults.update(kwargs)
        return FaqEntry.objects.create(
            organization=organization, created_by_user=author, **defaults
        )

    def test_counts_do_not_follow_the_open_tab(self, editor, organization, author):
        self.make(organization, author)
        self.make(organization, author, status="ARCHIVED")

        counts = editor.get(
            f"{API}/knowledge/faq/counts/", {"status": "ARCHIVED"}
        ).json()

        assert counts["total"] == 2
        assert counts["DRAFT"] == 1
        assert counts["ARCHIVED"] == 1

    def test_several_statuses_come_in_one_page(self, editor, organization, author):
        self.make(organization, author)
        self.make(organization, author, status="ARCHIVED")

        page = editor.get(
            f"{API}/knowledge/faq/", {"status": "DRAFT,ARCHIVED"}
        ).json()

        assert len(page["items"]) == 2

    def test_the_scope_filter_narrows_the_list(
        self, editor, organization, author, office
    ):
        self.make(organization, author, office=office)
        self.make(organization, author)

        page = editor.get(
            f"{API}/knowledge/faq/", {"office_id": str(office.id)}
        ).json()

        assert len(page["items"]) == 1
        assert page["items"][0]["office_name"] == office.name

    def test_a_linked_document_is_named(self, editor, organization, author):
        source = make_source(organization, author)
        self.make(organization, author, source=source)

        row = editor.get(f"{API}/knowledge/faq/").json()["items"][0]

        assert row["source_title"] == source.title
        assert row["indexed"] is False


# --- запреты приходят понятным отказом ---------------------------------------


class TestRefusalsAreReadable:
    """Запрет — это ответ, а не пятисотка.

    `PublishingError` — обычное исключение, не доменная ошибка: до этих
    проверок «черновик архивировать нельзя» приходило в браузер пустой
    пятисоткой. Сами правила при этом верные и не менялись.
    """

    def test_a_draft_cannot_be_archived(self, editor, organization, author):
        source = make_source(organization, author, status="DRAFT")

        response = editor.post(f"{API}/knowledge/sources/{source.id}/archive/")

        assert response.status_code == 409
        assert "действующую" in response.json()["error"]["message"]
        source.refresh_from_db()
        assert source.status == "DRAFT"

    def test_an_active_document_is_not_edited_in_place(
        self, editor, organization, author
    ):
        """Действующую версию правят новой версией, а не поверх."""
        source = make_source(organization, author, status="ACTIVE")

        response = editor.patch(
            f"{API}/knowledge/sources/{source.id}/",
            {"content": "Другой текст"},
            format="json",
        )

        assert response.status_code == 409
        source.refresh_from_db()
        assert source.content == "Проверочный текст."

    def test_a_document_cannot_belong_to_an_office_and_a_region_at_once(
        self, editor, organization, office, region
    ):
        response = editor.post(
            f"{API}/knowledge/sources/",
            {"title": "Тестовый — область", "source_type": "POLICY",
             "language": "ru", "content": "Текст",
             "office_id": str(office.id), "region_id": str(region.id)},
            format="json",
        )

        assert response.status_code == 409
        assert not KnowledgeSource.objects.filter(
            title="Тестовый — область"
        ).exists()

    def test_a_new_version_is_the_way_to_change_a_published_document(
        self, editor, organization, author
    ):
        """Разрешённый сценарий: новая версия на основе предыдущей."""
        published = make_source(organization, author, status="ACTIVE")

        response = editor.post(
            f"{API}/knowledge/sources/",
            {"title": published.title, "source_type": "POLICY",
             "language": "ru", "content": "Уточнённый текст",
             "parent_source_id": str(published.id)},
            format="json",
        )

        assert response.status_code == 201
        assert response.json()["version"] == 2
        assert response.json()["status"] == "DRAFT"
        # Действующая версия продолжает действовать, пока новая — черновик.
        published.refresh_from_db()
        assert published.status == "ACTIVE"


# --- при выключенном ассистенте наружу не уходит ничего ----------------------


class TestNothingLeavesWithTheAssistantOff:
    def test_indexing_never_reaches_the_provider(
        self, editor, organization, author, monkeypatch
    ):
        """Отказ наступает ДО создания провайдера, а не вместо ответа."""

        def explode(*args, **kwargs):  # pragma: no cover - не должно вызваться
            raise AssertionError("провайдер не должен создаваться")

        monkeypatch.setattr(
            "humotech.ai_assistant.providers.build_embedding_provider",
            explode,
            raising=False,
        )
        source = make_source(organization, author)

        response = editor.post(f"{API}/knowledge/sources/{source.id}/index/")

        assert response.status_code == 503
        assert response.json()["error"]["code"] == "ai_disabled"

    def test_a_faq_cannot_be_switched_on_without_an_embedding(
        self, editor, organization, author
    ):
        faq = FaqEntry.objects.create(
            organization=organization,
            created_by_user=author,
            canonical_question="Тестовый вопрос?",
            approved_answer="Тестовый ответ.",
            language="ru",
            content_hash="1" * 64,
            status="DRAFT",
        )

        response = editor.post(f"{API}/knowledge/faq/{faq.id}/activate/")

        assert response.status_code == 409
        faq.refresh_from_db()
        assert faq.status == "DRAFT"
