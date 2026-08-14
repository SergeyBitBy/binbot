import asyncio
import json
import logging
import re
import time
from typing import List, Optional
import httpx

from app.services.contact_extractor import ExtractedContact

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an expert contact information extraction system specialized in Binance P2P trader profiles and advertisement terms.
Your goal is to accurately extract all direct communication contacts (Telegram, Phone, WhatsApp, Viber, Instagram, Email).

Domain Knowledge & Context:
1. Telegram is the primary communication channel on P2P markets. Traders often write handles using shorthand:
   - 'тг', 'т.г.', 'т г', 'tg', 't.g', 'телеграм', 'telegram', 'телеграмм', 'связь', 'для зв'язку', 'звʼязку', 'лс', 'в лс', 'в личку', 'в личные сообщения', 'PM' followed by or preceded by a handle.
   - Handles enclosed in emojis (e.g. '📎 handle 📎', '✈️ handle', '🤝 handle') or written as contact signatures.
2. Standardize all Telegram handles with a leading '@' (e.g. '@username').
3. Phone, WhatsApp, Viber: normalize to international format with leading '+' and no spaces (e.g. '+573128318338', '+380971234567').
4. DO NOT extract banking words (e.g. 'SEPA instant', 'Wise', 'Monobank', 'IBAN'), general phrases ('Message me', 'Online 24/7'), or company registration codes as usernames.
5. If a handle appears as a contact handle after phrases like 'в личные сообщения <handle>' or 'пишите <handle>' or 'Tг <handle>', extract it as telegram.

Output Schema:
You MUST respond ONLY with a JSON object:
{
  "contacts": [
    {"type": "telegram" | "phone" | "whatsapp" | "viber" | "instagram" | "email", "value": "normalized value"}
  ]
}
If no contacts are found, return {"contacts": []}.
"""

class GroqContactExtractor:
    """Ultra-fast, multilingual AI contact extractor powered by Groq LPUs."""

    def __init__(self, api_key: str = "", model: str = "llama-3.3-70b-versatile"):
        self.api_key = api_key
        self.model = model
        self.endpoint = "https://api.groq.com/openai/v1/chat/completions"

    async def extract_from_merchant_data(
        self,
        nickname: str = "",
        remarks: str = "",
        auto_reply: str = "",
        api_key: Optional[str] = None,
        model: Optional[str] = None,
    ) -> List[ExtractedContact]:
        """Extract contacts using Groq LLM inference with strict normalization."""
        effective_key = api_key or self.api_key
        effective_model = model or self.model

        if not effective_key:
            logger.warning("Groq API key is missing. Cannot perform AI extraction.")
            return []

        # Build combined context
        text_content = f"Merchant Nickname: {nickname or 'N/A'}\n"
        if remarks:
            text_content += f"Terms / Remarks:\n{remarks}\n"
        if auto_reply:
            text_content += f"Auto-Reply Message:\n{auto_reply}\n"

        headers = {
            "Authorization": f"Bearer {effective_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": effective_model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Extract all communication contacts from this trader's data:\n\n{text_content}"},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.0,
        }

        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                t0 = time.perf_counter()
                resp = await client.post(self.endpoint, headers=headers, json=payload)
                duration_ms = round((time.perf_counter() - t0) * 1000)

                if resp.status_code == 200:
                    data = resp.json()
                    content = data["choices"][0]["message"]["content"]
                    parsed = json.loads(content)
                    raw_contacts = parsed.get("contacts", [])

                    contacts = []
                    seen = set()
                    for item in raw_contacts:
                        c_type = str(item.get("type", "telegram")).lower().strip()
                        c_val = str(item.get("value", "")).strip()
                        if not c_val:
                            continue

                        # Strict normalization
                        if c_type == "telegram":
                            c_val = c_val.lstrip("@").strip()
                            if "t.me" not in c_val:
                                c_val = f"@{c_val}"
                        elif c_type in ("phone", "whatsapp", "viber"):
                            digits = re.sub(r"\D", "", c_val)
                            if len(digits) < 8 or len(digits) > 16:
                                continue
                            c_val = f"+{digits}"
                        elif c_type == "email":
                            c_val = c_val.lower().strip()

                        key = (c_type, c_val.lower())
                        if key not in seen:
                            seen.add(key)
                            contacts.append(ExtractedContact(type=c_type, value=c_val, raw_source="groq_ai"))

                    logger.info(f"Groq AI ({effective_model}) extracted {len(contacts)} contacts in {duration_ms}ms.")
                    return contacts
                else:
                    logger.error(f"Groq API error ({resp.status_code}): {resp.text[:200]}")
                    return []
        except Exception as e:
            logger.exception(f"Exception during Groq AI contact extraction: {e}")
            return []

    async def test_connection(self, api_key: str, model: str = "llama-3.3-70b-versatile") -> tuple[bool, str, int]:
        """Test Groq API key connection and return (success, message, latency_ms)."""
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": "Extract contacts:\n\nContact me on Telegram @test_trader"},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.0,
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                t0 = time.perf_counter()
                resp = await client.post(self.endpoint, headers=headers, json=payload)
                duration_ms = round((time.perf_counter() - t0) * 1000)

                if resp.status_code == 200:
                    data = resp.json()
                    content = data["choices"][0]["message"]["content"]
                    parsed = json.loads(content)
                    if parsed.get("contacts"):
                        return True, "✅ Подключение успешно! ИИ готов к работе.", duration_ms
                    return True, "✅ Ключ валиден!", duration_ms
                elif resp.status_code == 401:
                    return False, "❌ Ошибка 401: Неверный API-ключ Groq.", duration_ms
                else:
                    return False, f"❌ Ошибка {resp.status_code}: {resp.text[:100]}", duration_ms
        except Exception as e:
            return False, f"❌ Ошибка сети: {str(e)[:100]}", 0
