"""Unit tests for HTML extraction, parked/dead detection, and AI JSON parsing."""

from app.classifier import _parse_llm_json
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
