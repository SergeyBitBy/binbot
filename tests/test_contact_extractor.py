from app.services.contact_extractor import ContactExtractor


def test_extract_telegram_handles():
    text = "Пишите в телеграм @crypto_king_777 или t.me/p2p_trader для быстрых сделок"
    contacts = ContactExtractor.extract_contacts(text)
    types = [c.type for c in contacts]
    values = [c.value for c in contacts]
    
    assert "telegram" in types
    assert "@crypto_king_777" in values
    assert "@p2p_trader" in values

def test_extract_whatsapp_and_phone():
    text = "Связь через WhatsApp +380971234567 или звоните +380509876543"
    contacts = ContactExtractor.extract_contacts(text)
    values = [c.value for c in contacts]
    
    assert "+380971234567" in values or "+380509876543" in values

def test_extract_email():
    text = "По всем вопросам пишите на manager.p2p@domain.com"
    contacts = ContactExtractor.extract_contacts(text)
    types = [c.type for c in contacts]
    values = [c.value for c in contacts]
    
    assert "email" in types
    assert "manager.p2p@domain.com" in values

def test_extract_from_merchant_data():
    contacts = ContactExtractor.extract_from_merchant_data(
        nickname="SergeP2P",
        remarks="ТГ @sergebybitp2p Быстрый обмен",
        auto_reply="WhatsApp wa.me/380931234567",
    )
    values = [c.value for c in contacts]
    assert "@sergebybitp2p" in values
    assert "+380931234567" in values
