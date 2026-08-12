from aiogram.fsm.state import State, StatesGroup


class ProfileForm(StatesGroup):
    name = State()
    asset = State()
    fiat = State()
    trade_type = State()
    scan_interval = State()

class MerchantSearchForm(StatesGroup):
    query = State()
