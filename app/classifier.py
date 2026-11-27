"""Modular OpenAI-compatible AI classifier for Indonesian illegal websites."""

from __future__ import annotations

import asyncio
import json
import random
import re
from dataclasses import dataclass
from typing import Any, Literal

from openai import (
    APIConnectionError,
    APIStatusError,
    AsyncOpenAI,
    BadRequestError,
    RateLimitError,
)

from app.config import Settings

Category = Literal["JUDOL", "PINJOL_ILEGAL", "SCAM_PHISHING", "SAFE"]
VALID_CATEGORIES: set[str] = {"JUDOL", "PINJOL_ILEGAL", "SCAM_PHISHING", "SAFE"}

SYSTEM_PROMPT = """You are an OSINT security analyst classifying Indonesian websites for a legal anti-illegal-web hackathon.
Analyze the provided URL, HTTP status, page title, meta description, and body text snippet.

Classify the website into EXACTLY ONE of these categories:
- JUDOL: Indonesian online gambling, slot gacor, togel, casino, sportsbook, sbobet, sabung ayam, poker, or RTP slot promotion sites.
- PINJOL_ILEGAL: Unlicensed/illegal Indonesian online loans, predatory payday loans, fake OJK-registered loan apps, or suspicious fast-cash loan sites.
- SCAM_PHISHING: Phishing pages, fake bank/e-wallet logins (BRI, BCA, Mandiri, DANA, OVO, GoPay), fake DANA Kaget, fake government aid (bansos/prakerja), or investment/crypto scams.
- SAFE: Legitimate business, blog, news, education, government, e-commerce, unrelated foreign site, or insufficient evidence.

Respond ONLY with a valid JSON object using this exact schema:
{
  "category": "JUDOL" | "PINJOL_ILEGAL" | "SCAM_PHISHING" | "SAFE",
  "confidence": 0.0 to 1.0,
  "reason": "Short 1-sentence explanation in Indonesian or English"
}"""

_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


@dataclass(frozen=True, slots=True)
class ClassificationResult:
    """Structured AI classification output."""

    category: Category
    confidence: float
    reason: str


def _parse_llm_json(raw_text: str) -> ClassificationResult:
    """Parse LLM JSON response with fallback extraction for non-strict models."""
    text = raw_text.strip()
    match = _JSON_BLOCK_RE.search(text)
    if not match:
        return ClassificationResult(
            category="SAFE",
            confidence=0.0,
            reason=f"Unparseable AI response: {text[:120]}",
        )

    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return ClassificationResult(
            category="SAFE",
            confidence=0.0,
            reason="Invalid JSON returned by AI",
        )

    raw_cat = str(data.get("category", "SAFE")).upper().strip()
    category: Category = raw_cat if raw_cat in VALID_CATEGORIES else "SAFE"  # type: ignore[assignment]

    try:
        conf = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        conf = 0.0
    conf = max(0.0, min(1.0, conf))

    reason = str(data.get("reason", "")).strip()[:300] or "No reason provided"
    return ClassificationResult(category=category, confidence=conf, reason=reason)


class AIClassifier:
    """OpenAI-compatible async classifier (supports Groq, Gemini, DeepSeek, etc.)."""

    def __init__(self, settings: Settings) -> None:
        if not settings.llm_api_key:
            raise ValueError("LLM_API_KEY is not configured. Set it in .env or environment.")
        self.model = settings.llm_model
        self.client = AsyncOpenAI(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            max_retries=0,
        )

    async def _create_completion(
        self,
        *,
        user_prompt: str,
        use_json_mode: bool,
        max_attempts: int = 4,
    ) -> Any:
        """Call chat completions with exponential backoff on 429/5xx transient errors."""
        kwargs: dict[str, Any] = {
            "model": self.model,
            "temperature": 0.1,
            "max_tokens": 220,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
        }
        if use_json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        for attempt in range(max_attempts):
            try:
                return await self.client.chat.completions.create(**kwargs)
            except (RateLimitError, APIConnectionError) as exc:
                if attempt == max_attempts - 1:
                    raise
                delay = (2.0 * (2**attempt)) + random.uniform(0.0, 1.0)
                await asyncio.sleep(delay)
            except APIStatusError as exc:
                if exc.status_code in (429, 500, 502, 503, 504) and attempt < max_attempts - 1:
                    delay = (2.0 * (2**attempt)) + random.uniform(0.0, 1.0)
                    await asyncio.sleep(delay)
                    continue
                raise

    async def classify(
        self,
        *,
        url: str,
        http_status: int,
        title: str,
        meta_description: str,
        body_snippet: str,
    ) -> ClassificationResult:
        """Classify a single live website snapshot."""
        user_prompt = (
            f"URL: {url}\n"
            f"HTTP Status: {http_status}\n"
            f"Title: {title or '(empty)'}\n"
            f"Meta Description: {meta_description or '(empty)'}\n"
            f"Body Snippet:\n{body_snippet or '(empty)'}"
        )

        try:
            response = await self._create_completion(
                user_prompt=user_prompt,
                use_json_mode=True,
            )
        except BadRequestError:
            # Fallback only when endpoint rejects response_format, never on 429/5xx
            response = await self._create_completion(
                user_prompt=user_prompt,
                use_json_mode=False,
            )

        content = (response.choices[0].message.content or "").strip()
        return _parse_llm_json(content)
