import re

from pydantic import BaseModel


class ExtractedContact(BaseModel):
    type: str  # telegram, whatsapp, phone, email, viber, instagram, website, other
    value: str
    raw_match: str

class ContactExtractor:
    """Regex & heuristic parser to extract contact information from merchant text."""

    TELEGRAM_PATTERNS = [
        re.compile(r"(?:https?://)?t(?:elegram)?\.me/([a-zA-Z0-9_]{4,32})", re.IGNORECASE),
        re.compile(r"(?:telegram|телеграм|тг|tg|тлг)\s*[:=\-]?\s*@?([a-zA-Z0-9_]{4,32})", re.IGNORECASE),
        re.compile(r"(?<!\w)@([a-zA-Z0-9_]{4,32})(?!\w)", re.IGNORECASE),
    ]

    WHATSAPP_PATTERNS = [
        re.compile(r"(?:https?://)?wa\.me/(\+?[0-9]{8,15})", re.IGNORECASE),
        re.compile(r"(?:whatsapp|вацап|ватсап|wa)\s*[:=\-]?\s*(\+?[0-9\s\-\(\)]{8,20})", re.IGNORECASE),
    ]

    VIBER_PATTERNS = [
        re.compile(r"(?:viber|вайбер)\s*[:=\-]?\s*(\+?[0-9\s\-\(\)]{8,20})", re.IGNORECASE),
    ]

    INSTAGRAM_PATTERNS = [
        re.compile(r"(?:https?://)?(?:www\.)?instagram\.com/([a-zA-Z0-9_\.]{3,30})", re.IGNORECASE),
        re.compile(r"(?:inst|instagram|инста)\s*[:=\-]?\s*@?([a-zA-Z0-9_\.]{3,30})", re.IGNORECASE),
    ]

    EMAIL_PATTERNS = [
        re.compile(r"([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})", re.IGNORECASE),
    ]

    PHONE_PATTERNS = [
        re.compile(r"(\+?[0-9]{1,4}[\s\-\.]?\(?[0-9]{2,4}\)?[\s\-\.]?[0-9]{3,4}[\s\-\.]?[0-9]{2,4})", re.IGNORECASE),
    ]

    URL_PATTERNS = [
        re.compile(r"(https?://[^\s/$.?#].[^\s]*)", re.IGNORECASE),
    ]

    # Reserved words to exclude from Telegram handle false positives
    EXCLUDED_TG_HANDLES = {
        "binance", "support", "admin", "p2p", "trade", "bot", "usdt", "uah", "usd", "eur",
        "gmail", "yahoo", "com", "net", "org", "http", "https", "online", "crypto", "pay",
        "card", "bank", "mono", "privat", "revolut", "wise"
    }

    @classmethod
    def extract_contacts(cls, text: str) -> list[ExtractedContact]:
        if not text:
            return []

        contacts: list[ExtractedContact] = []
        seen_values: set[str] = set()

        # 1. Telegram
        for pattern in cls.TELEGRAM_PATTERNS:
            for match in pattern.finditer(text):
                handle = match.group(1).strip()
                if handle.lower() not in cls.EXCLUDED_TG_HANDLES and len(handle) >= 4:
                    full_val = f"@{handle}" if not handle.startswith("@") else handle
                    val_key = f"telegram:{full_val.lower()}"
                    if val_key not in seen_values:
                        seen_values.add(val_key)
                        contacts.append(ExtractedContact(type="telegram", value=full_val, raw_match=match.group(0)))

        # 2. WhatsApp
        for pattern in cls.WHATSAPP_PATTERNS:
            for match in pattern.finditer(text):
                raw_num = match.group(1)
                clean_num = re.sub(r"[^\d+]", "", raw_num)
                if len(re.sub(r"\D", "", clean_num)) >= 8:
                    val_key = f"whatsapp:{clean_num}"
                    if val_key not in seen_values:
                        seen_values.add(val_key)
                        contacts.append(ExtractedContact(type="whatsapp", value=clean_num, raw_match=match.group(0)))

        # 3. Viber
        for pattern in cls.VIBER_PATTERNS:
            for match in pattern.finditer(text):
                raw_num = match.group(1)
                clean_num = re.sub(r"[^\d+]", "", raw_num)
                if len(re.sub(r"\D", "", clean_num)) >= 8:
                    val_key = f"viber:{clean_num}"
                    if val_key not in seen_values:
                        seen_values.add(val_key)
                        contacts.append(ExtractedContact(type="viber", value=clean_num, raw_match=match.group(0)))

        # 4. Instagram
        for pattern in cls.INSTAGRAM_PATTERNS:
            for match in pattern.finditer(text):
                handle = match.group(1).strip()
                if handle.lower() not in cls.EXCLUDED_TG_HANDLES:
                    val_key = f"instagram:{handle.lower()}"
                    if val_key not in seen_values:
                        seen_values.add(val_key)
                        contacts.append(ExtractedContact(type="instagram", value=f"@{handle}", raw_match=match.group(0)))

        # 5. Email
        for pattern in cls.EMAIL_PATTERNS:
            for match in pattern.finditer(text):
                email = match.group(1).strip().lower()
                val_key = f"email:{email}"
                if val_key not in seen_values:
                    seen_values.add(val_key)
                    contacts.append(ExtractedContact(type="email", value=email, raw_match=match.group(0)))

        # 6. Phone Numbers (Stand-alone phone pattern)
        for pattern in cls.PHONE_PATTERNS:
            for match in pattern.finditer(text):
                raw_num = match.group(1)
                digits_only = re.sub(r"\D", "", raw_num)
                if 10 <= len(digits_only) <= 15:
                    clean_phone = f"+{digits_only}" if not raw_num.startswith("+") else f"+{digits_only}"
                    # Don't add if already captured as whatsapp/viber
                    if not any(clean_phone in k for k in seen_values):
                        val_key = f"phone:{clean_phone}"
                        if val_key not in seen_values:
                            seen_values.add(val_key)
                            contacts.append(ExtractedContact(type="phone", value=clean_phone, raw_match=match.group(0)))

        return contacts

    @classmethod
    def extract_from_merchant_data(cls, nickname: str, remarks: str, auto_reply: str) -> list[ExtractedContact]:
        combined_text = f"{nickname or ''}\n{remarks or ''}\n{auto_reply or ''}"
        return cls.extract_contacts(combined_text)
