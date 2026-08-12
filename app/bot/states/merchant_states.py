from aiogram.fsm.state import State, StatesGroup

class MerchantSearchForm(StatesGroup):
    query = State()
