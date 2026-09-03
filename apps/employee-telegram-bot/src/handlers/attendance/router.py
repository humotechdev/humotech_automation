from src.handlers._stub import make_stub_router
from src.keyboards import menu as kb

# TODO: GET /attendance/my — список отметок за период
router = make_stub_router("attendance", "attendance", kb.BTN_ATTENDANCE, "Мои отметки")
