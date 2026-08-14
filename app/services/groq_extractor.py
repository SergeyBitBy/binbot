import asyncio
import json
import logging
import re
import time
from typing import List, Optional
import httpx

from app.services.contact_extractor import ExtractedContact, ContactExtractor

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
    """Ultra-fast, multilingual AI contact extractor powered by Groq LPUs with smart caching, pre-filtering and failover."""

    def __init__(self, api_key: str = "", model: str = "llama-3.1-8b-instant"):
        self.api_key = api_key
        self.model = model
        self.endpoint = "https://api.groq.com/openai/v1/chat/completions"

    @staticmethod
    def has_potential_contacts(text: str) -> bool:
        """Fast heuristic check to avoid unnecessary LLM calls when text has no contact patterns."""
        if not text or len(text.strip()) < 3:
            return False
        lower = text.lower()
        if "@" in lower or "t.me" in lower or "wa.me" in lower or "viber://" in lower:
            return True
        keywords = [
            "tg", "тг", "телеграм", "telegram", "телеграмм", "viber", "вайбер",
            "whats", "ватсап", "вацап", "связь", "зв'яз", "звʼяз", "личк", "лс",
            "pm", "inst", "инст", "phone", "тел", "contact", "контакт"
        ]
        if any(k in lower for k in keywords):
            return True
        # Check for sequences of at least 7 digits (phone numbers)
        if re.search(r"\+?\d[\d\s\-\(\)]{6,}\d", text):
            return True
        return False

    async def _query_groq_api(self, client: httpx.AsyncClient, effective_key: str, model_name: str, text_content: str) -> Optional[List[ExtractedContact]]:
        headers = {
            "Authorization": f"Bearer {effective_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Extract all communication contacts from this trader's data:\n\n{text_content}"},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.0,
        }

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

            logger.debug(f"Groq AI ({model_name}) extracted {len(contacts)} contacts in {duration_ms}ms.")
            return contacts
        else:
            logger.warning(f"Groq API error ({resp.status_code}) on {model_name}: {resp.text[:150]}")
            return None

    async def extract_from_merchant_data(
        self,
        nickname: str = "",
        remarks: str = "",
        auto_reply: str = "",
        api_key: Optional[str] = None,
        model: Optional[str] = None,
    ) -> List[ExtractedContact]:
        """Extract contacts using Groq LLM inference with fast pre-filtering and automatic regex fallback."""
        effective_key = api_key or self.api_key
        effective_model = model or self.model or "llama-3.1-8b-instant"

        # Combined text for fast heuristic pre-filtering
        combined_text = f"{nickname} {remarks} {auto_reply}".strip()
        if not self.has_potential_contacts(combined_text):
            return []

        if not effective_key:
            return ContactExtractor.extract_from_merchant_data(nickname=nickname, remarks=remarks, auto_reply=auto_reply)

        text_content = f"Merchant Nickname: {nickname or 'N/A'}\n"
        if remarks:
            text_content += f"Terms / Remarks:\n{remarks}\n"
        if auto_reply:
            text_content += f"Auto-Reply Message:\n{auto_reply}\n"

        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                # 1. Primary Model Query
                result = await self._query_groq_api(client, effective_key, effective_model, text_content)
                if result is not None:
                    return result

                # 2. Automatic Failover to llama-3.1-8b-instant if primary model hit rate limits
                if "8b" not in effective_model.lower():
                    logger.info("Failover to llama-3.1-8b-instant...")
                    result_8b = await self._query_groq_api(client, effective_key, "llama-3.1-8b-instant", text_content)
                    if result_8b is not None:
                        return result_8b
        except Exception as e:
            logger.warning(f"Groq AI request failed ({e}), falling back to regex extractor.")

        # 3. Final Fallback to local regex extractor
        return ContactExtractor.extract_from_merchant_data(nickname=nickname, remarks=remarks, auto_reply=auto_reply)

    async def test_connection(self, api_key: str, model: str = "llama-3.1-8b-instant") -> tuple[bool, str, int]:
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
                elif resp.status_code == 429:
                    return False, f"⚠️ Лимит запросов (429): {resp.text[:120]}", duration_ms
                else:
                    return False, f"❌ Ошибка {resp.status_code}: {resp.text[:100]}", duration_ms
        except Exception as e:
            return False, f"❌ Ошибка сети: {str(e)[:100]}", 0
