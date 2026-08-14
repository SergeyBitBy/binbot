import os
import pytest
from app.services.groq_extractor import GroqContactExtractor

GROQ_KEY = os.getenv("GROQ_API_KEY", "")

@pytest.mark.asyncio
async def test_groq_extractor_live():
    if not GROQ_KEY:
        pytest.skip("GROQ_API_KEY not set in environment")
    extractor = GroqContactExtractor(api_key=GROQ_KEY)
    success, msg, latency = await extractor.test_connection(extractor.api_key)
    assert success is True
    assert latency > 0

@pytest.mark.asyncio
async def test_groq_extractor_extract_contacts():
    if not GROQ_KEY:
        pytest.skip("GROQ_API_KEY not set in environment")
    extractor = GroqContactExtractor(api_key=GROQ_KEY)
    contacts = await extractor.extract_from_merchant_data(
        nickname="Serg_Exch01",
        remarks="По всем вопросам — в личные сообщения. SergExch01",
    )
    assert len(contacts) == 1
    assert contacts[0].type == "telegram"
    assert contacts[0].value == "@SergExch01"
