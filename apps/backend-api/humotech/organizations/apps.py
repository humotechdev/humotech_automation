from django.apps import AppConfig


class OrganizationsConfig(AppConfig):
    name = "humotech.organizations"
    # Метка короткая: она входит в имена служебных объектов Django и
    # в ссылки вида "organizations.Organization" из других приложений.
    label = "organizations"
    verbose_name = "Организации"
