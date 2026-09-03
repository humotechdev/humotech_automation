from src.handlers._stub import make_stub_router
from src.keyboards import menu as kb

# TODO: POST /questions и GET /questions/my — вопрос в HR и лента ответов
router = make_stub_router("questions", "question", kb.BTN_QUESTION, "Вопрос в HR")
