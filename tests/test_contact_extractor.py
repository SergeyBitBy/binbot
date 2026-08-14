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

def test_extract_user_provided_edge_cases():
    # Case 1: Serg_Exch01 (in message prefix)
    c1 = ContactExtractor.extract_from_text("По всем вопросам — в личные сообщения: @SergExch01")
    assert any(c.value == "@SergExch01" for c in c1)

    # Case 2: P2Punk (Latin/Cyrillic Tг and Ukrainian apostrophe для звʼязку)
    c2 = ContactExtractor.extract_from_text("Для звʼязку — Tг P2Punk15")
    assert any(c.value == "@P2Punk15" for c in c2)

    # Case 3: ExchangeStable (spaced т г diddork)
    c3 = ContactExtractor.extract_from_text("Нацелен на долгосрочное сотрудничество\nт г: diddork")
    assert any(c.value == "@diddork" for c in c3)

    # Case 4: direct username handle
    c4 = ContactExtractor.extract_from_text("Завжди буду радий співпраці 🥰 @jeffrieflopper")
    assert any(c.value == "@jeffrieflopper" for c in c4)

def test_sepa_instant_no_false_positive_instagram():
    text = "Payment from my partner account by SEPA instant. Accept only DE/ES/NL/FR/BE"
    contacts = ContactExtractor.extract_from_text(text, "remarks")
    assert len(contacts) == 0

def test_no_false_positives_for_common_words():
    text = "👉 Welcome to my trade window 🤝 Trusted counterparty 👤 Solo por número 🔗 Leave necessary information"
    contacts = ContactExtractor.extract_from_text(text, "remarks")
    assert len(contacts) == 0

def test_nicknames_never_extracted_as_contacts():
    contacts = ContactExtractor.extract_from_merchant_data(nickname="MonexChangePro", remarks="Fast trader", auto_reply="")
    assert len(contacts) == 0
