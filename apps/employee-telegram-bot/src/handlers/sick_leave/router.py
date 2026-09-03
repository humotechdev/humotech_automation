from src.handlers._stub import make_stub_router
from src.keyboards import menu as kb

# TODO: POST /absences/sick-leave — FSM по образцу handlers/vacation + вложение (file_id)
router = make_stub_router("sick_leave", "sick_leave", kb.BTN_SICK_LEAVE, "Больничный")
