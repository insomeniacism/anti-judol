"""Modular OpenAI-compatible AI classifier for Indonesian illegal websites."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Literal

from openai import AsyncOpenAI

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
        )

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
            response = await self.client.chat.completions.create(
                model=self.model,
                temperature=0.1,
                max_tokens=220,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
            )
        except Exception:
            # Fallback without response_format for OpenAI-compatible endpoints that reject it
            response = await self.client.chat.completions.create(
                model=self.model,
                temperature=0.1,
                max_tokens=220,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
            )

        content = (response.choices[0].message.content or "").strip()
        return _parse_llm_json(content)
