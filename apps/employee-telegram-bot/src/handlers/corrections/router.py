from src.handlers._stub import make_stub_router
from src.keyboards import menu as kb

# TODO: POST /attendance/corrections — дата, что исправить, предлагаемое время, причина
router = make_stub_router("corrections", "correction", kb.BTN_CORRECTION,
                          "Исправить отметку")
