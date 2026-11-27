"""Unit tests for HTML extraction, parked/dead detection, and AI JSON parsing."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from openai import APIStatusError, BadRequestError, RateLimitError

from app.classifier import (
    AIClassifier,
    AllProvidersExhaustedError,
    _parse_llm_json,
    build_providers,
    keyword_prefilter,
)
from app.config import Settings
from app.verifier import detect_parked_or_dead, extract_page_content


def test_extract_page_content_strips_scripts_and_styles() -> None:
    html = """
    <html>
      <head>
        <title>  Slot Gacor   Maxwin 88  </title>
        <meta name="description" content="Situs judi slot terpercaya" />
        <style>body { color: red; }</style>
      </head>
      <body>
        <script>console.log("ignore me");</script>
        <h1>Daftar Slot Gacor Hari Ini</h1>
        <p>Deposit pulsa tanpa potongan.</p>
      </body>
    </html>
    """
    title, meta, snippet = extract_page_content(html)
    assert title == "Slot Gacor Maxwin 88"
    assert meta == "Situs judi slot terpercaya"
    assert "Daftar Slot Gacor Hari Ini" in snippet
    assert "ignore me" not in snippet
    assert "color: red" not in snippet


def test_detect_parked_domain() -> None:
    status, reason = detect_parked_or_dead(
        200,
        "https://example.com",
        "Example.com - This domain is for sale",
        "",
        "Buy this domain today via Sedo.",
    )
    assert status == "PARKED"
    assert reason is not None


def test_detect_dead_page() -> None:
    status, _ = detect_parked_or_dead(
        404,
        "https://example.com",
        "404 Not Found",
        "",
        "Not found",
    )
    assert status == "DEAD"


def test_detect_live_page_returns_none() -> None:
    status, reason = detect_parked_or_dead(
        200,
        "https://slot88.example",
        "SLOT88: Situs Slot Gacor Maxwin",
        "RTP tertinggi hari ini",
        "Daftar dan mainkan game slot pragmatic play terpercaya di Indonesia.",
    )
    assert status is None
    assert reason is None


def test_parse_llm_json_handles_markdown_fences() -> None:
    raw = """```json
    {
      "category": "JUDOL",
      "confidence": 0.95,
      "reason": "Situs mempromosikan permainan slot gacor dan deposit judi online."
    }
    ```"""
    res = _parse_llm_json(raw)
    assert res.category == "JUDOL"
    assert res.confidence == 0.95
    assert "slot gacor" in res.reason


def test_parse_llm_json_clamps_and_sanitizes_invalid_category() -> None:
    raw = '{"category": "UNKNOWN_CAT", "confidence": 1.8, "reason": "test"}'
    res = _parse_llm_json(raw)
    assert res.category == "SAFE"
    assert res.confidence == 1.0


def _ok(content: str) -> MagicMock:
    msg = MagicMock()
    msg.choices = [MagicMock(message=MagicMock(content=content))]
    return msg


def _status_err(code: int, headers: dict[str, str] | None = None) -> APIStatusError:
    req = httpx.Request("POST", "https://example.com/v1/chat/completions")
    resp = httpx.Response(code, request=req, headers=headers or {})
    if code == 429:
        return RateLimitError("rate limited", response=resp, body=None)
    if code == 400:
        return BadRequestError("bad request", response=resp, body=None)
    return APIStatusError(f"http {code}", response=resp, body=None)


def _settings(**kw: object) -> Settings:
    base: dict[str, object] = {
        "llm_api_key": "",
        "keyword_prefilter": False,
        "_env_file": None,
    }
    base.update(kw)
    return Settings(**base)  # type: ignore[arg-type]


SNAP = {
    "url": "https://x.example",
    "http_status": 200,
    "title": "Home",
    "meta_description": "",
    "body_snippet": "Company profile",
}


def test_build_providers_from_presets_json_and_legacy() -> None:
    s = _settings(
        groq_api_key="g",
        gemini_api_key="m",
        llm_api_key="legacy",
        llm_base_url="https://api.deepseek.com/v1",
        llm_model="deepseek-chat",
        llm_providers='[{"name":"cf","base_url":"https://cf.example/v1","api_key":"c","model":"llama"}]',
    )
    names = [p.name for p in build_providers(s)]
    assert names == ["cf", "groq", "gemini", "default"]


def test_classifier_requires_at_least_one_provider() -> None:
    with pytest.raises(ValueError):
        AIClassifier(_settings())


async def test_router_fails_over_to_next_provider_on_429() -> None:
    clf = AIClassifier(_settings(groq_api_key="g", cerebras_api_key="c"))
    groq, cerebras = clf.providers
    groq.client.chat.completions.create = AsyncMock(side_effect=_status_err(429, {"retry-after": "120"}))  # type: ignore[method-assign]
    cerebras.client.chat.completions.create = AsyncMock(  # type: ignore[method-assign]
        return_value=_ok('{"category":"JUDOL","confidence":0.9,"reason":"slot"}')
    )

    res = await clf.classify(**SNAP)

    assert res.category == "JUDOL"
    assert res.provider == "cerebras"
    assert groq.available_at > cerebras.available_at - 60  # groq is cooling down ~120s
    # Next call must skip cooling-down groq entirely
    groq.client.chat.completions.create.reset_mock()
    cerebras.available_at = 0.0
    res2 = await clf.classify(**SNAP)
    assert res2.provider == "cerebras"
    assert groq.client.chat.completions.create.await_count == 0


async def test_router_disables_provider_on_bad_key() -> None:
    clf = AIClassifier(_settings(groq_api_key="bad", openrouter_api_key="ok"))
    groq, orr = clf.providers
    groq.client.chat.completions.create = AsyncMock(side_effect=_status_err(401))  # type: ignore[method-assign]
    orr.client.chat.completions.create = AsyncMock(  # type: ignore[method-assign]
        return_value=_ok('{"category":"SAFE","confidence":0.8,"reason":"ok"}')
    )
    res = await clf.classify(**SNAP)
    assert res.provider == "openrouter"
    assert groq.disabled


async def test_single_provider_waits_briefly_then_retries_on_429() -> None:
    clf = AIClassifier(_settings(llm_api_key="k", llm_base_url="https://example.com/v1", llm_model="t"))
    create = AsyncMock(side_effect=[_status_err(429), _ok('{"category":"JUDOL","confidence":0.9,"reason":"slot"}')])
    clf.client.chat.completions.create = create  # type: ignore[method-assign]

    with patch("app.classifier.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        res = await clf.classify(**SNAP)

    assert res.category == "JUDOL"
    assert create.await_count == 2
    assert mock_sleep.await_count == 1
    assert create.call_args_list[1].kwargs["response_format"] == {"type": "json_object"}


async def test_router_raises_when_all_providers_exhausted() -> None:
    clf = AIClassifier(_settings(groq_api_key="g", llm_max_wait_seconds=5))
    clf.client.chat.completions.create = AsyncMock(side_effect=_status_err(429, {"retry-after": "600"}))  # type: ignore[method-assign]
    with pytest.raises(AllProvidersExhaustedError):
        await clf.classify(**SNAP)


async def test_classifier_falls_back_without_json_mode_on_bad_request() -> None:
    clf = AIClassifier(_settings(llm_api_key="k", llm_base_url="https://example.com/v1", llm_model="t"))
    create = AsyncMock(side_effect=[_status_err(400), _ok('{"category":"SAFE","confidence":0.8,"reason":"ok"}')])
    clf.client.chat.completions.create = create  # type: ignore[method-assign]

    res = await clf.classify(**SNAP)

    assert res.category == "SAFE"
    assert create.await_count == 2
    assert "response_format" in create.call_args_list[0].kwargs
    assert "response_format" not in create.call_args_list[1].kwargs
    assert clf.providers[0].json_mode is False


def test_parse_llm_json_strips_think_block() -> None:
    res = _parse_llm_json('<think>{"category":"SAFE"} hmm</think>{"category":"JUDOL","confidence":0.9,"reason":"x"}')
    assert res.category == "JUDOL"


def test_prefilter_catches_blatant_judol_page() -> None:
    res = keyword_prefilter(
        "SLOT88: Situs Slot Gacor Maxwin Hari Ini",
        "Daftar slot online deposit pulsa, bonus new member",
        "Login / Daftar. RTP live tertinggi. Link alternatif resmi.",
    )
    assert res is not None
    assert res.category == "JUDOL"
    assert res.confidence >= 0.9
    assert res.provider == "prefilter"


def test_prefilter_skips_news_about_judol() -> None:
    res = keyword_prefilter(
        "Polisi Tangkap Bandar Judi Online Slot Gacor",
        "Berita pemberantasan judi online oleh Komdigi",
        "Tersangka mempromosikan situs slot dengan bonus new member dan link alternatif.",
    )
    assert res is None


def test_prefilter_skips_weak_signal() -> None:
    assert keyword_prefilter("Toko Bunga", "Promo bunga segar", "Daftar harga bunga hari ini") is None


async def test_prefilter_bypasses_llm() -> None:
    clf = AIClassifier(_settings(groq_api_key="g", keyword_prefilter=True))
    create = AsyncMock()
    clf.client.chat.completions.create = create  # type: ignore[method-assign]
    res = await clf.classify(
        url="https://slot.example",
        http_status=200,
        title="Slot Gacor Maxwin",
        meta_description="Bonus new member, deposit pulsa",
        body_snippet="Daftar sekarang, RTP live",
    )
    assert res.provider == "prefilter"
    assert create.await_count == 0


async def test_router_survives_malformed_provider_response() -> None:
    clf = AIClassifier(_settings(groq_api_key="g", cerebras_api_key="c"))
    groq, cerebras = clf.providers
    groq.client.chat.completions.create = AsyncMock(return_value="OK")  # type: ignore[method-assign]
    cerebras.client.chat.completions.create = AsyncMock(  # type: ignore[method-assign]
        return_value=_ok('{"category":"SAFE","confidence":0.9,"reason":"ok"}')
    )
    res = await clf.classify(**SNAP)
    assert res.provider == "cerebras"
