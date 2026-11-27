"""Unit tests for HTML extraction, parked/dead detection, and AI JSON parsing."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from openai import BadRequestError, RateLimitError

from app.classifier import AIClassifier, _parse_llm_json
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


async def test_classifier_retries_on_rate_limit_with_json_mode() -> None:
    settings = Settings(llm_api_key="test-key", llm_base_url="https://example.com/v1", llm_model="test")
    clf = AIClassifier(settings)

    req = httpx.Request("POST", "https://example.com/v1/chat/completions")
    resp_429 = httpx.Response(429, request=req)
    rate_err = RateLimitError("rate limited", response=resp_429, body=None)

    ok_msg = MagicMock()
    ok_msg.choices = [MagicMock(message=MagicMock(content='{"category":"JUDOL","confidence":0.9,"reason":"slot"}'))]

    mock_create = AsyncMock(side_effect=[rate_err, ok_msg])
    clf.client.chat.completions.create = mock_create  # type: ignore[method-assign]

    with patch("app.classifier.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        res = await clf.classify(
            url="https://slot.example",
            http_status=200,
            title="Slot",
            meta_description="",
            body_snippet="Slot gacor",
        )

    assert res.category == "JUDOL"
    assert mock_create.await_count == 2
    assert mock_sleep.await_count == 1
    assert mock_create.call_args_list[1].kwargs["response_format"] == {"type": "json_object"}


async def test_classifier_falls_back_without_json_mode_on_bad_request() -> None:
    settings = Settings(llm_api_key="test-key", llm_base_url="https://example.com/v1", llm_model="test")
    clf = AIClassifier(settings)

    req = httpx.Request("POST", "https://example.com/v1/chat/completions")
    resp_400 = httpx.Response(400, request=req)
    bad_err = BadRequestError("json_object unsupported", response=resp_400, body=None)

    ok_msg = MagicMock()
    ok_msg.choices = [MagicMock(message=MagicMock(content='{"category":"SAFE","confidence":0.8,"reason":"ok"}'))]

    mock_create = AsyncMock(side_effect=[bad_err, ok_msg])
    clf.client.chat.completions.create = mock_create  # type: ignore[method-assign]

    res = await clf.classify(
        url="https://safe.example",
        http_status=200,
        title="Home",
        meta_description="",
        body_snippet="Company profile",
    )

    assert res.category == "SAFE"
    assert mock_create.await_count == 2
    assert "response_format" in mock_create.call_args_list[0].kwargs
    assert "response_format" not in mock_create.call_args_list[1].kwargs
