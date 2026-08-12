import pytest
from app.services.contact_extractor import ContactExtractor

def test_extract_telegram_from_remarks():
    remarks = "На связи 24/7. Мой телеграм: @crypto_trader_ua пишите туда!"
    contacts = ContactExtractor.extract_from_text(remarks, "remarks")
    assert len(contacts) == 1
    assert contacts[0].type == "telegram"
    assert contacts[0].value == "@crypto_trader_ua"

def test_extract_local_ua_phone():
    remarks = "Быстрая оплата на Монобанк. Тг или вайбер 0971234567, также 093 987 65 43"
    contacts = ContactExtractor.extract_from_text(remarks, "remarks")
    phones = [c.value for c in contacts if c.type in ("phone", "viber", "whatsapp")]
    assert len(phones) >= 2
    assert "+380971234567" in phones

def test_extract_from_merchant_data_priority():
    remarks = "Условия сделки: оплата только с личной карты. Контакт @manager_p2p"
    nickname = "SuperP2P"
    contacts = ContactExtractor.extract_from_merchant_data(nickname=nickname, remarks=remarks)
    assert len(contacts) == 1
    assert contacts[0].value == "@manager_p2p"
    assert contacts[0].raw_source == "remarks"
