"""FSM-сценарии бота."""

from aiogram.fsm.state import State, StatesGroup


class LinkAccount(StatesGroup):
    phone = State()
    code = State()


class VacationRequest(StatesGroup):
    date_from = State()
    date_to = State()
    comment = State()
    confirm = State()


class HrRejectAbsence(StatesGroup):
    reason = State()


class HrSearchEmployee(StatesGroup):
    query = State()
