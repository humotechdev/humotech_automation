from src.handlers._stub import make_stub_router
from src.keyboards import menu as kb

# TODO: GET /attendance/my/summary — часы, опоздания, переработки (считает бэкенд)
router = make_stub_router("statistics", "statistics", kb.BTN_STATISTICS, "Статистика")
