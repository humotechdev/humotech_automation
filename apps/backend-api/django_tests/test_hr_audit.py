"""Журнал изменений: читается, но не пишется через API.

Журнал, который можно отредактировать тем же ключом, которым делают
изменения, ничего не доказывает. Поэтому здесь проверяется не только то,
что записи видны, но и то, что записать или стереть их снаружи нельзя.
"""

from __future__ import annotations

import pytest

from humotech.core.errors import PermissionDenied
from humotech.regions.services import RegionService

pytestmark = pytest.mark.django_db

API = "/api/v1"


@pytest.fixture()
def auditor(api_client, make_user, organization):
    user = make_user(organization, permissions=("audit.read",))
    api_client.force_authenticate(user=user)
    return api_client


@pytest.fixture()
def some_change(make_actor, organization):
    """Настоящее изменение, а не выдуманная строка журнала."""
    actor = make_actor(organization, permissions=("regions.manage",))
    region = RegionService().create(
        actor, code="AUDITED", name="Проверяемый", timezone="Asia/Dushanbe"
    )
    return actor, region


class TestReading:
    def test_change_appears_in_the_log(self, auditor, some_change):
        actor, region = some_change

        body = auditor.get(f"{API}/audit-logs", {"entity_id": str(region.id)}).json()

        assert body["items"], "изменение обязано оставить след"
        entry = body["items"][0]
        assert entry["entity_type"] == "regions"
        assert entry["actor_user_id"] == str(actor.user_id)
        assert entry["new_values"]["code"] == "AUDITED"

    def test_filter_by_action_prefix(self, auditor, some_change):
        body = auditor.get(f"{API}/audit-logs", {"action": "region."}).json()
        assert body["items"]
        for entry in body["items"]:
            assert entry["action"].startswith("region.")

    def test_reading_requires_its_own_permission(
        self, api_client, make_user, organization
    ):
        # Право менять данные не даёт права читать журнал: это разные
        # роли, и совмещать их в одной проверке нельзя.
        user = make_user(organization, permissions=("regions.manage",))
        api_client.force_authenticate(user=user)

        assert api_client.get(f"{API}/audit-logs").status_code == 403

    def test_other_organization_is_invisible(
        self, auditor, other_organization, make_actor
    ):
        stranger = make_actor(other_organization, permissions=("regions.manage",))
        RegionService().create(
            stranger, code="THEIRS", name="Чужой", timezone="Asia/Dushanbe"
        )

        body = auditor.get(f"{API}/audit-logs").json()
        assert all("THEIRS" not in str(e["new_values"]) for e in body["items"])


class TestImmutability:
    @pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
    def test_log_cannot_be_written_through_the_api(self, auditor, method):
        response = getattr(auditor, method)(f"{API}/audit-logs", {}, format="json")
        assert response.status_code in (403, 404, 405), (
            "журнал, который можно править снаружи, ничего не доказывает"
        )

    def test_secrets_never_reach_the_log(
        self, auditor, make_actor, organization, office
    ):
        """`AuditTrail.sanitize` вырезает пароли и токены при ЗАПИСИ.

        Проверяется через настоящую операцию, а не через вызов sanitize:
        важно, что путь от сервиса до строки журнала не проносит секрет.
        """
        from humotech.qr_codes.points import QrPointService
        manager = make_actor(organization, permissions=("qr_points.manage",))
        issued = QrPointService().create(
            manager, office_id=office.id, code="AUDIT_QR", name="Точка",
            qr_mode="STATIC",
        )

        body = auditor.get(
            f"{API}/audit-logs", {"entity_id": str(issued.point.id)}
        ).json()
        assert body["items"]
        assert issued.static_token not in str(body)
