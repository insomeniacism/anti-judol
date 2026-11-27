"""Multi-provider OpenAI-compatible AI classifier for Indonesian illegal websites.

Konsep mirip 9router: beberapa provider free-tier digilir (round-robin), provider
yang kena 429 / error diistirahatkan (cooldown) dan request langsung pindah ke
provider berikutnya, sehingga kuota gratis gabungan bisa bertahan jauh lebih lama.
Kasus judol yang sangat jelas diputus oleh keyword pre-filter tanpa memanggil LLM.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
)

from app.config import Settings
from app.keywords import JUDOL_ACTION_PATTERN, JUDOL_NEGATIVE_PATTERN, JUDOL_STRONG_PATTERNS

logger = logging.getLogger(__name__)

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

# Preset provider free-tier. Key & model dibaca dari Settings (lihat config.py / .env.example).
# rpm = batas request/menit yang dijaga proaktif supaya tidak sering kena 429.
PROVIDER_PRESETS: dict[str, dict[str, Any]] = {
    "groq": {"base_url": "https://api.groq.com/openai/v1", "rpm": 28},
    "cerebras": {"base_url": "https://api.cerebras.ai/v1", "rpm": 25},
    "gemini": {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai/", "rpm": 10},
    "openrouter": {"base_url": "https://openrouter.ai/api/v1", "rpm": 18},
    "ghmodels": {"base_url": "https://models.github.ai/inference", "rpm": 10},
    "mistral": {"base_url": "https://api.mistral.ai/v1", "rpm": 50},
    "zhipu": {"base_url": "https://open.bigmodel.cn/api/paas/v4", "rpm": 30},
    "nvidia": {"base_url": "https://integrate.api.nvidia.com/v1", "rpm": 35},
}


@dataclass(frozen=True, slots=True)
class ClassificationResult:
    """Structured AI classification output."""

    category: Category
    confidence: float
    reason: str
    provider: str = ""


class MalformedResponseError(RuntimeError):
    """Provider returned something that is not a chat completion (HTML, plain text, ...)."""


def _extract_content(resp: Any) -> str:
    try:
        return (resp.choices[0].message.content or "").strip()
    except (AttributeError, IndexError, TypeError) as exc:
        raise MalformedResponseError(
            f"Respons bukan chat completion: {str(resp)[:80]!r}"
        ) from exc


class AllProvidersExhaustedError(RuntimeError):
    """Raised when every configured LLM provider is cooling down or disabled."""


@dataclass(slots=True)
class Provider:
    """One OpenAI-compatible endpoint plus its runtime routing state."""

    name: str
    base_url: str
    api_key: str
    model: str
    rpm: float | None = None
    client: AsyncOpenAI = field(init=False, repr=False)
    available_at: float = 0.0
    json_mode: bool = True
    disabled_reason: str = ""

    def __post_init__(self) -> None:
        self.client = AsyncOpenAI(
            base_url=self.base_url,
            api_key=self.api_key,
            max_retries=0,
            timeout=40.0,
        )

    @property
    def disabled(self) -> bool:
        return bool(self.disabled_reason)


def _parse_llm_json(raw_text: str) -> ClassificationResult:
    """Parse LLM JSON response with fallback extraction for non-strict models."""
    text = raw_text.strip()
    # Reasoning models sometimes emit <think>...</think> before the JSON
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
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


def keyword_prefilter(title: str, meta_description: str, body_snippet: str) -> ClassificationResult | None:
    """Return a JUDOL verdict for blatant gambling pages without spending an LLM call.

    Conservative on purpose: needs >=3 distinct strong gambling terms, at least one
    call-to-action (daftar/deposit/login/bonus), and no news/anti-judol context words.
    Everything else goes to the LLM.
    """
    text = f"{title}\n{meta_description}\n{body_snippet}".lower()
    hits: list[str] = []
    for pattern in JUDOL_STRONG_PATTERNS:
        m = pattern.search(text)
        if m:
            hits.append(" ".join(m.group(0).split()))
    if len(hits) < 3:
        return None
    if not JUDOL_ACTION_PATTERN.search(text):
        return None
    if JUDOL_NEGATIVE_PATTERN.search(text):
        return None
    confidence = min(0.99, 0.80 + 0.04 * len(hits))
    shown = ", ".join(hits[:5])
    return ClassificationResult(
        category="JUDOL",
        confidence=confidence,
        reason=f"Keyword pre-filter: {len(hits)} sinyal judol kuat ({shown})",
        provider="prefilter",
    )


def build_providers(settings: Settings) -> list[Provider]:
    """Build the provider pool from LLM_PROVIDERS JSON, preset keys, and legacy LLM_* vars."""
    providers: list[Provider] = []
    seen: set[tuple[str, str, str]] = set()

    def _add(name: str, base_url: str, api_key: str, model: str, rpm: float | None) -> None:
        if not api_key or not model or not base_url:
            return
        key = (base_url.rstrip("/"), api_key, model)
        if key in seen:
            return
        seen.add(key)
        providers.append(Provider(name=name, base_url=base_url, api_key=api_key, model=model, rpm=rpm))

    # 1. Custom JSON list (most flexible: Cloudflare, SiliconFlow, local Ollama, 9router, dll.)
    if settings.llm_providers.strip():
        try:
            items = json.loads(settings.llm_providers)
        except json.JSONDecodeError as exc:
            raise ValueError(f"LLM_PROVIDERS bukan JSON valid: {exc}") from exc
        if not isinstance(items, list):
            raise ValueError("LLM_PROVIDERS harus berupa JSON array")
        for i, item in enumerate(items):
            _add(
                str(item.get("name") or f"custom{i}"),
                str(item.get("base_url", "")),
                str(item.get("api_key", "")),
                str(item.get("model", "")),
                float(item["rpm"]) if item.get("rpm") else None,
            )

    # 2. Presets: cukup isi <NAMA>_API_KEY di .env / GitHub Secrets
    for name, preset in PROVIDER_PRESETS.items():
        _add(
            name,
            preset["base_url"],
            getattr(settings, f"{name}_api_key"),
            getattr(settings, f"{name}_model"),
            preset["rpm"],
        )

    # 3. Legacy single-provider config (tetap didukung, mis. DeepSeek panitia)
    _add("default", settings.llm_base_url, settings.llm_api_key, settings.llm_model, None)

    return providers


def _retry_after_seconds(exc: APIStatusError) -> float | None:
    try:
        raw = exc.response.headers.get("retry-after")
    except Exception:
        return None
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


class AIClassifier:
    """Round-robin router over multiple free-tier OpenAI-compatible providers."""

    def __init__(self, settings: Settings) -> None:
        self.providers = build_providers(settings)
        if not self.providers:
            raise ValueError(
                "Tidak ada LLM provider yang dikonfigurasi. Isi minimal satu dari: "
                "GROQ_API_KEY, CEREBRAS_API_KEY, GEMINI_API_KEY, OPENROUTER_API_KEY, "
                "GHMODELS_API_KEY, MISTRAL_API_KEY, ZHIPU_API_KEY, NVIDIA_API_KEY, "
                "LLM_PROVIDERS, atau LLM_API_KEY."
            )
        self.prefilter_enabled = settings.keyword_prefilter
        self.default_cooldown = settings.llm_cooldown_seconds
        self.max_wait = settings.llm_max_wait_seconds
        self._rr = 0

    # Backward-compat convenience for single-provider callers/tests
    @property
    def client(self) -> AsyncOpenAI:
        return self.providers[0].client

    @property
    def model(self) -> str:
        return self.providers[0].model

    def _pick(self) -> tuple[Provider | None, float]:
        """Return the next ready provider (round-robin) or the soonest wait time."""
        now = time.monotonic()
        active = [p for p in self.providers if not p.disabled]
        if not active:
            return None, math.inf
        n = len(self.providers)
        for offset in range(n):
            p = self.providers[(self._rr + offset) % n]
            if not p.disabled and p.available_at <= now:
                self._rr = (self._rr + offset + 1) % n
                return p, 0.0
        soonest = min(active, key=lambda p: p.available_at)
        return soonest, soonest.available_at - now

    def _cooldown(self, p: Provider, seconds: float, why: str) -> None:
        seconds = max(1.0, min(seconds, 6 * 3600.0))
        p.available_at = max(p.available_at, time.monotonic() + seconds)
        logger.info("LLM provider %s cooldown %.0fs (%s)", p.name, seconds, why)

    def _disable(self, p: Provider, why: str) -> None:
        p.disabled_reason = why
        logger.warning("LLM provider %s dinonaktifkan untuk run ini: %s", p.name, why)

    async def _call(self, p: Provider, user_prompt: str) -> str:
        """Single request to one provider; falls back to non-JSON mode once on HTTP 400."""
        kwargs: dict[str, Any] = {
            "model": p.model,
            "temperature": 0.1,
            "max_tokens": 300,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
        }
        if p.json_mode:
            try:
                resp = await p.client.chat.completions.create(
                    **kwargs, response_format={"type": "json_object"}
                )
                return _extract_content(resp)
            except APIStatusError as exc:
                if exc.status_code != 400:
                    raise
                # Endpoint menolak response_format -> ingat & ulang tanpa JSON mode
                p.json_mode = False
        resp = await p.client.chat.completions.create(**kwargs)
        return _extract_content(resp)

    async def _route(self, user_prompt: str) -> tuple[str, str]:
        """Try providers until one answers. Returns (content, provider_name)."""
        max_attempts = len(self.providers) * 2 + 2
        last_err: Exception | None = None

        for _ in range(max_attempts):
            p, wait = self._pick()
            if p is None:
                break
            if wait > 0:
                if wait > self.max_wait:
                    break
                await asyncio.sleep(wait)

            if p.rpm:
                # Reservasi slot berikutnya sebelum await, supaya task paralel tidak menabrak limit
                p.available_at = max(p.available_at, time.monotonic()) + 60.0 / p.rpm

            try:
                content = await self._call(p, user_prompt)
            except APIStatusError as exc:
                last_err = exc
                code = exc.status_code
                if code == 429:
                    self._cooldown(p, _retry_after_seconds(exc) or self.default_cooldown, "429 rate limit")
                elif code in (401, 403):
                    self._disable(p, f"HTTP {code} (API key salah / tidak punya akses)")
                elif code == 404:
                    self._disable(p, f"HTTP 404 (model '{p.model}' tidak ditemukan)")
                elif code == 402:
                    self._disable(p, "HTTP 402 (kuota/kredit habis)")
                else:
                    self._cooldown(p, 15.0, f"HTTP {code}")
                continue
            except (APIConnectionError, APITimeoutError) as exc:
                last_err = exc
                self._cooldown(p, 15.0, type(exc).__name__)
                continue
            except MalformedResponseError as exc:
                last_err = exc
                self._cooldown(p, 120.0, "malformed response")
                continue

            if not content:
                self._cooldown(p, 5.0, "empty response")
                continue
            return content, p.name

        status = ", ".join(
            f"{p.name}:{'off' if p.disabled else 'cooldown'}" for p in self.providers
        )
        raise AllProvidersExhaustedError(
            f"Semua LLM provider sedang limit/nonaktif [{status}]; last error: {last_err}"
        )

    @staticmethod
    def build_prompt(
        *, url: str, http_status: int, title: str, meta_description: str, body_snippet: str
    ) -> str:
        return (
            f"URL: {url}\n"
            f"HTTP Status: {http_status}\n"
            f"Title: {title or '(empty)'}\n"
            f"Meta Description: {meta_description or '(empty)'}\n"
            f"Body Snippet:\n{body_snippet or '(empty)'}"
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
        if self.prefilter_enabled:
            quick = keyword_prefilter(title, meta_description, body_snippet)
            if quick is not None:
                return quick

        user_prompt = self.build_prompt(
            url=url,
            http_status=http_status,
            title=title,
            meta_description=meta_description,
            body_snippet=body_snippet,
        )
        content, provider_name = await self._route(user_prompt)
        res = _parse_llm_json(content)
        return ClassificationResult(
            category=res.category,
            confidence=res.confidence,
            reason=res.reason,
            provider=provider_name,
        )

    async def probe(self, p: Provider) -> tuple[bool, str, float]:
        """Send one test classification directly to a provider (used by `llm-check`)."""
        prompt = self.build_prompt(
            url="https://contoh-slot88.example",
            http_status=200,
            title="SLOT88 Situs Slot Gacor Maxwin Hari Ini",
            meta_description="Daftar slot online, deposit pulsa, bonus new member 100%",
            body_snippet="Login / Daftar. RTP live tertinggi. Link alternatif resmi.",
        )
        start = time.monotonic()
        try:
            content = await self._call(p, prompt)
        except APIStatusError as exc:
            detail = f"HTTP {exc.status_code}: {str(exc.message)[:160]}"
            if exc.status_code == 404:
                detail += await self._suggest_models(p)
            return False, detail, time.monotonic() - start
        except Exception as exc:  # noqa: BLE001 - diagnostic command
            return False, f"{type(exc).__name__}: {str(exc)[:160]}", time.monotonic() - start
        res = _parse_llm_json(content)
        info = f"{res.category} ({res.confidence:.2f}) json_mode={'on' if p.json_mode else 'off'}"
        parsed = not res.reason.startswith(("Unparseable", "Invalid JSON"))
        if parsed and res.category != "JUDOL":
            info += " [peringatan: contoh judol tidak terdeteksi, model mungkin terlalu lemah]"
        return parsed, info, time.monotonic() - start

    @staticmethod
    async def _suggest_models(p: Provider) -> str:
        try:
            models = await p.client.models.list()
            ids = [m.id for m in models.data][:15]
        except Exception:
            return ""
        return f"\n    model tersedia: {', '.join(ids)}" if ids else ""
