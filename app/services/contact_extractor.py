import re
import logging
from dataclasses import dataclass
from typing import List, Set, Tuple

logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class ExtractedContact:
    type: str  # telegram, whatsapp, viber, phone, email, instagram
    value: str
    raw_source: str  # remarks, auto_reply, nickname

    @property
    def raw_match(self) -> str:
        return self.value

class ContactExtractor:
    """Enhanced extractor to parse contacts from advertisement descriptions (remarks), auto-replies, and nicknames."""

    # Regex Patterns with explicit capturing group (group 1)
    PATTERNS = {
        "telegram": [
            # Direct links
            r"(?:t\.me|telegram\.me)/(?:\+)?([a-zA-Z0-9_\+]{4,64})",
            # Spaced or punctuated t g / т г / т.г / т_г / t.g
            r"(?:t|т)[\s._\-–]*(?:g|г)[:\s—\-–=]*@?([a-zA-Z0-9_]{4,32})",
            # Keyphrase prefixes (TG, Telegram, связь, для зв'язку, личные сообщения, лс, личка, сотрудничество, etc.)
            r"(?:tg|тг|телеграм|telegram|связь|зв[ʼ'\`]?язку|для\s+зв[ʼ'\`]?язку|для\s+связи|канал|чат|контакт|контакты|написать|пишите|инфо|info|личку|личка|лс|л\.с\.|личные\s+сообщения|личных\s+сообщениях|співпраця|сотрудничество)[:\s—\-–=.]*@?([a-zA-Z0-9_]{4,32})",
            # Direct @username handle
            r"(?<!\w)@([a-zA-Z0-9_]{4,32})(?!\w)",
            # Emoji-bounded handle (e.g. 📎 handle 📎, 🤝 handle, 📲 handle)
            r"(?:📎|📩|📱|💬|✈️|📲|👉|🤙|🤝|👤|➡️|🔗|🔹|🔸|⚡)[:\s—\-–=]*@?([a-zA-Z0-9_]{4,32})(?=\s*(?:📎|📩|📱|💬|✈️|📲|👉|🤙|🤝|👤|➡️|🔗|🔹|🔸|⚡|\s|\.|\,|$))",
        ],
        "whatsapp": [
            r"(?:wa\.me|api\.whatsapp\.com/send\?phone=)(\d{10,15})",
            r"(?:wa|whatsapp|ватсап|ватцап|вацап)[:\s—\-–=]*\+?(\d{10,15})",
        ],
        "viber": [
            r"(?:viber\.click|viber://chat\?number=)(\d{10,15})",
            r"(?:viber|вайбер)[:\s—\-–=]*\+?(\d{10,15})",
        ],
        "email": [
            r"([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})",
        ],
        "instagram": [
            r"(?:inst|instagram|инстаграм|инста)[:\s—\-–=]*@?([a-zA-Z0-9._]{3,30})",
            r"instagram\.com/([a-zA-Z0-9._]{3,30})",
        ],
        "phone": [
            # International format with +
            r"(\+\d{1,4}[-.\s]?\(?\d{1,4}\)?[-.\s]?\d{1,4}[-.\s]?\d{1,9})",
            # Local Ukrainian numbers: 0971234567, 093 123 45 67, 050-123-4567, 380971234567
            r"(?<!\d)((?:380|0)\d{2}[-.\s]?\d{3}[-.\s]?\d{2}[-.\s]?\d{2})(?!\d)",
        ],
    }

    # Blacklist of common keywords to prevent false positive telegram matches
    TG_BLACKLIST = {
        "binance", "support", "admin", "p2p", "help", "bot", "online", "fast",
        "trade", "usdt", "uah", "rub", "usd", "eur", "mono", "privat", "pumb",
        "bank", "card", "pay", "order", "buyer", "seller", "crypto", "change",
        "privatbank", "monobank", "a-bank", "abank", "vlasnyirakhunok",
    }

    @classmethod
    def extract_from_text(cls, text: str, source_label: str = "text") -> List[ExtractedContact]:
        if not text:
            return []

        found_contacts: List[ExtractedContact] = []
        seen_values: Set[Tuple[str, str]] = set()

        clean_text = text.strip()

        for c_type, patterns in cls.PATTERNS.items():
            for pattern in patterns:
                for match in re.finditer(pattern, clean_text, re.IGNORECASE):
                    val = (match.group(1) if match.lastindex else match.group(0)).strip()
                    
                    # Normalization
                    if c_type == "telegram":
                        val = val.lstrip("@").strip()
                        if len(val) < 4 or val.lower() in cls.TG_BLACKLIST or val.isdigit():
                            continue
                        val = f"@{val}"
                    elif c_type in ("phone", "whatsapp", "viber"):
                        # Clean non-digits for phone numbers
                        digits = re.sub(r"\D", "", val)
                        if len(digits) < 9 or len(digits) > 15:
                            continue
                        if c_type == "phone" and digits.startswith("0") and len(digits) == 10:
                            digits = f"38{digits}"  # Normalize local UA 0XX... to 380XX...
                        val = f"+{digits}" if not digits.startswith("+") else digits
                    elif c_type == "email":
                        val = val.lower()

                    key = (c_type, val)
                    if key not in seen_values:
                        seen_values.add(key)
                        found_contacts.append(ExtractedContact(type=c_type, value=val, raw_source=source_label))

        return found_contacts

    @classmethod
    def extract_from_merchant_data(
        cls,
        nickname: str = "",
        remarks: str = "",
        auto_reply: str = "",
    ) -> List[ExtractedContact]:
        """Extract contacts prioritize advertisement description (remarks) & auto reply, then nickname."""
        all_contacts: List[ExtractedContact] = []
        seen_values: Set[Tuple[str, str]] = set()

        # Primary Search: Advertisement Remarks/Description
        for c in cls.extract_from_text(remarks, source_label="remarks"):
            key = (c.type, c.value)
            if key not in seen_values:
                seen_values.add(key)
                all_contacts.append(c)

        # Secondary Search: Auto Reply Message
        for c in cls.extract_from_text(auto_reply, source_label="auto_reply"):
            key = (c.type, c.value)
            if key not in seen_values:
                seen_values.add(key)
                all_contacts.append(c)

        # Fallback / Supplemental Search: Merchant Nickname
        for c in cls.extract_from_text(nickname, source_label="nickname"):
            key = (c.type, c.value)
            if key not in seen_values:
                seen_values.add(key)
                all_contacts.append(c)

        return all_contacts
