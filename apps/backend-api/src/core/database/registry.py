"""Единая точка сборки метаданных.

Импортирует все модули с моделями, чтобы:
  * `Base.metadata` содержала все таблицы (нужно Alembic для autogenerate);
  * SQLAlchemy мог разрешить строковые ссылки в `relationship()`.

Модели ссылаются друг на друга ТОЛЬКО строками ("Employee", "Office", ...),
поэтому между модулями нет циклических импортов: реальные классы связываются
один раз здесь, при конфигурации маппера.
"""

from __future__ import annotations

from src.core.database.base import Base

# порядок импорта роли не играет — связи разрешаются лениво
from src.modules.absences import models as absences_models  # noqa: F401
from src.modules.audit import models as audit_models  # noqa: F401
from src.modules.departments import models as departments_models  # noqa: F401
from src.modules.devices import models as devices_models  # noqa: F401
from src.modules.employees import models as employees_models  # noqa: F401
from src.modules.files import models as files_models  # noqa: F401
from src.modules.knowledge_base import models as knowledge_models  # noqa: F401
from src.modules.notifications import models as notifications_models  # noqa: F401
from src.modules.offices import models as offices_models  # noqa: F401
from src.modules.organizations import models as organizations_models  # noqa: F401
from src.modules.positions import models as positions_models  # noqa: F401
from src.modules.qr_attendance import models as qr_attendance_models  # noqa: F401
from src.modules.qr_codes import models as qr_codes_models  # noqa: F401
from src.modules.questions import models as questions_models  # noqa: F401
from src.modules.regions import models as regions_models  # noqa: F401
from src.modules.roles import models as roles_models  # noqa: F401
from src.modules.schedules import models as schedules_models  # noqa: F401
from src.modules.telegram import models as telegram_models  # noqa: F401
from src.modules.users import models as users_models  # noqa: F401

target_metadata = Base.metadata

__all__ = ["Base", "target_metadata"]
