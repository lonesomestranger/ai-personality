from aiogram.fsm.state import State, StatesGroup


class AdminStates(StatesGroup):
    waiting_for_n_value = State()
    waiting_for_n_group_value = State()
    waiting_for_cooldown_value = State()

    waiting_for_exclusion_chat = State()
    waiting_for_restriction_chat = State()
    waiting_for_priority_chat = State()
