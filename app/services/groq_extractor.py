import asyncio
import json
import logging
import re
import time
from datetime import datetime, timezone, timedelta
from typing import List, Optional
import httpx

from app.services.contact_extractor import ExtractedContact, ContactExtractor

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an expert contact information extraction system specialized in Binance P2P trader advertisements.
Your goal is to accurately extract ONLY direct communication contacts (Telegram, Phone, WhatsApp, Viber, Instagram, Email) that are explicitly provided by the trader.

CRITICAL EXTRACTION RULES:
1. ONLY extract contacts explicitly provided in the advertisement terms or auto-reply message.
2. DO NOT extract or assume usernames from merchant nicknames, terms of trade, bank names, payment methods (e.g. 'SEPA instant', 'Revolut', 'Wise', 'Bizum'), or instructions (e.g. 'Welcome', 'Leave info', 'Only trade').
3. Standardize Telegram handles with a leading '@' (e.g. '@username').
4. Normalize Phone, WhatsApp, Viber numbers to international format with leading '+' (e.g. '+380971234567', '+573128318338').
5. If no explicit communication contacts are provided, return an empty array.

Output Schema:
You MUST respond ONLY with a JSON object:
{
  "contacts": [
    {"type": "telegram" | "phone" | "whatsapp" | "viber" | "instagram" | "email", "value": "normalized value"}
  ]
}
If no contacts are found, return {"contacts": []}.
"""

# Global circuit breaker state for 70B rate limits
_70B_RATE_LIMITED_UNTIL: Optional[datetime] = None

class GroqContactExtractor:
    """Ultra-fast, multilingual AI contact extractor powered by Groq LPUs with smart circuit breaker, pre-filtering and failover."""

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
        if "@" in lower or "t.me" in lower or "wa.me" in lower or "viber://" in lower or "instagram.com" in lower:
            return True
        keywords = [
            "tg", "тг", "телеграм", "telegram", "телеграмм", "viber", "вайбер",
            "whats", "ватсап", "вацап", "связь", "зв'яз", "звʼяз", "личк", "лс",
            "pm", "phone", "тел", "contact", "контакт"
        ]
        # Match whole words only
        for k in keywords:
            if re.search(rf"\b{k}\b", lower):
                return True
        # Check for sequences of at least 8 digits (phone numbers)
        if re.search(r"\+?\d[\d\s\-\(\)]{7,}\d", text):
            return True
        return False

    async def _query_groq_api(self, client: httpx.AsyncClient, effective_key: str, model_name: str, text_content: str) -> Optional[List[ExtractedContact]]:
        global _70B_RATE_LIMITED_UNTIL
        headers = {
            "Authorization": f"Bearer {effective_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Extract explicit communication contacts from this advertisement text:\n\n{text_content}"},
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
                c_val = str(item.get("value", "")).strip(".,;:!? '\"()[]{}<>")
                if not c_val:
                    continue

                if c_type == "telegram":
                    c_val = c_val.lstrip("@").strip(".,;:!? '\"()[]{}<>")
                    if len(c_val) < 3 or c_val.lower() in ContactExtractor.TG_BLACKLIST:
                        continue
                    if "t.me" not in c_val:
                        c_val = f"@{c_val}"
                elif c_type in ("phone", "whatsapp", "viber"):
                    digits = re.sub(r"\D", "", c_val)
                    if len(digits) < 8 or len(digits) > 16:
                        continue
                    c_val = f"+{digits}"
                elif c_type == "instagram":
                    c_val = c_val.lstrip("@").strip(".,;:!? '\"()[]{}<>")
                    if len(c_val) < 3 or c_val.lower() in ContactExtractor.TG_BLACKLIST:
                        continue
                elif c_type == "email":
                    c_val = c_val.lower().strip()

                key = (c_type, c_val.lower())
                if key not in seen:
                    seen.add(key)
                    contacts.append(ExtractedContact(type=c_type, value=c_val, raw_source="groq_ai"))

            logger.debug(f"Groq AI ({model_name}) extracted {len(contacts)} contacts in {duration_ms}ms.")
            return contacts
        elif resp.status_code == 429:
            if "70b" in model_name.lower():
                _70B_RATE_LIMITED_UNTIL = datetime.now(timezone.utc) + timedelta(minutes=30)
                logger.warning(f"Groq 70B rate limit hit (429). Circuit breaker active for 30 minutes.")
            return None
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
        """Extract contacts strictly from advertisement text & auto-reply."""
        global _70B_RATE_LIMITED_UNTIL
        effective_key = api_key or self.api_key
        effective_model = model or self.model or "llama-3.1-8b-instant"

        # 1. Fast heuristic pre-filtering on advertisement text & auto reply only
        combined_text = f"{remarks} {auto_reply}".strip()
        if not self.has_potential_contacts(combined_text):
            return []

        if not effective_key:
            return ContactExtractor.extract_from_merchant_data(remarks=remarks, auto_reply=auto_reply)

        # 2. Check 70B circuit breaker
        target_model = effective_model
        now = datetime.now(timezone.utc)
        if "70b" in target_model.lower() and _70B_RATE_LIMITED_UNTIL and now < _70B_RATE_LIMITED_UNTIL:
            target_model = "llama-3.1-8b-instant"

        text_content = ""
        if remarks:
            text_content += f"Terms / Remarks:\n{remarks}\n"
        if auto_reply:
            text_content += f"Auto-Reply Message:\n{auto_reply}\n"

        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                result = await self._query_groq_api(client, effective_key, target_model, text_content)
                if result is not None:
                    return result

                # Failover to 8B if primary 70B failed
                if "8b" not in target_model.lower():
                    result_8b = await self._query_groq_api(client, effective_key, "llama-3.1-8b-instant", text_content)
                    if result_8b is not None:
                        return result_8b
        except Exception as e:
            logger.warning(f"Groq AI request failed ({e}), falling back to regex extractor.")

        # 3. Final Fallback to local regex extractor
        return ContactExtractor.extract_from_merchant_data(remarks=remarks, auto_reply=auto_reply)

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
