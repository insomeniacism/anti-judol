"""Async HTTP verifier, parked-domain detector, and AI Judge pipeline."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx
from bs4 import BeautifulSoup

from app.classifier import AIClassifier
from app.config import Settings
from app.db import Database

logger = logging.getLogger(__name__)

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
}

PARKED_SIGNATURES = (
    "domain is for sale",
    "buy this domain",
    "this domain is parked",
    "domain parking",
    "sedoparking",
    "hugedomains.com",
    "afternic.com",
    "dan.com",
    "bodis.com",
    "godaddy.com/domain",
    "namecheap.com",
    "account suspended",
    "this account has been suspended",
    "website is currently unavailable",
    "default web site page",
    "apache2 ubuntu default page",
    "welcome to nginx!",
)

DEAD_TITLE_SIGNATURES = (
    "404 not found",
    "403 forbidden",
    "502 bad gateway",
    "503 service unavailable",
    "error 1016",
    "error 1001",
    "site can't be reached",
)

_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class PageSnapshot:
    """Extracted metadata and text from an HTTP response."""

    http_status: int
    final_url: str
    title: str
    meta_description: str
    body_snippet: str


def extract_page_content(html: str, max_snippet_chars: int = 1500) -> tuple[str, str, str]:
    """Extract (title, meta_description, clean_body_snippet) from HTML."""
    soup = BeautifulSoup(html, "html.parser")

    title = ""
    if soup.title and soup.title.string:
        title = _WHITESPACE_RE.sub(" ", soup.title.string).strip()[:300]

    meta_desc = ""
    for attr_name in ("description", "og:description"):
        tag = soup.find("meta", attrs={"name": attr_name}) or soup.find(
            "meta", attrs={"property": attr_name}
        )
        if tag and tag.get("content"):
            meta_desc = _WHITESPACE_RE.sub(" ", str(tag["content"])).strip()[:400]
            break

    for noisy in soup(["script", "style", "noscript", "svg", "iframe"]):
        noisy.decompose()

    raw_text = soup.get_text(separator=" ")
    clean_body = _WHITESPACE_RE.sub(" ", raw_text).strip()[:max_snippet_chars]
    return title, meta_desc, clean_body


def detect_parked_or_dead(
    http_status: int,
    final_url: str,
    title: str,
    meta_description: str,
    body_snippet: str,
) -> tuple[str | None, str | None]:
    """Return ('PARKED'|'DEAD', reason) if page is parked/dead, else (None, None)."""
    combined = f"{final_url} {title} {meta_description} {body_snippet}".lower()
    title_lower = title.lower()

    for sig in PARKED_SIGNATURES:
        if sig in combined:
            return "PARKED", f"Matched parked/suspended signature: '{sig}'"

    if http_status in (404, 410, 500, 502, 503, 504):
        return "DEAD", f"HTTP error status {http_status}"

    for sig in DEAD_TITLE_SIGNATURES:
        if sig in title_lower:
            return "DEAD", f"Matched dead title signature: '{sig}'"

    if not title and len(body_snippet) < 40:
        return "DEAD", "Empty response / insufficient content"

    return None, None


async def _fetch_snapshot(
    client: httpx.AsyncClient,
    url: str,
) -> PageSnapshot | None:
    """Fetch URL over HTTP/HTTPS, falling back to http:// if https:// fails TLS."""
    candidates_to_try = [url]
    if url.startswith("https://"):
        candidates_to_try.append("http://" + url[len("https://") :])

    for target in candidates_to_try:
        try:
            resp = await client.get(target)
            content_type = resp.headers.get("content-type", "").lower()
            if "html" not in content_type and "text" not in content_type and content_type:
                return PageSnapshot(
                    http_status=resp.status_code,
                    final_url=str(resp.url),
                    title="",
                    meta_description="",
                    body_snippet="",
                )
            title, meta_desc, snippet = extract_page_content(resp.text)
            return PageSnapshot(
                http_status=resp.status_code,
                final_url=str(resp.url),
                title=title,
                meta_description=meta_desc,
                body_snippet=snippet,
            )
        except httpx.HTTPError:
            continue
    return None


async def _process_one(
    row: dict[str, Any],
    *,
    client: httpx.AsyncClient,
    classifier: AIClassifier,
    db: Database,
    settings: Settings,
    semaphore: asyncio.Semaphore,
    counts: dict[str, int],
) -> None:
    async with semaphore:
        candidate_id = int(row["id"])
        url = str(row["original_url"])

        snapshot = await _fetch_snapshot(client, url)
        if snapshot is None:
            await db.update_verification_result(
                candidate_id,
                status="DEAD",
                ai_reason="Unreachable / connection timeout",
            )
            counts["DEAD"] = counts.get("DEAD", 0) + 1
            return

        parked_or_dead, reason = detect_parked_or_dead(
            snapshot.http_status,
            snapshot.final_url,
            snapshot.title,
            snapshot.meta_description,
            snapshot.body_snippet,
        )
        if parked_or_dead is not None:
            await db.update_verification_result(
                candidate_id,
                status=parked_or_dead,
                http_status=snapshot.http_status,
                final_url=snapshot.final_url[:500],
                page_title=snapshot.title,
                meta_description=snapshot.meta_description,
                is_parked=(parked_or_dead == "PARKED"),
                ai_reason=reason,
            )
            counts[parked_or_dead] = counts.get(parked_or_dead, 0) + 1
            return

        try:
            ai_res = await classifier.classify(
                url=url,
                http_status=snapshot.http_status,
                title=snapshot.title,
                meta_description=snapshot.meta_description,
                body_snippet=snapshot.body_snippet,
            )
        except Exception as exc:
            logger.warning("AI classification error for %s: %s", url, exc)
            # Release back to PENDING on transient AI error
            await db.update_verification_result(
                candidate_id,
                status="PENDING",
                http_status=snapshot.http_status,
                final_url=snapshot.final_url[:500],
                page_title=snapshot.title,
                meta_description=snapshot.meta_description,
                ai_reason=f"Transient AI error: {str(exc)[:150]}",
            )
            counts["ERROR"] = counts.get("ERROR", 0) + 1
            return

        if ai_res.category == "SAFE":
            final_status = "SAFE"
        elif ai_res.confidence >= settings.ai_confidence_threshold:
            final_status = "VERIFIED_ILLEGAL"
        else:
            final_status = "LOW_CONFIDENCE"

        await db.update_verification_result(
            candidate_id,
            status=final_status,
            http_status=snapshot.http_status,
            final_url=snapshot.final_url[:500],
            page_title=snapshot.title,
            meta_description=snapshot.meta_description,
            is_parked=False,
            category=ai_res.category,
            confidence=ai_res.confidence,
            ai_reason=ai_res.reason,
        )
        counts[final_status] = counts.get(final_status, 0) + 1


async def verify_batch(
    db: Database,
    settings: Settings,
    batch_size: int | None = None,
) -> dict[str, int]:
    """Claim a batch of PENDING candidates and verify them concurrently."""
    size = batch_size or settings.verify_batch_size
    claimed = await db.claim_pending(
        worker_id=settings.worker_id,
        batch_size=size,
        lock_timeout_minutes=settings.lock_timeout_minutes,
    )
    if not claimed:
        return {"claimed": 0}

    classifier = AIClassifier(settings)
    semaphore = asyncio.Semaphore(settings.verify_concurrency)
    counts: dict[str, int] = {"claimed": len(claimed)}

    async with httpx.AsyncClient(
        headers=BROWSER_HEADERS,
        timeout=settings.http_timeout_seconds,
        follow_redirects=True,
        verify=False,  # Illegal sites often use self-signed/expired certs; we still verify content
    ) as client:
        tasks = [
            _process_one(
                row,
                client=client,
                classifier=classifier,
                db=db,
                settings=settings,
                semaphore=semaphore,
                counts=counts,
            )
            for row in claimed
        ]
        await asyncio.gather(*tasks)

    return counts
