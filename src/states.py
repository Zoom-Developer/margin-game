from aiogram.fsm.state import StatesGroup, State


class UserState(StatesGroup):
    team_name = State()