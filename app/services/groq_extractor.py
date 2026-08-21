import asyncio
import hashlib
import json
import logging
import random
import re
import time
from datetime import datetime, timezone, timedelta
from typing import List, Optional
import httpx

from app.services.contact_extractor import ExtractedContact

logger = logging.getLogger(__name__)

# Global extraction cache to prevent redundant Groq API calls for unchanged ads
_EXTRACTION_CACHE: dict[str, List[ExtractedContact]] = {}

DEFAULT_SYSTEM_PROMPT = """You are an expert contact information extraction system specialized in Binance P2P trader advertisements.
Your goal is to accurately extract ONLY direct communication contacts (Telegram, Phone, WhatsApp, Viber, Instagram, Email) that are explicitly provided by the trader.

CRITICAL EXTRACTION RULES:
1. ONLY extract contacts explicitly provided in the advertisement terms or auto-reply message.
2. DO NOT extract or assume usernames from merchant nicknames, terms of trade, bank names, payment methods (e.g. 'SEPA instant', 'Revolut', 'Wise', 'Bizum'), or instructions (e.g. 'Welcome', 'Leave info', 'Only trade').
3. DO NOT return placeholder values (such as 'unknown', 'none', 'n/a', 'null', 'tg', 'tlg', 'whatsapp', 'social') if a username/handle is not explicitly provided.
4. DO NOT extract service names or abbreviations (e.g. 'Tlg', 'Telegram', 'Whatsapp', 'Viber') as usernames. If a trader only mentions a platform name without giving a handle, IGNORE it.
5. Standardize Telegram handles with a leading '@' (e.g. '@username').
6. Normalize Phone, WhatsApp, Viber numbers to international format with leading '+' (e.g. '+380971234567', '+573128318338').
7. If no explicit communication contacts are provided, return an empty array {"contacts": []}.

Output Schema:
You MUST respond ONLY with a JSON object:
{
  "contacts": [
    {"type": "telegram" | "phone" | "whatsapp" | "viber" | "instagram" | "email", "value": "normalized value"}
  ]
}
If no contacts are found, return {"contacts": []}.
"""

SYSTEM_PROMPT = DEFAULT_SYSTEM_PROMPT

# Global circuit breaker state and concurrency control
_GROQ_SEMAPHORE = asyncio.Semaphore(2)

# Blacklist of common keywords to prevent false positive matches in AI output
AI_BLACKLIST = {
    "binance", "support", "admin", "p2p", "help", "bot", "online", "fast",
    "trade", "usdt", "uah", "rub", "usd", "eur", "mono", "privat", "pumb",
    "bank", "card", "pay", "order", "buyer", "seller", "crypto", "change",
    "privatbank", "monobank", "a-bank", "abank", "vlasnyirakhunok",
    "instant", "sepa", "revolut", "wise", "garant", "escrow", "ant", "ant.",
    "instructions", "instrucciones", "welcome", "leave", "thank",
    "thanks", "merch", "merchant", "trading", "exchange", "account",
    "partner", "compro", "vendo", "transfer", "transfers", "payment",
    "payments", "trusted", "only", "solo", "communication", "every",
    "professional", "please", "ready", "service", "terms", "condition",
    "conditions", "notice", "autopilot", "system", "automated",
    "unknown", "none", "null", "n/a", "na", "tlg", "whatsup", "whatsapp",
    "telegram", "viber", "social", "midea", "media", "contact", "contacts",
    "username", "handle", "profile", "channel", "chat", "group", "link",
    "dm", "pm", "direct", "number", "phone",
}

class GroqContactExtractor:
    """Pure AI contact extractor powered by Groq LPUs with zero regex fallback."""

    def __init__(self, api_key: str = "", model: str = "qwen/qwen3.6-27b", custom_prompt: str = ""):
        self.api_key = api_key
        self.model = model or "qwen/qwen3.6-27b"
        self.custom_prompt = custom_prompt or ""
        self.endpoint = "https://api.groq.com/openai/v1/chat/completions"

    @staticmethod
    def has_potential_contacts(text: str) -> bool:
        """Fast heuristic check to avoid unnecessary LLM calls when text has no contact patterns."""
        if not text or len(text.strip()) < 3:
            return False
        lower = text.lower()
        if "@" in lower or "t.me" in lower or "wa.me" in lower or "viber://" in lower or "instagram.com" in lower:
            return True
        # Check contact-related emojis
        contact_emojis = ["📲", "📱", "📞", "☎️", "💬", "✉️", "📧", "👉", "👇", "✍️"]
        for emoji in contact_emojis:
            if emoji in text:
                return True
        keywords = [
            "tg", "тг", "телеграм", "telegram", "телеграмм", "viber", "вайбер",
            "whats", "whatsapp", "ватсап", "вацап", "связь", "зв'яз", "звʼяз", "личк", "лс",
            "pm", "dm", "phone", "тел", "contact", "контакт", "contacts", "write",
            "пишите", "напишите", "escribe", "escribir", "contactar", "teléfono",
            "telefono", "wpp", "social", "comunicar", "mensaje", "inbox"
        ]
        # Match whole words only
        for k in keywords:
            if re.search(rf"\b{k}\b", lower):
                return True
        # Check for sequences of at least 8 digits (phone numbers)
        if re.search(r"\+?\d[\d\s\-\(\)]{7,}\d", text):
            return True
        return False

    async def _query_groq_api(self, client: httpx.AsyncClient, effective_key: str, model_name: str, text_content: str, system_prompt: str = "") -> Optional[List[ExtractedContact]]:
        effective_prompt = system_prompt or self.custom_prompt or DEFAULT_SYSTEM_PROMPT
        headers = {
            "Authorization": f"Bearer {effective_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": effective_prompt},
                {"role": "user", "content": f"Extract explicit communication contacts from this advertisement text:\n\n{text_content}"},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.0,
        }

        max_retries = 3
        for attempt in range(1, max_retries + 1):
            t0 = time.perf_counter()
            resp = await client.post(self.endpoint, headers=headers, json=payload)
            duration_ms = round((time.perf_counter() - t0) * 1000)

            if resp.status_code == 200:
                data = resp.json()
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "{}")
                parsed = json.loads(content)
                raw_contacts = parsed.get("contacts", [])

                contacts: List[ExtractedContact] = []
                for item in raw_contacts:
                    c_type = str(item.get("type", "")).strip().lower()
                    c_val = str(item.get("value", "")).strip()

                    if not c_type or not c_val:
                        continue

                    if c_type == "telegram":
                        if not c_val.startswith("@") and "t.me" not in c_val:
                            c_val = f"@{c_val}"
                        clean_name = c_val.lstrip("@").lower()
                        if clean_name in AI_BLACKLIST or len(clean_name) < 4:
                            continue
                    elif c_type in ("phone", "whatsapp", "viber"):
                        digits = re.sub(r"\D", "", c_val)
                        if len(digits) < 8 or len(digits) > 16:
                            continue
                        if not c_val.startswith("+"):
                            c_val = f"+{digits}"

                    contacts.append(ExtractedContact(type=c_type, value=c_val, raw_source="groq_ai"))

                logger.debug(f"Groq AI ({model_name}) extracted {len(contacts)} contacts in {duration_ms}ms.")
                return contacts
            elif resp.status_code == 429:
                retry_after = float(resp.headers.get("retry-after", attempt * 1.5))
                logger.warning(f"Groq rate limit hit (429) on {model_name}. Attempt {attempt}/{max_retries}, retrying in {retry_after}s.")
                if attempt == max_retries:
                    return None
                await asyncio.sleep(retry_after + random.uniform(0.1, 0.4))
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
        custom_prompt: Optional[str] = None,
    ) -> List[ExtractedContact]:
        """Extract contacts strictly via Groq AI without ANY regex fallback."""
        effective_key = api_key or self.api_key
        effective_model = model or self.model or "qwen/qwen3.6-27b"
        effective_prompt = custom_prompt or self.custom_prompt or DEFAULT_SYSTEM_PROMPT

        if not effective_key:
            return []

        # 1. Fast heuristic pre-filtering on advertisement text & auto reply only
        combined_text = f"{remarks} {auto_reply}".strip()
        if not self.has_potential_contacts(combined_text):
            return []

        target_model = effective_model

        # Check cache by hashing model, prompt, and input text
        cache_key = hashlib.md5(f"{target_model}:{effective_prompt}:{combined_text}".encode("utf-8")).hexdigest()
        if cache_key in _EXTRACTION_CACHE:
            return _EXTRACTION_CACHE[cache_key]

        text_content = ""
        if remarks:
            text_content += f"Terms / Remarks:\n{remarks}\n"
        if auto_reply:
            text_content += f"Auto-Reply Message:\n{auto_reply}\n"

        try:
            async with _GROQ_SEMAPHORE:
                async with httpx.AsyncClient(timeout=4.0) as client:
                    result = await self._query_groq_api(client, effective_key, target_model, text_content, system_prompt=effective_prompt)
                    if result is not None:
                        if len(_EXTRACTION_CACHE) > 5000:
                            _EXTRACTION_CACHE.clear()
                        _EXTRACTION_CACHE[cache_key] = result
                        return result

                    # Failover to secondary models if primary returned error or rate limit
                    failovers = ["groq/compound-mini", "openai/gpt-oss-120b"]
                    for f_model in failovers:
                        if f_model != target_model:
                            result_fallback = await self._query_groq_api(client, effective_key, f_model, text_content, system_prompt=effective_prompt)
                            if result_fallback is not None:
                                if len(_EXTRACTION_CACHE) > 5000:
                                    _EXTRACTION_CACHE.clear()
                                _EXTRACTION_CACHE[cache_key] = result_fallback
                                return result_fallback
        except Exception as e:
            logger.warning(f"Groq AI request failed: {e}")

        # When Groq AI is enabled, NEVER fallback to regex
        return []

    async def test_connection(self, api_key: str, model: str = "qwen/qwen3.6-27b", custom_prompt: str = "") -> tuple[bool, str, int]:
        """Test Groq API key connection and return (success, message, latency_ms)."""
        effective_prompt = custom_prompt or self.custom_prompt or DEFAULT_SYSTEM_PROMPT
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": effective_prompt},
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
