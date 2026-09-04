"""Справочники отделов и должностей.

Отдел принадлежит офису, должность — организации, и это не деталь схемы:
«Отдел разработки» в Душанбе и в Худжанде — разные отделы с разными
людьми, а «Инженер» один на всю компанию.

Отсюда и разная область видимости: отделы фильтруются по офисам, а
должности видны всем внутри организации.
"""

from __future__ import annotations

import pytest

from humotech.core.errors import NotFound, PermissionDenied, ValidationFailed
from humotech.departments.models import Department
from humotech.departments.services import DepartmentService, PositionService

pytestmark = pytest.mark.django_db

API = "/api/v1"


@pytest.fixture()
def catalog_actor(make_actor, organization):
    return make_actor(
        organization,
        permissions=(
            "offices.read", "employees.read",
            "departments.manage", "positions.manage",
        ),
    )


class TestDepartments:
    def test_department_belongs_to_an_office(self, catalog_actor, office):
        department = DepartmentService().create(
            catalog_actor, office_id=office.id, code="DEV", name="Разработка"
        )
        assert department.office_id == office.id
        assert department.status == "ACTIVE"

    def test_office_admin_sees_only_own_departments(
        self, catalog_actor, make_actor, organization, office, other_office
    ):
        service = DepartmentService()
        service.create(catalog_actor, office_id=office.id, code="MINE", name="Наш")
        service.create(
            catalog_actor, office_id=other_office.id, code="THEIRS", name="Чужой"
        )

        local = make_actor(
            organization, permissions=("offices.read",), office=office
        )
        codes = {d.code for d in service.list(local).items}
        assert codes == {"MINE"}

    def test_empty_scope_gives_nothing_not_everything(
        self, catalog_actor, make_actor, organization, office
    ):
        from humotech.regions.models import Region

        DepartmentService().create(
            catalog_actor, office_id=office.id, code="SOME", name="Отдел"
        )
        empty = Region.objects.create(
            organization=organization, code="EMPTY_DEP", name="Пустой",
            status="ACTIVE",
        )
        nobody = make_actor(
            organization, permissions=("offices.read",), region=empty
        )
        assert DepartmentService().list(nobody).items == []

    def test_hierarchy_stays_inside_one_office(
        self, catalog_actor, office, other_office
    ):
        """Отдел, вложенный в отдел другого офиса, означал бы, что человек
        числится сразу в двух местах, и «сотрудники офиса» перестало бы
        быть числом."""
        service = DepartmentService()
        parent = service.create(
            catalog_actor, office_id=office.id, code="PARENT", name="Родитель"
        )
        with pytest.raises(ValidationFailed):
            service.create(
                catalog_actor,
                office_id=other_office.id,
                code="CHILD",
                name="Ребёнок",
                parent_department_id=parent.id,
            )

    def test_department_is_switched_off_not_deleted(self, catalog_actor, office):
        service = DepartmentService()
        department = service.create(
            catalog_actor, office_id=office.id, code="OLD", name="Старый"
        )
        service.set_status(catalog_actor, department.id, status="INACTIVE")

        # Запись на месте: на неё ссылаются закрытые назначения, и стереть
        # её значило бы потерять, кем человек работал в прошлом году.
        assert Department.objects.filter(id=department.id).exists()

    def test_managing_needs_its_own_permission(
        self, make_actor, organization, office
    ):
        reader = make_actor(organization, permissions=("offices.read",))
        with pytest.raises(PermissionDenied):
            DepartmentService().create(
                reader, office_id=office.id, code="X", name="Отдел"
            )


class TestPositions:
    def test_position_has_no_office_scope(
        self, catalog_actor, make_actor, organization, other_office
    ):
        """Должность не принадлежит офису: «Инженер» одинаков для всех,
        и администратор любого офиса обязан его видеть."""
        PositionService().create(catalog_actor, code="ENG", name="Инженер")

        office_admin = make_actor(
            organization, permissions=("employees.read",), office=other_office
        )
        codes = {p.code for p in PositionService().list(office_admin).items}
        assert "ENG" in codes

    def test_foreign_organization_position_is_not_found(
        self, catalog_actor, other_organization, make_actor
    ):
        stranger = make_actor(other_organization, permissions=("positions.manage",))
        theirs = PositionService().create(stranger, code="THEIRS", name="Чужая")

        with pytest.raises(NotFound):
            PositionService().get(catalog_actor, theirs.id)

    def test_managing_needs_its_own_permission(self, make_actor, organization):
        reader = make_actor(organization, permissions=("employees.read",))
        with pytest.raises(PermissionDenied):
            PositionService().create(reader, code="X", name="Должность")


class TestHttp:
    @pytest.fixture()
    def client(self, api_client, make_user, organization):
        user = make_user(
            organization,
            permissions=("offices.read", "employees.read",
                         "departments.manage", "positions.manage"),
        )
        api_client.force_authenticate(user=user)
        return api_client

    def test_full_department_lifecycle_over_http(self, client, office):
        created = client.post(
            f"{API}/departments/",
            {"office_id": str(office.id), "code": "HTTP_DEV", "name": "Разработка"},
            format="json",
        )
        assert created.status_code == 201
        department_id = created.json()["id"]

        renamed = client.patch(
            f"{API}/departments/{department_id}/", {"name": "Инженерия"},
            format="json",
        )
        assert renamed.json()["name"] == "Инженерия"

        off = client.post(f"{API}/departments/{department_id}/deactivate/")
        assert off.json()["status"] == "INACTIVE"
        on = client.post(f"{API}/departments/{department_id}/reactivate/")
        assert on.json()["status"] == "ACTIVE"

    def test_no_delete_method(self, client, office):
        created = client.post(
            f"{API}/departments/",
            {"office_id": str(office.id), "code": "NODEL", "name": "Отдел"},
            format="json",
        ).json()

        response = client.delete(f"{API}/departments/{created['id']}/")
        assert response.status_code in (403, 404, 405)

    def test_positions_over_http(self, client):
        created = client.post(
            f"{API}/positions/", {"code": "HTTP_ENG", "name": "Инженер"},
            format="json",
        )
        assert created.status_code == 201
        listing = client.get(f"{API}/positions/").json()
        assert any(p["code"] == "HTTP_ENG" for p in listing["items"])
